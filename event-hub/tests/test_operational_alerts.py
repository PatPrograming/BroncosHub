"""Runtime/worker failure wiring with real database transactions, no providers."""

from datetime import datetime, timedelta
import json

import pytest
from sqlalchemy.exc import OperationalError

from app import observability
from app.extensions import db
from app.models import Event, Payment, Registration, User
from app.models.checkout_attempt import CheckoutAttempt
from app.models.notification import Notification
from app.services import notification_service as notices
from tools import payment_worker as worker


class LocalAlerts:
    """Record the outgoing fixed event; SDK transport is tested separately."""

    def __init__(self):
        self.events = []

    def capture_event(self, event, scope=None):
        assert scope is None
        assert set(event) == {"event_id", "logger", "tags"}
        cleaned = observability._before_send(event, {})
        assert cleaned is not None
        self.events.append(cleaned)
        return cleaned["event_id"]


@pytest.fixture
def alerts(app, monkeypatch):
    recorder = LocalAlerts()
    app.extensions[observability.EXTENSION_KEY] = observability.AlertState(recorder)

    def no_network(*args, **kwargs):
        pytest.fail("Operational tests must never contact a provider")

    monkeypatch.setattr("requests.sessions.Session.request", no_network)
    return recorder


def assert_alerts(alerts, *categories):
    assert [item["tags"]["category"] for item in alerts.events] == list(categories)
    for event, category in zip(alerts.events, categories):
        assert event["message"] == observability.FAILURES[category]
        assert event["fingerprint"] == ["broncoshub", category]
        assert set(event) == {"event_id", "timestamp", "platform", "level", "logger",
                              "message", "tags", "fingerprint"}
        assert "private-" not in json.dumps(event)


@pytest.fixture
def event_case(app):
    with app.app_context():
        host = User(name="Host", email="private-host@scu.edu", google_sub="host")
        student = User(name="Student", email="private-student@scu.edu", google_sub="student")
        event = Event(organizer=host, title="private-event-title", location="private-room",
                      category="Tech", price_cents=1500, status="published",
                      start_time=datetime(2050, 1, 1, 12), end_time=datetime(2050, 1, 1, 14))
        registration = Registration(event=event, student=student, status="confirmed")
        db.session.add_all([host, student, event, registration])
        db.session.commit()
        return {"host": host.id, "event": event.id, "registration": registration.id}


def test_unhandled_request_reports_only_fixed_failure_and_hides_exception(app, alerts):
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.get("/_test/private-request")
    def fail():
        raise RuntimeError("private-password and private-email@scu.edu")

    response = app.test_client().get("/_test/private-request?token=private-token",
                                     headers={"Authorization": "private-bearer"})
    assert response.status_code == 500
    assert b"private-" not in response.data
    assert_alerts(alerts, "http_500")


@pytest.mark.parametrize("failure,status", [("database is locked", 503), ("connection lost", 500)])
def test_database_request_reports_fixed_category_and_correct_response(app, alerts, failure, status):
    @app.get("/_test/database")
    def fail():
        raise OperationalError("private-query", {"email": "private-email"}, Exception(failure))

    response = app.test_client().get("/_test/database")
    assert response.status_code == status
    assert b"private-" not in response.data
    assert_alerts(alerts, "database_failure")


def test_health_failure_alerts_without_secrets_and_recovers(app, alerts, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("private-database-password")

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "execute", fail)
        response = app.test_client().get("/healthz")
    assert response.status_code == 503
    assert response.json == {"status": "unavailable"}
    assert app.test_client().get("/healthz").json == {"status": "ok"}
    assert_alerts(alerts, "database_failure")


def test_payment_failure_rolls_back_and_notices_get_a_fresh_session(app, alerts, event_case, monkeypatch):
    app.config["PAYMENT_PROVIDER"] = "stripe"
    sessions = []

    def payment_failure(limit):
        assert limit == 20
        sessions.append(db.session())
        db.session.get(User, event_case["host"]).name = "Uncommitted change"
        db.session.flush()
        raise RuntimeError("private-stripe-response")

    def successful_notices(limit):
        assert limit == 20
        sessions.append(db.session())
        assert db.session.get(User, event_case["host"]).name == "Host"
        return {"sent": 1, "failed": 0}

    monkeypatch.setattr(worker, "reconcile_pending", payment_failure)
    monkeypatch.setattr(worker, "deliver_pending", successful_notices)
    worker.run_cycle(app)
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert_alerts(alerts, "payment_recovery")


def test_notice_failure_cannot_undo_committed_payment(app, alerts, event_case, monkeypatch):
    app.config["PAYMENT_PROVIDER"] = "stripe"

    def successful_payment(limit):
        db.session.add(Payment(registration_id=event_case["registration"], amount_cents=1500,
                               status="succeeded", provider_reference="private-payment-reference"))
        db.session.commit()

    def notice_failure(limit):
        payment = db.session.scalar(db.select(Payment))
        assert payment.status == "succeeded"
        payment.status = "Uncommitted"
        db.session.flush()
        raise RuntimeError("private-recipient@scu.edu")

    monkeypatch.setattr(worker, "reconcile_pending", successful_payment)
    monkeypatch.setattr(worker, "deliver_pending", notice_failure)
    worker.run_cycle(app)
    with app.app_context():
        payment = db.session.scalar(db.select(Payment))
        assert payment.status == "succeeded" and payment.amount_cents == 1500
    assert_alerts(alerts, "notification_worker")


def queued_notice(app, event_case):
    app.config.update(NOTIFICATION_EMAIL_PROVIDER="resend", RESEND_API_KEY="re_local_only",
                      NOTIFICATION_FROM_EMAIL="events@example.com",
                      PUBLIC_BASE_URL="https://broncoshub.example.com")
    with app.app_context():
        notices.queue_event_notices(db.session.get(Event, event_case["event"]), "changed")
        db.session.commit()
        return db.session.scalar(db.select(Notification.id))


def test_terminal_notification_failure_triggers_operator_alert(app, alerts, event_case, monkeypatch, caplog):
    app.config["PAYMENT_PROVIDER"] = "disabled"
    identifier = queued_notice(app, event_case)

    def reject(payload, key):
        raise notices.DeliveryError("provider_rejected", False)

    monkeypatch.setattr(notices, "send_email", reject)
    worker.run_cycle(app)
    with app.app_context():
        notice = db.session.get(Notification, identifier)
        assert notice.status == "failed" and notice.delivery_payload is None
    assert_alerts(alerts, "notification_failed")
    assert "Event notification delivery needs operator review" in caplog.text
    assert "private-" not in caplog.text


def test_disabled_payments_still_deliver_real_queued_notice(app, alerts, event_case, monkeypatch):
    app.config["PAYMENT_PROVIDER"] = "disabled"
    identifier = queued_notice(app, event_case)
    delivered = []

    def no_payments(**kwargs):
        pytest.fail("Stripe reconciliation is disabled")

    def send(payload, key):
        delivered.append(key)
        return "local-provider-receipt"

    monkeypatch.setattr(worker, "reconcile_pending", no_payments)
    monkeypatch.setattr(notices, "send_email", send)
    worker.run_cycle(app)
    with app.app_context():
        assert db.session.get(Notification, identifier).status == "sent"
    assert delivered == [identifier]
    assert_alerts(alerts)


@pytest.mark.parametrize("error,expected", [
    ("Refund failed; operator review required.", ("refund_pending",)),
    ("Temporary provider error; retry later.", ()),
])
def test_durable_failed_refund_alerts_but_routine_retry_does_not(app, alerts, event_case, monkeypatch,
                                                             error, expected):
    app.config["PAYMENT_PROVIDER"] = "stripe"
    with app.app_context():
        db.session.add(CheckoutAttempt(
            registration_id=event_case["registration"], event_id=event_case["event"],
            account_id="private-account", amount_cents=1500, event_title="private-title",
            customer_email="private-customer@scu.edu", status="refund_pending", last_error=error,
            expires_at=datetime.now() + timedelta(hours=1),
            success_url="https://broncoshub.example.com/success", cancel_url="https://broncoshub.example.com/cancel",
        ))
        db.session.commit()
    calls = []
    monkeypatch.setattr(worker, "reconcile_pending", lambda **kwargs: calls.append("payments"))
    monkeypatch.setattr(worker, "deliver_pending", lambda **kwargs: calls.append("notices") or {"failed": 0})
    worker.run_cycle(app)
    assert calls == ["payments", "notices"]
    assert_alerts(alerts, *expected)
