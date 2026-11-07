"""Registration email follows committed admission state, never a browser redirect."""
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from app.extensions import db
from app.models import Event, Payment, Registration, Report, Ticket, User
from app.models.notification import Notification, utcnow
from app.services import notification_service as notices
from app.services.moderation_service import resolve_report
from app.services.payment_service import complete_purchase
from app.services.registration_service import cancel_free_registration, register_student
from app.services.stripe_checkout_service import BillingVerificationError, process_session
from test_stripe_checkout import paid_app  # Shared local-only Stripe contract fixture.
import test_stripe_checkout as stripe_cases


@pytest.fixture(autouse=True)
def no_external_requests(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Registration email tests cannot contact external services")
    monkeypatch.setattr("requests.sessions.Session.request", blocked)


def enable_local_mail(app):
    app.config.update(NOTIFICATION_EMAIL_PROVIDER="resend", RESEND_API_KEY="re_local_only",
                      NOTIFICATION_FROM_EMAIL="events@example.com", SUPPORT_EMAIL="ecan@scu.edu",
                      PUBLIC_BASE_URL="https://broncoshub.example.com")


def seed_signup(app):
    enable_local_mail(app)
    with app.app_context():
        host = User(name="Host", email="signup-host@scu.edu", google_sub="signup-host")
        student = User(name="Student", email="signup-student@scu.edu", google_sub="signup-student")
        admin = User(name="Admin", email="signup-admin@scu.edu", google_sub="signup-admin", role="admin")
        event = Event(organizer=host, title="Campus game night", description="Private description",
                      location="Campus Hall", category="Tech", price_cents=0, capacity=5,
                      start_time=datetime(2050, 1, 1, 12), end_time=datetime(2050, 1, 1, 14),
                      status="published")
        db.session.add_all([host, student, admin, event])
        db.session.commit()
        return {"host": host.id, "student": student.id, "admin": admin.id, "event": event.id}


@pytest.fixture
def signup_case(app):
    return seed_signup(app)


def client_for(app, identifier):
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(_user_id=str(identifier), _fresh=True,
                       moderation_csrf={"user": str(identifier), "token": "signup-token"})
    return client


def register(app, case):
    return register_student(db.session.get(User, case["student"]), db.session.get(Event, case["event"]))


def all_notices():
    return list(db.session.scalars(db.select(Notification).order_by(Notification.created_at, Notification.id)))


def record_delivery(monkeypatch):
    sent = []
    def send(payload, identifier):
        assert not db.session().in_transaction()
        sent.append((deepcopy(payload), identifier))
        return "provider-local-email"
    monkeypatch.setattr(notices, "send_email", send)
    return sent


def test_free_http_registration_queues_one_confirmation_for_repeated_submissions(app, signup_case, monkeypatch):
    sent = record_delivery(monkeypatch)
    client = client_for(app, signup_case["student"])
    url = f"/events/{signup_case['event']}/register"
    for _ in range(2):
        assert client.post(url, data={"csrf_token": "signup-token"}).status_code == 302
    assert sent == []  # Saving a registration never sends email inside its transaction.
    with app.app_context():
        registration = db.session.scalar(db.select(Registration))
        assert registration.status == "confirmed"
        [message] = all_notices()
        assert message.kind == "registered" and message.registration_id == registration.id
        assert notices.deliver_pending()["sent"] == 1
        assert notices.deliver_pending()["sent"] == 0
    [payload, _] = sent[0]
    assert payload["subject"] == "Your registration is confirmed"
    assert payload["to"] == ["signup-student@scu.edu"]
    assert "Campus game night" in payload["text"] and "Campus Hall" in payload["text"]
    assert "Pacific time" in payload["text"] and "/my-registrations/" in payload["text"]
    assert "Private description" not in json.dumps(payload)
    assert "Ticket reference" not in payload["text"]


@pytest.mark.parametrize("status,capacity", [("cancelled", 5), ("removed", 5), ("published", 0)])
def test_rejected_registration_has_no_confirmation(app, signup_case, status, capacity):
    with app.app_context():
        event = db.session.get(Event, signup_case["event"])
        event.status, event.capacity = status, capacity
        db.session.commit()
    client = client_for(app, signup_case["student"])
    response = client.post(f"/events/{signup_case['event']}/register", data={"csrf_token": "signup-token"})
    assert response.status_code in {302, 404}
    with app.app_context():
        assert all_notices() == []


def test_self_cancellation_supersedes_unsent_confirmation_and_repeated_cancel_is_quiet(app, signup_case, monkeypatch):
    client = client_for(app, signup_case["student"])
    with app.app_context():
        registration = register(app, signup_case)
        identifier = registration.id
    for _ in range(2):
        assert client.post(f"/my-registrations/{identifier}/cancel",
                           data={"csrf_token": "signup-token"}).status_code == 302
    sent = record_delivery(monkeypatch)
    with app.app_context():
        messages = {message.kind: message for message in all_notices()}
        assert len(messages) == 2
        assert messages["registered"].status == "skipped"
        assert messages["unregistered"].status == "pending"
        assert notices.deliver_pending()["sent"] == 1
    assert sent[0][0]["subject"] == "Your registration was cancelled"
    assert "You cancelled" in sent[0][0]["text"]
    assert "Campus Hall" in sent[0][0]["text"] and "Pacific time" in sent[0][0]["text"]


def test_cancel_then_reregister_sends_only_latest_registration_state(app, signup_case, monkeypatch):
    with app.app_context():
        registration = register(app, signup_case)
        first_id = all_notices()[0].id
        cancel_free_registration(db.session.get(User, signup_case["student"]), registration)
        register(app, signup_case)
        messages = all_notices()
        assert len(messages) == 3 and len({message.id for message in messages}) == 3
        pending = [message for message in messages if message.status == "pending"]
        assert len(pending) == 1 and pending[0].kind == "registered" and pending[0].id != first_id
        assert all(message.delivery_payload is None for message in messages)
        sent = record_delivery(monkeypatch)
        assert notices.deliver_pending()["sent"] == 1
    assert sent[0][0]["subject"] == "Your registration is confirmed"


def test_registration_and_notice_rollback_together_when_queue_fails(app, signup_case, monkeypatch):
    original = notices.queue_registration_notice
    def fail_after_queue(registration, kind):
        original(registration, kind)
        db.session.flush()
        raise RuntimeError("Local queue failure")
    monkeypatch.setattr(notices, "queue_registration_notice", fail_after_queue)
    with app.app_context():
        with pytest.raises(RuntimeError, match="queue failure"):
            register(app, signup_case)
        assert db.session.scalar(db.select(db.func.count()).select_from(Registration)) == 0
        assert all_notices() == []


def test_cancellation_and_superseding_notice_rollback_together(app, signup_case, monkeypatch):
    with app.app_context():
        registration = register(app, signup_case)
        identifier = registration.id
        old_id = all_notices()[0].id
        original = notices.queue_registration_notice
        def fail_after_queue(registration, kind):
            original(registration, kind)
            db.session.flush()
            raise RuntimeError("Local queue failure")
        monkeypatch.setattr(notices, "queue_registration_notice", fail_after_queue)
        with pytest.raises(RuntimeError, match="queue failure"):
            cancel_free_registration(db.session.get(User, signup_case["student"]), registration)
        assert db.session.get(Registration, identifier).status == "confirmed"
        [message] = all_notices()
        assert message.id == old_id and message.status == "pending"


@pytest.mark.parametrize("card,status", [("4000", "failed"), ("4999", "pending")])
def test_paid_pending_and_declined_do_not_claim_registration_confirmed(app, signup_case, card, status):
    app.config["PAYMENT_PROVIDER"] = "mock"
    with app.app_context():
        event = db.session.get(Event, signup_case["event"])
        event.price_cents = 1500
        db.session.commit()
        registration = register(app, signup_case)
        assert all_notices() == [] and registration.status == "pending"
        complete_purchase(registration, card)
        assert registration.status == status and all_notices() == []


def test_mock_payment_success_queues_one_confirmation_with_ticket(app, signup_case, monkeypatch):
    app.config["PAYMENT_PROVIDER"] = "mock"
    with app.app_context():
        db.session.get(Event, signup_case["event"]).price_cents = 1500
        db.session.commit()
        registration = register(app, signup_case)
        complete_purchase(registration, "4242424242424242")
        complete_purchase(registration, "4242424242424242")
        [message] = all_notices()
        assert message.kind == "registered"
        ticket = registration.ticket.code
        sent = record_delivery(monkeypatch)
        assert notices.deliver_pending()["sent"] == 1
    assert f"Ticket reference: {ticket}" in sent[0][0]["text"]
    assert "4242424242424242" not in json.dumps(sent)


@pytest.mark.parametrize("provider_name", ["mock", "stripe"])
def test_paid_admission_ticket_and_notice_rollback_together_then_retry_once(paid_app, monkeypatch, provider_name):
    app, provider = paid_app
    original = notices.queue_registration_notice
    def fail_after_queue(registration, kind):
        original(registration, kind)
        db.session.flush()
        raise RuntimeError("Local queue failure")
    with app.app_context():
        stripe_cases.start()
        paid = provider.pay("cs_1")
        if provider_name == "mock":
            app.config["PAYMENT_PROVIDER"] = "mock"
        def confirm():
            if provider_name == "mock":
                return complete_purchase(db.session.get(Registration, 1), "4242")
            return process_session(paid, "acct_organizer", verified_snapshot=True)
        monkeypatch.setattr(notices, "queue_registration_notice", fail_after_queue)
        with pytest.raises(RuntimeError, match="queue failure"):
            confirm()
        assert db.session.get(Registration, 1).status == "pending"
        assert db.session.scalar(db.select(Payment)) is None
        assert db.session.scalar(db.select(Ticket)) is None
        assert all_notices() == []
        monkeypatch.setattr(notices, "queue_registration_notice", original)
        confirm()
        confirm()
        assert db.session.get(Registration, 1).status == "confirmed"
        assert len(all_notices()) == 1 and all_notices()[0].kind == "registered"


def test_verified_stripe_completion_queues_once_and_unpaid_redirect_does_not(paid_app, monkeypatch):
    app, provider = paid_app
    enable_local_mail(app)
    with app.app_context():
        stripe_cases.start()
        assert all_notices() == []
        # A provider snapshot that is not paid cannot confirm admission.
        process_session(provider.get("acct_organizer", "cs_1"), "acct_organizer", verified_snapshot=True)
        assert all_notices() == []
        paid = provider.pay("cs_1")
    for _ in range(2):
        assert stripe_cases.webhook(app, stripe_cases.completed(paid)).status_code == 200
    sent = record_delivery(monkeypatch)
    with app.app_context():
        [message] = all_notices()
        assert message.kind == "registered"
        assert db.session.get(Registration, 1).status == "confirmed"
        assert notices.deliver_pending()["sent"] == 1
    text = sent[0][0]["text"]
    assert "Ticket reference: BH-" in text
    for private in ("pi_cs_1", "acct_organizer", "whsec_", "sk_test_"):
        assert private not in json.dumps(sent)


def test_wrong_stripe_amount_never_queues_confirmation(paid_app):
    app, provider = paid_app
    with app.app_context():
        stripe_cases.start()
        paid = provider.pay("cs_1")
        paid["amount_total"] += 1
        with pytest.raises(BillingVerificationError):
            process_session(paid, "acct_organizer", verified_snapshot=True)
        db.session.rollback()
        assert all_notices() == []


def test_late_paid_session_is_refunded_without_confirmation(paid_app):
    app, provider = paid_app
    with app.app_context():
        stripe_cases.start()
        stripe_cases.attempt().expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        process_session(provider.pay("cs_1"), "acct_organizer", verified_snapshot=True)
        assert all_notices() == []
        assert db.session.get(Registration, 1).status == "cancelled"


@pytest.mark.parametrize("change", ["cancelled", "removed", "ended", "registration", "email"])
def test_stale_registered_notice_is_not_delivered(app, signup_case, monkeypatch, change):
    with app.app_context():
        registration = register(app, signup_case)
        event = db.session.get(Event, signup_case["event"])
        if change in {"cancelled", "removed"}:
            event.status = change
        elif change == "ended":
            event.end_time = datetime(2000, 1, 1)
        elif change == "registration":
            registration.status = "cancelled"
        else:
            registration.student.email = "outside@example.com"
        db.session.commit()
        monkeypatch.setattr(notices, "send_email", lambda *args: pytest.fail("Stale confirmation sent"))
        assert notices.deliver_pending()["skipped"] == 1


def test_removal_before_confirmation_sends_only_safe_removal_notice(app, signup_case, monkeypatch):
    with app.app_context():
        registration = register(app, signup_case)
        report = Report(event_id=signup_case["event"], reporter_id=signup_case["student"],
                        reason="misleading", details="Private report")
        db.session.add(report)
        db.session.commit()
        assert resolve_report(report, db.session.get(User, signup_case["admin"]), "remove", "Private decision")
        sent = record_delivery(monkeypatch)
        result = notices.deliver_pending()
        assert result["sent"] == 1 and result["skipped"] == 1
    assert sent[0][0]["subject"] == "An event you registered for was removed"
    assert "Campus game night" not in json.dumps(sent)


def test_initial_confirmation_uses_current_schedule_then_retry_preserves_envelope(app, signup_case, monkeypatch):
    attempts = []
    def timeout(payload, key):
        attempts.append((deepcopy(payload), key))
        raise notices.DeliveryError("provider_unavailable")
    with app.app_context():
        register(app, signup_case)
        event = db.session.get(Event, signup_case["event"])
        event.location = "New room"
        db.session.commit()
        monkeypatch.setattr(notices, "send_email", timeout)
        notices.deliver_pending()
        [message] = all_notices()
        assert message.details["location"] == "New room"
        assert "New room" in attempts[0][0]["text"]
        message.available_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        app.config["NOTIFICATION_FROM_EMAIL"] = "changed@example.com"
        monkeypatch.setattr(notices, "send_email", lambda payload, key: attempts.append((deepcopy(payload), key)) or "sent")
        assert notices.deliver_pending()["sent"] == 1
    assert attempts[0] == attempts[1]


def test_new_schedule_stops_retry_of_frozen_confirmation(app, signup_case, monkeypatch):
    with app.app_context():
        register(app, signup_case)
        monkeypatch.setattr(notices, "send_email", lambda *args: (_ for _ in ()).throw(
            notices.DeliveryError("provider_unavailable")))
        notices.deliver_pending()
        [message] = all_notices()
        message.available_at = utcnow() - timedelta(seconds=1)
        db.session.get(Event, signup_case["event"]).location = "New room"
        db.session.commit()
        monkeypatch.setattr(notices, "send_email", lambda *args: pytest.fail("Stale frozen confirmation sent"))
        assert notices.deliver_pending()["skipped"] == 1


def test_inflight_completion_cannot_revive_confirmation_superseded_by_cancellation(app, signup_case, monkeypatch):
    with app.app_context():
        registration = register(app, signup_case)
        identifier = registration.id
        confirmation_id = all_notices()[0].id
        def cancel_during_send(payload, key):
            cancel_free_registration(db.session.get(User, signup_case["student"]),
                                     db.session.get(Registration, identifier))
            return "already-accepted-email"
        monkeypatch.setattr(notices, "send_email", cancel_during_send)
        notices.deliver_pending(limit=1)
        message = db.session.get(Notification, confirmation_id)
        assert message.status == "skipped" and message.delivery_payload is None and message.lease_token is None
        sent = record_delivery(monkeypatch)
        assert notices.deliver_pending()["sent"] == 1
    assert sent[0][0]["subject"] == "Your registration was cancelled"


def test_inflight_cancellation_retry_cannot_revive_after_reregistration(app, signup_case, monkeypatch):
    with app.app_context():
        registration = register(app, signup_case)
        cancel_free_registration(db.session.get(User, signup_case["student"]), registration)
        cancellation = next(message for message in all_notices() if message.kind == "unregistered")
        cancellation_id = cancellation.id
        def reregister_during_send(payload, key):
            assert payload["subject"] == "Your registration was cancelled"
            register(app, signup_case)
            raise notices.DeliveryError("provider_unavailable")
        monkeypatch.setattr(notices, "send_email", reregister_during_send)
        notices.deliver_pending(limit=1)
        cancellation = db.session.get(Notification, cancellation_id)
        assert cancellation.status == "skipped" and cancellation.delivery_payload is None
        assert cancellation.details == {} and cancellation.lease_token is None
        sent = record_delivery(monkeypatch)
        assert notices.deliver_pending()["sent"] == 1
    assert sent[0][0]["subject"] == "Your registration is confirmed"
