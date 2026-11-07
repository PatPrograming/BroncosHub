"""Private object storage keeps images across app instances without public URLs."""
from io import BytesIO
from pathlib import Path
import re
import sqlite3

from PIL import Image
import pytest
import requests

from app import create_app
from app.extensions import db
from app.models import Event, User
from app.services.cover_service import commit_event_with_cover, read_cover
from app.services.remote_cover_storage import CoverStorageError, RemoteCoverStorage, validate_cover_storage_config
from config import TestConfig


ORIGIN = "https://abcdefghijklmnopqrst.supabase.co"
SECRET = "sb_secret_only-a-server-test-secret"
NAME = "a" * 32 + ".webp"


def webp_bytes():
    stream = BytesIO()
    Image.new("RGB", (30, 20), "red").save(stream, format="WEBP")
    return stream.getvalue()


class Reply:
    def __init__(self, status=200, data=b"", headers=None):
        self.status_code, self.data = status, data
        self.headers = headers if headers is not None else {"Content-Type": "image/webp"}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        for index in range(0, len(self.data), chunk_size):
            yield self.data[index:index + chunk_size]


@pytest.fixture
def remote_app(tmp_path, monkeypatch):
    class RemoteConfig(TestConfig):
        COVER_STORAGE_BACKEND = "supabase"
        SUPABASE_URL = ORIGIN
        SUPABASE_SECRET_KEY = SECRET
        SUPABASE_STORAGE_BUCKET = "event-covers"
        COVER_UPLOAD_DIR = str(tmp_path / "must-not-store-here")
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'remote.db'}"
        SQLALCHEMY_ENGINE_OPTIONS = {}

    app = create_app(RemoteConfig)
    app.remote_test_config = RemoteConfig
    app.remote_objects = {}
    app.remote_calls = []
    app.remote_failure = None
    app.after_upload = None

    def request(method, url, **kwargs):
        # The same connection would fail if an edit held SQLite's write lock
        # during the remote upload or cleanup.
        with sqlite3.connect(tmp_path / 'remote.db', timeout=0.1) as probe:
            probe.execute("BEGIN IMMEDIATE")
            probe.rollback()
        assert url.startswith(ORIGIN + "/storage/v1/object/event-covers")
        assert kwargs["headers"]["apikey"] == SECRET
        assert "Authorization" not in kwargs["headers"]
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        assert kwargs["timeout"] == (3.05, 15)
        app.remote_calls.append((method, url))
        if method == app.remote_failure:
            raise requests.ConnectionError("provider detail that must not reach a user")
        if method == "POST":
            name = url.rsplit("/", 1)[1]
            assert kwargs["headers"]["x-upsert"] == "false"
            assert kwargs["headers"]["Content-Type"] == "image/webp"
            app.remote_objects[name] = kwargs["data"]
            if app.after_upload:
                app.after_upload()
            return Reply(201)
        if method == "GET":
            name = url.rsplit("/", 1)[1]
            return Reply(200, app.remote_objects[name]) if name in app.remote_objects else Reply(404)
        if method == "DELETE":
            assert url == ORIGIN + "/storage/v1/object/event-covers"
            for name in kwargs["json"]["prefixes"]:
                app.remote_objects.pop(name, None)
            return Reply(200)
        raise AssertionError(method)

    monkeypatch.setattr("app.services.remote_cover_storage.requests.request", request)
    with app.app_context():
        db.session.add_all([
            User(id=1, name="Host", email="host@scu.edu", google_sub="host", role="student"),
            User(id=2, name="Other", email="other@scu.edu", google_sub="other", role="student"),
            User(id=3, name="Admin", email="admin@scu.edu", google_sub="admin", role="admin"),
        ])
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def login(app, user_id=1):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def form(client, event_id=None, **changes):
    url = f"/organizer/events/{event_id}/edit" if event_id else "/organizer/events/new"
    page = client.get(url)
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    data = dict(csrf_token=token, title="Persistent cover", description="Testing image persistence.",
                location="Learning Commons", category="Tech", start_time="2050-11-01T17:00",
                end_time="2050-11-01T19:00", price="0", capacity="25",
                cover_image=(BytesIO(webp_bytes()), "poster.webp"))
    data.update(changes)
    return url, data


def publish(app):
    client = login(app)
    url, data = form(client)
    assert client.post(url, data=data).status_code == 302
    with app.app_context():
        event = db.session.scalar(db.select(Event))
        return client, event.id, event.cover_image_path


def test_remote_cover_survives_new_app_with_empty_local_storage(remote_app):
    client, event_id, name = publish(remote_app)
    assert not Path(remote_app.config["COVER_UPLOAD_DIR"]).exists()
    restarted = create_app(remote_app.remote_test_config)
    try:
        response = restarted.test_client().get(f"/media/events/{event_id}/cover")
        assert response.status_code == 200
        assert response.data == remote_app.remote_objects[name]
        assert response.headers["Cache-Control"] == "private, no-store"
        html = restarted.test_client().get(f"/events/{event_id}").data
        assert f"/media/events/{event_id}/cover".encode() in html
        assert ORIGIN.encode() not in html and SECRET.encode() not in html
    finally:
        with restarted.app_context():
            db.session.remove()
            db.engine.dispose()


def test_replacement_upload_and_cleanup_do_not_hold_event_write_lock(remote_app):
    client, event_id, old_name = publish(remote_app)
    url, data = form(client, event_id)
    assert client.post(url, data=data).status_code == 302
    with remote_app.app_context():
        new_name = db.session.get(Event, event_id).cover_image_path
    assert new_name != old_name
    assert set(remote_app.remote_objects) == {new_name}


def test_rejected_edit_discards_unused_remote_upload(remote_app):
    client, event_id, old_name = publish(remote_app)
    url, data = form(client, event_id, title="")
    assert client.post(url, data=data).status_code == 422
    assert set(remote_app.remote_objects) == {old_name}


def test_event_removed_during_upload_stays_removed_and_discards_upload(remote_app):
    client, event_id, old_name = publish(remote_app)
    url, data = form(client, event_id)
    def remove_after_upload():
        # Represents a second request finishing while Storage uploads the image.
        db.session.execute(db.update(Event).where(Event.id == event_id).values(status="removed"))
        db.session.commit()
    remote_app.after_upload = remove_after_upload
    assert client.post(url, data=data).status_code == 409
    with remote_app.app_context():
        event = db.session.get(Event, event_id)
        assert event.status == "removed" and event.cover_image_path == old_name
    assert set(remote_app.remote_objects) == {old_name}


def test_failed_upload_preserves_old_event_and_does_not_fall_back_to_disk(remote_app):
    client, event_id, old_name = publish(remote_app)
    remote_app.remote_failure = "POST"
    url, data = form(client, event_id, title="Must not save")
    response = client.post(url, data=data)
    assert response.status_code == 503
    assert b"provider detail" not in response.data
    with remote_app.app_context():
        event = db.session.get(Event, event_id)
        assert event.title == "Persistent cover" and event.cover_image_path == old_name
    assert set(remote_app.remote_objects) == {old_name}
    assert not Path(remote_app.config["COVER_UPLOAD_DIR"]).exists()


def test_removed_cover_checks_viewer_before_storage(remote_app):
    client, event_id, name = publish(remote_app)
    with remote_app.app_context():
        db.session.get(Event, event_id).status = "removed"
        db.session.commit()
    before = len(remote_app.remote_calls)
    for viewer in (remote_app.test_client(), login(remote_app, 2)):
        response = viewer.get(f"/media/events/{event_id}/cover")
        assert response.status_code == 404 and response.cache_control.no_store
    assert len(remote_app.remote_calls) == before
    for viewer in (client, login(remote_app, 3)):
        assert viewer.get(f"/media/events/{event_id}/cover").data == remote_app.remote_objects[name]


def test_unavailable_remote_image_has_no_secret_or_local_fallback(remote_app):
    client, event_id, name = publish(remote_app)
    directory = Path(remote_app.config["COVER_UPLOAD_DIR"])
    directory.mkdir()
    (directory / name).write_bytes(webp_bytes())
    remote_app.remote_failure = "GET"
    response = client.get(f"/media/events/{event_id}/cover")
    assert response.status_code == 503
    assert SECRET.encode() not in response.data
    assert b"provider detail" not in response.data


def test_database_failure_cleans_new_remote_object(remote_app, monkeypatch):
    client, event_id, old_name = publish(remote_app)
    url, data = form(client, event_id)
    def failure():
        raise RuntimeError("Database rejected save")
    monkeypatch.setattr(db.session, "commit", failure)
    with pytest.raises(RuntimeError, match="Database rejected save"):
        client.post(url, data=data)
    assert set(remote_app.remote_objects) == {old_name}
    with remote_app.app_context():
        assert db.session.get(Event, event_id).cover_image_path == old_name


def test_cleanup_failure_does_not_mask_original_database_error(remote_app, monkeypatch):
    client, event_id, old_name = publish(remote_app)
    remote_app.remote_failure = "DELETE"
    url, data = form(client, event_id)
    def failure():
        raise RuntimeError("Database rejected save")
    monkeypatch.setattr(db.session, "commit", failure)
    with pytest.raises(RuntimeError, match="Database rejected save"):
        client.post(url, data=data)
    with remote_app.app_context():
        assert db.session.get(Event, event_id).cover_image_path == old_name


@pytest.mark.parametrize("name", ["../../secret", "https://other.example/file", "../" + NAME, "", None])
def test_invalid_stored_key_never_reaches_provider(remote_app, name):
    before = len(remote_app.remote_calls)
    with remote_app.app_context():
        assert read_cover(name) is None
    assert len(remote_app.remote_calls) == before


@pytest.mark.parametrize("status,headers,data", [
    (302, {"Location": "https://other.example"}, b""),
    (200, {"Content-Type": "text/html"}, b"<html>bad</html>"),
    (200, {"Content-Type": "image/webp", "Content-Length": "99999999"}, b""),
    (200, {"Content-Type": "image/webp", "Content-Length": "broken"}, b""),
    (200, {"Content-Type": "image/webp"}, b"not an image"),
    (200, {"Content-Type": "image/webp"}, b"RIFF0000WEBP" + b"x" * 100),
])
def test_bad_provider_response_is_bounded_and_closed(remote_app, monkeypatch, status, headers, data):
    response = Reply(status, data, headers)
    monkeypatch.setattr("app.services.remote_cover_storage.requests.request", lambda *a, **kw: response)
    with remote_app.app_context():
        config = dict(remote_app.config, COVER_MAX_BYTES=50)
        with pytest.raises(CoverStorageError):
            RemoteCoverStorage(config).read(NAME)
    assert response.closed


@pytest.mark.parametrize("setting,value", [
    ("COVER_STORAGE_BACKEND", "s3"),
    ("SUPABASE_URL", "http://abcdefghijklmnopqrst.supabase.co"),
    ("SUPABASE_URL", "https://abcdefghijklmnopqrst.supabase.co.evil.example"),
    ("SUPABASE_URL", "https://user@abcdefghijklmnopqrst.supabase.co"),
    ("SUPABASE_URL", ORIGIN + "/other"),
    ("SUPABASE_SECRET_KEY", "sb_publishable_just-public-test-key"),
    ("SUPABASE_SECRET_KEY", SECRET + "\n"),
    ("SUPABASE_STORAGE_BUCKET", "../../other-bucket"),
])
def test_invalid_storage_config_fails_before_network(remote_app, setting, value):
    config = dict(remote_app.config)
    config[setting] = value
    with pytest.raises(RuntimeError):
        validate_cover_storage_config(config)
