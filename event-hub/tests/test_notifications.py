"""Committed event changes queue notices; provider retries cannot duplicate them.

Google sessions and the email provider are supplied locally. No emails are sent.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
import json
import sqlite3
from threading import Event as Signal, Lock

import pytest
import requests

from app import create_app
from app.extensions import db
from app.models import Event, Registration, Report, User
from app.models.notification import Notification, utcnow
from app.services import notification_service as notices
from app.services.event_write_service import event_write
from app.services.moderation_service import resolve_report
from config import TestConfig


def configure(app):
    app.config.update(
        NOTIFICATION_EMAIL_PROVIDER="resend", RESEND_API_KEY="re_test_only",
        NOTIFICATION_FROM_EMAIL="events@example.com", SUPPORT_EMAIL="ecan@scu.edu",
        PUBLIC_BASE_URL="https://broncoshub.example.com",
    )


def seed(app):
    with app.app_context():
        users = [User(name=name, email=f"notice-{name}@scu.edu", google_sub=f"notice-{name}", role=role)
                 for name, role in (("host", "student"), ("attendee", "student"), ("admin", "admin"))]
        event = Event(organizer=users[0], title="Private event title", description="Private description",
                      location="Original room", category="Tech", price_cents=0, capacity=10,
                      start_time=datetime(2050, 1, 1, 12), end_time=datetime(2050, 1, 1, 14),
                      status="published")
        registration = Registration(event=event, student=users[1], status="confirmed")
        report = Report(event=event, reporter=users[1], reason="misleading", details="Private report details")
        db.session.add_all([*users, event, registration, report])
        db.session.commit()
        return dict(host=users[0].id, student=users[1].id, admin=users[2].id,
                    event=event.id, registration=registration.id, report=report.id)


@pytest.fixture
def notice_case(app):
    configure(app)
    return seed(app)


def queue(app, case, kind="changed"):
    with app.app_context():
        with event_write(case["event"]) as event:
            notices.queue_event_notices(event, kind)
            db.session.commit()
        return db.session.scalar(db.select(Notification.id).order_by(Notification.created_at.desc()))


def browser(app, user_id):
    result = app.test_client()
    with result.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["moderation_csrf"] = {"user": str(user_id), "token": "notice-token"}
    return result


def test_queue_uses_callers_transaction_and_only_active_registrations(app, notice_case):
    with app.app_context():
        event = db.session.get(Event, notice_case["event"])
        notices.queue_event_notices(event, "changed")
        db.session.flush()
        assert db.session.scalar(db.select(db.func.count()).select_from(Notification)) == 1
        db.session.rollback()
        assert db.session.scalar(db.select(db.func.count()).select_from(Notification)) == 0
        registration = db.session.get(Registration, notice_case["registration"])
        registration.status = "cancelled"
        db.session.commit()
        notices.queue_event_notices(event, "cancelled")
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count()).select_from(Notification)) == 0


def test_disabled_delivery_does_not_send_and_expires_then_prunes_old_notices(app, notice_case, monkeypatch):
    identifier = queue(app, notice_case)
    app.config["NOTIFICATION_EMAIL_PROVIDER"] = "disabled"
    monkeypatch.setattr(notices, "send_email", lambda *args: pytest.fail("Email is disabled"))
    with app.app_context():
        assert notices.deliver_pending()["disabled"] is True
        message = db.session.get(Notification, identifier)
        assert message.status == "pending"
        message.created_at = utcnow() - timedelta(days=2)
        message.delivery_payload = {"text": "Old content"}
        db.session.commit()
        notices.deliver_pending()
        db.session.refresh(message)
        assert (message.status, message.failure_code, message.delivery_payload, message.details) == (
            "failed", "expired", None, {})
        message.completed_at = utcnow() - timedelta(days=31)
        db.session.commit()
        notices.deliver_pending()
        db.session.expire_all()
        assert db.session.get(Notification, identifier) is None


@pytest.mark.parametrize("enabled,expected_failures", [(True, 1), (False, 0)])
def test_expiry_is_reported_once_only_when_email_enabled(app, notice_case, monkeypatch, enabled, expected_failures):
    identifier = queue(app, notice_case)
    app.config["NOTIFICATION_EMAIL_PROVIDER"] = "resend" if enabled else "disabled"
    monkeypatch.setattr(notices, "send_email", lambda *args: pytest.fail("Expired notices must not send"))
    with app.app_context():
        message = db.session.get(Notification, identifier)
        message.created_at = utcnow() - timedelta(days=2)
        db.session.commit()
        assert notices.deliver_pending()["failed"] == expected_failures
        assert notices.deliver_pending()["failed"] == 0
        assert db.session.get(Notification, identifier).failure_code == "expired"


def test_success_sends_committed_payload_without_db_transaction_and_clears_content(app, notice_case, monkeypatch):
    identifier = queue(app, notice_case)
    sent = []

    def send(payload, key):
        assert not db.session().in_transaction()
        sent.append((deepcopy(payload), key))
        return "provider-success"

    monkeypatch.setattr(notices, "send_email", send)
    with app.app_context():
        assert notices.deliver_pending()["sent"] == 1
        assert notices.deliver_pending()["sent"] == 0
        message = db.session.get(Notification, identifier)
        assert message.status == "sent" and message.provider_id == "provider-success"
        assert message.attempts == 1 and message.delivery_payload is None and message.details == {}
    assert len(sent) == 1 and sent[0][1] == identifier
    assert sent[0][0]["to"] == ["notice-attendee@scu.edu"]
    assert "/my-registrations/" in sent[0][0]["text"]
    assert "Private description" not in sent[0][0]["text"]


def test_timeout_retry_reuses_exact_envelope_and_key_despite_title_or_sender_changes(app, notice_case, monkeypatch):
    identifier = queue(app, notice_case)
    calls = []

    def send(payload, key):
        calls.append((deepcopy(payload), key))
        if len(calls) == 1:
            raise notices.DeliveryError("provider_unavailable")
        return "same-provider-email"

    monkeypatch.setattr(notices, "send_email", send)
    with app.app_context():
        notices.deliver_pending()
        message = db.session.get(Notification, identifier)
        assert message.status == "pending" and message.delivery_payload == calls[0][0]
        db.session.get(Event, notice_case["event"]).title = "Renamed event"
        message.available_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        app.config.update(NOTIFICATION_FROM_EMAIL="new-sender@example.com", SUPPORT_EMAIL="help@example.com")
        assert notices.deliver_pending()["sent"] == 1
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.parametrize("change", ["location", "time", "cancel", "remove", "registration", "email"])
def test_obsolete_updates_are_skipped_before_resending(app, notice_case, monkeypatch, change):
    identifier = queue(app, notice_case)
    monkeypatch.setattr(notices, "send_email", lambda *args: (_ for _ in ()).throw(
        notices.DeliveryError("provider_unavailable")))
    with app.app_context():
        notices.deliver_pending()
        event = db.session.get(Event, notice_case["event"])
        if change == "location":
            event.location = "Different room"
        elif change == "time":
            event.start_time += timedelta(hours=1)
        elif change in {"cancel", "remove"}:
            event.status = "cancelled" if change == "cancel" else "removed"
        elif change == "registration":
            db.session.get(Registration, notice_case["registration"]).status = "cancelled"
        else:
            db.session.get(User, notice_case["student"]).email = "changed-address@scu.edu"
        db.session.get(Notification, identifier).available_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        monkeypatch.setattr(notices, "send_email", lambda *args: pytest.fail("Stale notice sent"))
        assert notices.deliver_pending()["skipped"] == 1
        message = db.session.get(Notification, identifier)
        assert message.delivery_payload is None and message.details == {}


def test_removal_notices_never_republish_removed_content(app, notice_case, monkeypatch):
    with app.app_context():
        assert resolve_report(db.session.get(Report, notice_case["report"]),
                              db.session.get(User, notice_case["admin"]), "remove", "Private review note")
        message = db.session.scalar(db.select(Notification))
        assert message.kind == "removed"
        assert not resolve_report(db.session.get(Report, notice_case["report"]),
                                  db.session.get(User, notice_case["admin"]), "remove", "Second decision")
        assert db.session.scalar(db.select(db.func.count()).select_from(Notification)) == 1
        sent = []
        monkeypatch.setattr(notices, "send_email", lambda payload, key: sent.append(payload) or "safe-removal")
        assert notices.deliver_pending()["sent"] == 1
    content = json.dumps(sent)
    assert "was removed" in content
    for private in ("Private event title", "Private description", "Original room", "Private review note", "Private report details"):
        assert private not in content


def test_attempted_cancellation_is_not_resent_after_removal(app, notice_case, monkeypatch):
    identifier = queue(app, notice_case, "cancelled")
    monkeypatch.setattr(notices, "send_email", lambda *args: (_ for _ in ()).throw(notices.DeliveryError("timeout")))
    with app.app_context():
        notices.deliver_pending()
        db.session.get(Event, notice_case["event"]).status = "removed"
        db.session.get(Notification, identifier).available_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        monkeypatch.setattr(notices, "send_email", lambda *args: pytest.fail("Removed content resent"))
        assert notices.deliver_pending()["skipped"] == 1


@pytest.mark.parametrize("attempted", [True, False])
def test_cancel_then_moderation_sends_one_safe_replacement_only_to_affected_attendee(app, notice_case, monkeypatch, attempted):
    with app.app_context():
        earlier = User(name="Earlier cancellation", email="earlier@scu.edu", google_sub="earlier")
        db.session.add(Registration(student=earlier, event_id=notice_case["event"], status="cancelled"))
        db.session.commit()
    owner = browser(app, notice_case["host"])
    response = owner.post(f"/organizer/events/{notice_case['event']}/cancel", data={"csrf_token": "notice-token"})
    assert response.status_code == 302
    monkeypatch.setattr(notices, "send_email", lambda *args: (_ for _ in ()).throw(notices.DeliveryError("timeout")))
    with app.app_context():
        if attempted:
            notices.deliver_pending()
        old = db.session.scalar(db.select(Notification))
        assert old.kind == "cancelled" and old.status == "pending"
        assert resolve_report(db.session.get(Report, notice_case["report"]),
                              db.session.get(User, notice_case["admin"]), "remove", "Private moderation note")
        messages = list(db.session.scalars(db.select(Notification)))
        assert len(messages) == 2
        assert {message.kind for message in messages} == {"cancelled", "removed"}
        assert {message.registration_id for message in messages} == {notice_case["registration"]}
        old.available_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        delivered = []
        monkeypatch.setattr(notices, "send_email", lambda payload, key: delivered.append(payload) or "removal-email")
        result = notices.deliver_pending()
        assert result["sent"] == 1 and result["skipped"] == 1
    assert len(delivered) == 1
    content = json.dumps(delivered)
    assert "was removed" in content
    assert "earlier@scu.edu" not in content
    assert "Private event title" not in content
    assert "Original room" not in content
    assert "Private moderation note" not in content


def test_retry_attempts_are_bounded_and_permanent_failures_clear_payload(app, notice_case, monkeypatch):
    identifier = queue(app, notice_case)
    calls = []

    def fail(payload, key):
        calls.append(key)
        raise notices.DeliveryError("provider_unavailable")

    monkeypatch.setattr(notices, "send_email", fail)
    with app.app_context():
        for _ in range(notices.MAX_ATTEMPTS):
            message = db.session.get(Notification, identifier)
            message.available_at = utcnow() - timedelta(seconds=1)
            db.session.commit()
            notices.deliver_pending()
        message = db.session.get(Notification, identifier)
        assert message.status == "failed" and message.attempts == notices.MAX_ATTEMPTS
        assert message.delivery_payload is None and message.details == {}
        notices.deliver_pending()
    assert len(calls) == notices.MAX_ATTEMPTS


def test_permanent_failure_is_not_retried_and_discards_message_content(app, notice_case, monkeypatch):
    identifier = queue(app, notice_case)
    calls = []

    def reject(payload, key):
        calls.append(key)
        raise notices.DeliveryError("provider_rejected", False)

    monkeypatch.setattr(notices, "send_email", reject)
    with app.app_context():
        assert notices.deliver_pending()["failed"] == 1
        message = db.session.get(Notification, identifier)
        assert message.status == "failed" and message.attempts == 1
        assert message.delivery_payload is None and message.details == {}
        assert notices.deliver_pending()["failed"] == 0
    assert calls == [identifier]


def test_crashed_worker_releases_on_lease_expiry_with_same_key_and_payload(app, notice_case, monkeypatch):
    identifier = queue(app, notice_case)
    attempted = []

    def crash(payload, key):
        attempted.append((deepcopy(payload), key))
        raise RuntimeError("Process interrupted after provider call")

    monkeypatch.setattr(notices, "send_email", crash)
    with app.app_context():
        with pytest.raises(RuntimeError):
            notices.deliver_pending()
        db.session.remove()
        message = db.session.get(Notification, identifier)
        assert message.status == "sending" and message.delivery_payload == attempted[0][0]
        assert notices.deliver_pending()["sent"] == 0  # The first worker still holds its lease.
        message.lease_until = utcnow() - timedelta(seconds=1)
        db.session.commit()

        def recover(payload, key):
            attempted.append((deepcopy(payload), key))
            return "recovered-email"

        monkeypatch.setattr(notices, "send_email", recover)
        assert notices.deliver_pending()["sent"] == 1
    assert len(attempted) == 2 and attempted[0] == attempted[1]


@pytest.mark.parametrize("replacement", ["completed", "reclaimed", "expired"])
def test_stale_worker_cannot_prepare_or_send_after_losing_lease(app, notice_case, monkeypatch, replacement):
    identifier = queue(app, notice_case)
    original_payload = notices._payload

    def lose_lease(message):
        payload = original_payload(message)
        if replacement == "completed":
            # Another worker completed and cleared its sensitive envelope.
            message.status, message.lease_token, message.lease_until = "sent", None, None
            message.delivery_payload, message.details = None, {}
            message.completed_at = utcnow()
        elif replacement == "reclaimed":
            message.lease_token = "new-worker-token"
            message.lease_until = utcnow() + timedelta(minutes=2)
        else:
            message.lease_until = utcnow() - timedelta(seconds=1)
        db.session.commit()
        return payload

    monkeypatch.setattr(notices, "_payload", lose_lease)
    monkeypatch.setattr(notices, "send_email", lambda *args: pytest.fail("A stale worker must not send"))
    with app.app_context():
        result = notices.deliver_pending(limit=1)
        message = db.session.get(Notification, identifier)
        assert result["sent"] == 0 and message.attempts == 0
        assert message.delivery_payload is None
        if replacement == "completed":
            assert message.status == "sent" and message.details == {}
        elif replacement == "reclaimed":
            assert message.lease_token == "new-worker-token"


def test_event_edit_notice_commits_with_change_and_rolls_back_with_failed_cover_save(app, notice_case, monkeypatch):
    owner = browser(app, notice_case["host"])
    path = f"/organizer/events/{notice_case['event']}/edit"
    form = dict(csrf_token="notice-token", title="Private event title", description="Event description",
                location="New room", category="Tech", start_time="2050-01-01T12:00",
                end_time="2050-01-01T14:00", price="0", capacity="10")
    from app.routes import organizer
    real_save = organizer.commit_event_with_cover
    monkeypatch.setattr(organizer, "commit_event_with_cover", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("failed")))
    assert owner.post(path, data=form).status_code == 503
    with app.app_context():
        assert db.session.get(Event, notice_case["event"]).location == "Original room"
        assert db.session.scalar(db.select(db.func.count()).select_from(Notification)) == 0
    monkeypatch.setattr(organizer, "commit_event_with_cover", real_save)
    assert owner.post(path, data=form).status_code == 302
    assert owner.post(path, data=form).status_code == 302  # An identical save isn't another update.
    with app.app_context():
        assert db.session.get(Event, notice_case["event"]).location == "New room"
        assert db.session.scalar(db.select(db.func.count()).select_from(Notification)) == 1


def test_repeated_organizer_cancellation_queues_only_one_notice(app, notice_case):
    owner = browser(app, notice_case["host"])
    path = f"/organizer/events/{notice_case['event']}/cancel"
    for _ in range(2):
        assert owner.post(path, data={"csrf_token": "notice-token"}).status_code == 302
    with app.app_context():
        message = db.session.scalar(db.select(Notification))
        assert message.kind == "cancelled"
        assert db.session.scalar(db.select(db.func.count()).select_from(Notification)) == 1
        assert db.session.get(Registration, notice_case["registration"]).status == "cancelled"


@pytest.mark.parametrize("setting,value", [
    ("NOTIFICATION_EMAIL_PROVIDER", "smtp"), ("RESEND_API_KEY", ""),
    ("NOTIFICATION_FROM_EMAIL", "sender@example.com\r\nBcc:other@example.com"),
    ("SUPPORT_EMAIL", "not-an-email"), ("PUBLIC_BASE_URL", "http://broncoshub.example.com"),
    ("PUBLIC_BASE_URL", "https://evil.example.com"),
    ("PUBLIC_BASE_URL", "https://broncoshub.example.com:notaport"),
    ("PUBLIC_BASE_URL", "https://[broken"), ("PUBLIC_BASE_URL", None),
    ("PUBLIC_BASE_URL", "https://broncoshub.example.com/path"),
])
def test_invalid_enabled_email_configuration_fails_clearly(app, setting, value):
    configure(app)
    app.config["TRUSTED_HOSTS"] = ["broncoshub.example.com"]
    app.config[setting] = value
    with pytest.raises(RuntimeError):
        notices.configure_notifications(app)


class Response:
    def __init__(self, status=200, body=b'{"id":"provider-123"}'):
        self.status_code, self.body = status, body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield self.body


def transport(monkeypatch, response):
    calls = []

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            assert self.trust_env is False
            calls.append((url, kwargs))
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(notices.requests, "Session", Session)
    return calls


def envelope():
    return {"from": "events@example.com", "to": ["recipient@scu.edu"],
            "reply_to": "ecan@scu.edu", "subject": "Event update", "text": "Details"}


def test_resend_uses_immutable_payload_bounded_https_and_idempotency_key(app, monkeypatch):
    configure(app)
    calls = transport(monkeypatch, Response())
    with app.app_context():
        assert notices.send_email(envelope(), "notice-123") == "provider-123"
    url, request = calls[0]
    assert url == "https://api.resend.com/emails"
    assert request["headers"]["Idempotency-Key"] == "broncoshub-event-notice-123"
    assert request["json"] == envelope()
    assert request["allow_redirects"] is False and request["timeout"] == (5, 15)


@pytest.mark.parametrize("status,retryable", [(301, False), (401, False), (422, False), (429, True), (500, True)])
def test_provider_errors_have_safe_codes_and_correct_retryability(app, monkeypatch, status, retryable):
    configure(app)
    transport(monkeypatch, Response(status, b"sensitive response must not escape"))
    with app.app_context(), pytest.raises(notices.DeliveryError) as error:
        notices.send_email(envelope(), "notice-123")
    assert error.value.code == "provider_rejected" and error.value.retryable == retryable
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize("name,retryable", [
    ("concurrent_idempotent_requests", True), ("resource_locked", True),
    ("invalid_idempotent_request", False), ("unrecognized", False), ([], False),
])
def test_provider_409_retries_only_known_transient_error_names(app, monkeypatch, name, retryable):
    configure(app)
    transport(monkeypatch, Response(409, json.dumps({"name": name, "message": "private diagnostic"}).encode()))
    with app.app_context(), pytest.raises(notices.DeliveryError) as error:
        notices.send_email(envelope(), "same-notice")
    assert error.value.retryable == retryable
    assert error.value.code == ("provider_busy" if retryable else "provider_rejected")
    assert "private" not in str(error.value)


@pytest.mark.parametrize("response", [requests.Timeout("secret payload"), Response(body=b"bad json"),
                                       Response(body=b"x" * 17000), Response(body=b'{"id":"bad\\nid"}')])
def test_bad_provider_responses_are_bounded_and_sanitized(app, monkeypatch, response):
    configure(app)
    transport(monkeypatch, response)
    with app.app_context(), pytest.raises(notices.DeliveryError) as error:
        notices.send_email(envelope(), "notice-123")
    assert error.value.code in {"provider_unavailable", "invalid_provider_response"}
    assert "secret" not in str(error.value)


def test_workers_cannot_claim_same_notice_or_hold_database_lock_during_network(tmp_path, monkeypatch):
    path = tmp_path / "notifications.db"

    class ConcurrentConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{path}"
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 10, "check_same_thread": False}}
        COVER_UPLOAD_DIR = str(tmp_path / "covers")

    app = create_app(ConcurrentConfig)
    configure(app)
    case = seed(app)
    identifier = queue(app, case)
    first_inside, release = Signal(), Signal()
    calls, lock = [], Lock()

    def send(payload, key):
        # A separate connection can write while the provider is being called.
        with sqlite3.connect(path, timeout=1) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        with lock:
            calls.append(key)
        first_inside.set()
        assert release.wait(5)
        return "one-email"

    def worker():
        with app.app_context():
            return notices.deliver_pending(limit=1)

    monkeypatch.setattr(notices, "send_email", send)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker)
            assert first_inside.wait(5)
            second = pool.submit(worker)
            assert second.result(timeout=5)["sent"] == 0
            release.set()
            assert first.result(timeout=5)["sent"] == 1
        assert calls == [identifier]
    finally:
        release.set()
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
