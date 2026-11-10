"""Persistent event covers use real multipart uploads, files and database rows."""

from io import BytesIO
from pathlib import Path
import re
import sqlite3

from PIL import Image
import pytest
from werkzeug.datastructures import FileStorage

from app import create_app
from app.extensions import db
from app.models import Event, User
from app.services.cover_service import InvalidCover, normalize_cover
from config import TestConfig


@pytest.fixture
def cover_app(tmp_path):
    class CoverConfig(TestConfig):
        COVER_UPLOAD_DIR = str(tmp_path / "covers")
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'covers.db'}"
        SQLALCHEMY_ENGINE_OPTIONS = {}

    app = create_app(CoverConfig)
    app.cover_test_config = CoverConfig
    with app.app_context():
        for index, role in enumerate(("organizer", "organizer", "student", "admin"), start=1):
            db.session.add(User(id=index, name=f"Cover User {index}", email=f"cover{index}@scu.edu",
                                google_sub=f"cover-{index}", role=role))
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def signed_in(app, user_id=1):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def image_file(fmt="PNG", size=(40, 24)):
    stream = BytesIO()
    Image.new("RGB", size, "red").save(stream, format=fmt)
    stream.seek(0)
    return stream


def event_form(client, event_id=None, **changes):
    action = f"/organizer/events/{event_id}/edit" if event_id else "/organizer/events/new"
    response = client.get(action)
    assert response.status_code == 200
    token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
    fields = dict(csrf_token=token, title="Campus Cover Event", description="A real campus gathering.",
                  location="Learning Commons", category="Tech", start_time="2050-11-01T17:00",
                  end_time="2050-11-01T19:00", price="0", capacity="25")
    fields.update(changes)
    return action, fields


def browser_multipart(fields):
    """Keep the cover among text fields, as the browser's event form does."""
    boundary = "----WebKitFormBoundaryBroncosHubCoverTest"
    parts = []
    for name, value in fields.items():
        header = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        if isinstance(value, tuple):
            content, filename = value
            header += f'; filename="{filename}"\r\nContent-Type: image/png'
        else:
            content = value.encode()
        parts.append((header + "\r\n\r\n").encode() + content + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def create_cover(app, client):
    action, fields = event_form(client, cover_image=(image_file(), "poster.png"))
    assert client.post(action, data=fields).status_code == 302
    with app.app_context():
        event = db.session.scalar(db.select(Event))
        return event.id, event.cover_image_path


@pytest.mark.parametrize("fmt,extension", [("JPEG", "jpg"), ("PNG", "png"), ("WEBP", "webp")])
def test_create_decodes_and_normalizes_cover_and_displays_it(cover_app, fmt, extension):
    client = signed_in(cover_app)
    original = image_file(fmt)
    action, fields = event_form(client, cover_image=(original, f"poster.{extension}"))
    assert client.post(action, data=fields).status_code == 302
    with cover_app.app_context():
        event = db.session.scalar(db.select(Event))
        name, event_id, url = event.cover_image_path, event.id, event.event_view()["cover_url"]
    assert re.fullmatch(r"[a-f0-9]{32}\.webp", name)
    saved = Path(cover_app.config["COVER_UPLOAD_DIR"]) / name
    with Image.open(saved) as image:
        image.load()
        assert image.format == "WEBP"
        assert image.size == (40, 24)
        assert not image.getexif()
    public = cover_app.test_client()
    assert url.encode() in public.get(f"/events/{event_id}").data
    response = public.get(url)
    assert response.status_code == 200 and response.mimetype == "image/webp"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.data == saved.read_bytes()


@pytest.mark.parametrize("editing", [False, True], ids=["create", "replace"])
def test_browser_upload_accepts_campus_photo_larger_than_parser_chunk(cover_app, editing):
    client = signed_in(cover_app)
    event_id, previous = create_cover(cover_app, client) if editing else (None, None)
    action, fields = event_form(client, event_id)
    photo = (Path(__file__).resolve().parents[1] / "app/static/images/scu-campus.png").read_bytes()
    assert 64 * 1024 < len(photo) < 5 * 1024 * 1024
    # Chrome sends controls in document order, including text fields after the
    # image. A real photo spans parser chunks; tiny solid-color images do not.
    fields = {
        **{name: fields[name] for name in ("csrf_token", "title", "description", "category")},
        "cover_image": (photo, "scu-campus.png"),
        "cover_token": "",
        **{name: fields[name] for name in ("location", "start_time", "end_time", "price", "capacity")},
    }
    body, content_type = browser_multipart(fields)
    assert client.post(action, data=body, content_type=content_type).status_code == 302
    with cover_app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Event)) == 1
        event = db.session.scalar(db.select(Event))
        event_id, filename, url = event.id, event.cover_image_path, event.event_view()["cover_url"]
    directory = Path(cover_app.config["COVER_UPLOAD_DIR"])
    with Image.open(directory / filename) as saved:
        assert saved.format == "WEBP" and saved.size == (759, 545)
    if previous:
        assert filename != previous and not (directory / previous).exists()
    public = cover_app.test_client()
    assert url.encode() in public.get(f"/events/{event_id}").data
    assert public.get(url).status_code == 200


@pytest.mark.parametrize("oversized", ["text", "parts"])
def test_multipart_text_and_part_limits_still_reject_oversized_forms(cover_app, oversized):
    client = signed_in(cover_app)
    action, fields = event_form(client)
    if oversized == "text":
        fields["description"] = "x" * (300 * 1024)
    else:
        fields.update({f"extra_{index}": "x" for index in range(50)})
    body, content_type = browser_multipart(fields)
    assert client.post(action, data=body, content_type=content_type).status_code == 413
    with cover_app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Event)) == 0
    assert not list(Path(cover_app.config["COVER_UPLOAD_DIR"]).glob("*"))


@pytest.mark.parametrize("kind", ["svg", "corrupt", "spoof", "path", "too_large", "pixels"])
def test_rejects_bad_covers_without_saving_event_or_files(cover_app, kind):
    client = signed_in(cover_app)
    upload, filename = image_file(), "poster.png"
    if kind == "svg":
        upload, filename = BytesIO(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'), "poster.svg"
    elif kind == "corrupt":
        upload = BytesIO(b"not a PNG")
    elif kind == "spoof":
        filename = "poster.jpg"
    elif kind == "path":
        filename = "../../poster.png"
    elif kind == "too_large":
        upload = BytesIO(b"x" * (5 * 1024 * 1024 + 1))
    elif kind == "pixels":
        cover_app.config["COVER_MAX_PIXELS"] = 100
    action, fields = event_form(client, cover_image=(upload, filename))
    response = client.post(action, data=fields)
    assert response.status_code == 422
    assert b'id="cover_image-error"' in response.data
    with cover_app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Event)) == 0
    assert not list(Path(cover_app.config["COVER_UPLOAD_DIR"]).glob("*"))


def test_windows_style_upload_path_is_rejected_before_storage(cover_app):
    # Multipart parsers may strip Windows separators first. The service also
    # rejects unnormalized names when called directly.
    with cover_app.app_context(), pytest.raises(InvalidCover):
        normalize_cover(FileStorage(image_file(), filename="..\\poster.png", content_type="image/png"))


def test_request_limit_returns_friendly_error(cover_app):
    client = signed_in(cover_app)
    action, fields = event_form(client, cover_image=(BytesIO(b"x" * (6 * 1024 * 1024)), "poster.png"))
    response = client.post(action, data=fields)
    assert response.status_code == 413
    assert b"Upload too large" in response.data


def test_invalid_event_does_not_leave_uploaded_file(cover_app):
    client = signed_in(cover_app)
    action, fields = event_form(client, title="", cover_image=(image_file(), "poster.png"))
    response = client.post(action, data=fields)
    assert response.status_code == 422
    assert b"Choose your cover again" in response.data
    assert not list(Path(cover_app.config["COVER_UPLOAD_DIR"]).glob("*"))


def test_cover_replace_remove_and_normal_edit(cover_app):
    client = signed_in(cover_app)
    event_id, first = create_cover(cover_app, client)
    directory = Path(cover_app.config["COVER_UPLOAD_DIR"])
    action, fields = event_form(client, event_id, title="Updated event")
    assert client.post(action, data=fields).status_code == 302
    assert (directory / first).is_file()
    action, fields = event_form(client, event_id, cover_image=(image_file("JPEG"), "new.jpg"))
    assert client.post(action, data=fields).status_code == 302
    with cover_app.app_context():
        second = db.session.get(Event, event_id).cover_image_path
    assert second != first and (directory / second).is_file()
    assert not (directory / first).exists()
    action, fields = event_form(client, event_id, remove_cover="1")
    assert client.post(action, data=fields).status_code == 302
    with cover_app.app_context():
        event = db.session.get(Event, event_id)
        assert event.cover_image_path is None
        assert event.event_view()["cover_url"] == ""
    assert not (directory / second).exists()
    assert client.get(f"/media/events/{event_id}/cover").status_code == 404


def test_invalid_replacement_preserves_saved_event_and_cover(cover_app):
    client = signed_in(cover_app)
    event_id, filename = create_cover(cover_app, client)
    action, fields = event_form(client, event_id, title="Should not save",
                                cover_image=(BytesIO(b"broken image"), "new.png"))
    assert client.post(action, data=fields).status_code == 422
    with cover_app.app_context():
        event = db.session.get(Event, event_id)
        assert event.title == "Campus Cover Event" and event.cover_image_path == filename
    assert (Path(cover_app.config["COVER_UPLOAD_DIR"]) / filename).is_file()


def test_remove_cover_intent_stays_visible_after_another_field_error(cover_app):
    client = signed_in(cover_app)
    event_id, filename = create_cover(cover_app, client)
    action, fields = event_form(client, event_id, title="", remove_cover="1")
    response = client.post(action, data=fields)
    assert response.status_code == 422
    assert b'data-current-cover=""' in response.data
    assert f"/media/events/{event_id}/cover".encode() not in response.data
    assert b'name="remove_cover" value="1"' in response.data
    with cover_app.app_context():
        assert db.session.get(Event, event_id).cover_image_path == filename
    assert (Path(cover_app.config["COVER_UPLOAD_DIR"]) / filename).is_file()
    fields["title"] = "Corrected title"
    assert client.post(action, data=fields).status_code == 302
    with cover_app.app_context():
        assert db.session.get(Event, event_id).cover_image_path is None
    assert not (Path(cover_app.config["COVER_UPLOAD_DIR"]) / filename).exists()


def test_storage_failure_preserves_old_cover_and_shows_retry_message(cover_app, monkeypatch):
    client = signed_in(cover_app)
    event_id, filename = create_cover(cover_app, client)
    action, fields = event_form(client, event_id, title="Should not save",
                                cover_image=(image_file(), "new.png"))
    def unavailable(data):
        raise OSError("Storage unavailable")
    monkeypatch.setattr("app.services.cover_service.store_cover", unavailable)
    response = client.post(action, data=fields)
    assert response.status_code == 503 and b"We couldn&#39;t save your image" in response.data
    with cover_app.app_context():
        event = db.session.get(Event, event_id)
        assert event.title == "Campus Cover Event" and event.cover_image_path == filename
    assert (Path(cover_app.config["COVER_UPLOAD_DIR"]) / filename).is_file()


def test_permissions_and_csrf_prevent_cover_mutation(cover_app):
    # A student can upload a cover, but other hosts and admins still cannot edit it.
    owner = signed_in(cover_app, 3)
    event_id, first = create_cover(cover_app, owner)
    action, fields = event_form(owner, event_id, remove_cover="1")
    for user_id in (1, 2, 4):
        assert signed_in(cover_app, user_id).post(action, data=fields).status_code == 404
    fields.pop("csrf_token")
    assert owner.post(action, data=fields).status_code == 400
    with cover_app.app_context():
        event = db.session.get(Event, event_id)
        assert event.organizer_id == 3 and event.cover_image_path == first
        assert db.session.get(User, 3).role == "student"
    assert (Path(cover_app.config["COVER_UPLOAD_DIR"]) / first).is_file()


def test_removed_cover_is_private_and_no_longer_editable(cover_app):
    owner = signed_in(cover_app)
    event_id, _ = create_cover(cover_app, owner)
    with cover_app.app_context():
        db.session.get(Event, event_id).status = "removed"
        db.session.commit()
    url = f"/media/events/{event_id}/cover"
    for client in (cover_app.test_client(), signed_in(cover_app, 3), signed_in(cover_app, 2)):
        response = client.get(url)
        assert response.status_code == 404
        assert response.cache_control.no_store
    for client in (owner, signed_in(cover_app, 4)):
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-store"
    assert owner.get(f"/organizer/events/{event_id}/edit").status_code == 409


def test_bad_stored_path_cannot_serve_local_files(cover_app, tmp_path):
    client = signed_in(cover_app)
    event_id, _ = create_cover(cover_app, client)
    with cover_app.app_context():
        db.session.get(Event, event_id).cover_image_path = "../../config.py"
        db.session.commit()
    assert client.get(f"/media/events/{event_id}/cover").status_code == 404


def test_database_failure_keeps_old_cover_and_removes_new_file(cover_app, monkeypatch):
    client = signed_in(cover_app)
    event_id, old_name = create_cover(cover_app, client)
    action, fields = event_form(client, event_id, cover_image=(image_file(), "new.png"))
    def fail_commit():
        raise RuntimeError("Simulated database failure")
    monkeypatch.setattr(db.session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="Simulated database failure"):
        client.post(action, data=fields)
    with cover_app.app_context():
        assert db.session.get(Event, event_id).cover_image_path == old_name
    assert [path.name for path in Path(cover_app.config["COVER_UPLOAD_DIR"]).iterdir()] == [old_name]


def test_cover_survives_new_app_instance(cover_app):
    event_id, filename = create_cover(cover_app, signed_in(cover_app))
    restarted = create_app(cover_app.cover_test_config)
    with restarted.app_context():
        assert db.session.get(Event, event_id).cover_image_path == filename
    assert restarted.test_client().get(f"/media/events/{event_id}/cover").status_code == 200
    with restarted.app_context():
        db.session.remove()
        db.engine.dispose()


def test_relative_upload_directory_uses_instance_path_for_writes_reads_and_removal(cover_app, tmp_path, monkeypatch):
    cover_app.instance_path = str(tmp_path / "app-instance")
    cover_app.config["COVER_UPLOAD_DIR"] = "relative-covers"
    working = tmp_path / "working"
    working.mkdir()
    monkeypatch.chdir(working)
    client = signed_in(cover_app)
    event_id, filename = create_cover(cover_app, client)
    saved = tmp_path / "app-instance" / "relative-covers" / filename
    assert saved.is_file()
    assert not (working / "relative-covers").exists()
    # A process started from another directory must resolve exactly the same file.
    monkeypatch.chdir(tmp_path)
    response = client.get(f"/media/events/{event_id}/cover")
    assert response.status_code == 200 and response.data == saved.read_bytes()
    action, fields = event_form(client, event_id, remove_cover="1")
    assert client.post(action, data=fields).status_code == 302
    assert not saved.exists()


def test_existing_sqlite_upgrade_is_additive_and_preserves_removal_history(tmp_path):
    database = tmp_path / "old.db"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT, status TEXT);
            CREATE TABLE reports (id INTEGER PRIMARY KEY, event_id INTEGER, status TEXT, details TEXT);
            INSERT INTO events VALUES (1, 'Removed old event', 'cancelled');
            INSERT INTO events VALUES (2, 'Ordinary cancellation', 'cancelled');
            INSERT INTO events VALUES (3, 'Still published', 'published');
            INSERT INTO reports VALUES (7, 1, 'removed', 'Keep this review history');
        """)
    class OldConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
        SQLALCHEMY_ENGINE_OPTIONS = {}
    for _ in range(2):
        app = create_app(OldConfig)
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id, title, status, cover_image_path FROM events ORDER BY id").fetchall() == [
            (1, "Removed old event", "removed", None),
            (2, "Ordinary cancellation", "cancelled", None),
            (3, "Still published", "published", None),
        ]
        assert connection.execute("SELECT * FROM reports").fetchall() == [
            (7, 1, "removed", "Keep this review history")
        ]
