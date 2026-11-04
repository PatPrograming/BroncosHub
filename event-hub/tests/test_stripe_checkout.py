"""Provider contract fakes, real transactions, signatures and competing buyers."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import sqlite3
from threading import Barrier, Lock
import time

import pytest

from app import create_app
from app.extensions import db
from app.models import ConnectedAccount, Event, Payment, Registration, Report, Ticket, User
from app.models.checkout_attempt import CheckoutAttempt, utcnow
from app.services import stripe_gateway as gateway
from app.services.moderation_service import resolve_report
from app.services.payment_service import CheckoutNotAllowed
from app.services.stripe_checkout_service import (
    BillingVerificationError, CheckoutTemporarilyUnavailable, process_session,
    process_refund, queue_event_cancellations, reconcile_pending, start_checkout,
)
from app.services.event_write_service import event_write, occupied_seats
from config import TestConfig


class Provider:
    def __init__(self, path):
        self.path, self.sessions, self.keys, self.refunds = path, {}, {}, {}
        self.guard = Lock()
        self.lose_response = False
        self.fail_refund = False
        self.pending_refund = False
        self.refund_objects = {}
        self.create_calls = []

    def network_boundary(self):
        # A second SQLite writer must work while any provider request is running.
        connection = sqlite3.connect(self.path, timeout=0.05)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        finally:
            connection.close()

    def ready(self, user_id):
        self.network_boundary()
        return db.session.scalar(db.select(ConnectedAccount).where(ConnectedAccount.user_id == user_id))

    def create(self, **arguments):
        self.network_boundary()
        with self.guard:
            self.create_calls.append(deepcopy(arguments))
            key = arguments["idempotency_key"]
            if key not in self.keys:
                session_id = f"cs_{len(self.sessions) + 1}"
                self.keys[key] = session_id
                self.sessions[session_id] = dict(
                    id=session_id, url=f"https://checkout.stripe.com/c/pay/{session_id}",
                    amount_total=arguments["amount_cents"], currency="usd", livemode=False,
                    mode="payment", status="open", payment_status="unpaid", payment_intent=None,
                    metadata={"attempt_id": str(arguments["attempt_id"]),
                              "registration_id": str(arguments["registration_id"])},
                )
            if self.lose_response:
                self.lose_response = False
                raise gateway.PaymentProviderError(retryable=True)
            return deepcopy(self.sessions[self.keys[key]])

    def get(self, account_id, session_id):
        self.network_boundary()
        assert account_id == "acct_organizer"
        return deepcopy(self.sessions[session_id])

    def expire(self, account_id, session_id, idempotency_key):
        self.network_boundary()
        if self.sessions[session_id]["payment_status"] == "paid":
            raise gateway.PaymentProviderError()
        self.sessions[session_id]["status"] = "expired"
        return self.get(account_id, session_id)

    def pay(self, session_id):
        session = self.sessions[session_id]
        session.update(status="complete", payment_status="paid", payment_intent={
            "id": f"pi_{session_id}", "latest_charge": {
                "amount": session["amount_total"], "currency": "usd", "paid": True,
                "amount_refunded": 0,
                "refunds": {"data": [], "has_more": False},
            },
        })
        return deepcopy(session)

    def refund(self, account_id, payment_intent_id, idempotency_key):
        self.network_boundary()
        if self.fail_refund:
            raise gateway.PaymentProviderError(retryable=True)
        with self.guard:
            if idempotency_key in self.refunds:
                return deepcopy(next(value for value in self.refund_objects.values()
                                     if value["payment_intent"] == payment_intent_id))
            self.refunds.setdefault(idempotency_key, payment_intent_id)
            result = {"id": f"re_{payment_intent_id}", "status": "pending" if self.pending_refund else "succeeded",
                      "payment_intent": payment_intent_id, "amount": 1500, "currency": "usd", "livemode": False}
            self.refund_objects[result["id"]] = result
            for session in self.sessions.values():
                intent = session.get("payment_intent")
                if intent and intent["id"] == payment_intent_id:
                    result["amount"] = session["amount_total"] - intent["latest_charge"]["amount_refunded"]
                    intent["latest_charge"]["amount_refunded"] = session["amount_total"]
                    intent["latest_charge"]["refunds"]["data"].append(result)
        return deepcopy(result)

    def get_refund(self, account_id, refund_id):
        self.network_boundary()
        return deepcopy(self.refund_objects[refund_id])


@pytest.fixture
def paid_app(tmp_path, monkeypatch):
    path = tmp_path / "payments.db"
    class PaymentConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{path}"
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 10, "check_same_thread": False}}
        PAYMENT_PROVIDER = "stripe"
        STRIPE_SECRET_KEY = "sk_test_local_contract_only"
        STRIPE_WEBHOOK_SECRET = "whsec_local_contract_only"
        PUBLIC_BASE_URL = "https://broncos.example"
        STRIPE_LIVE_MODE = False
        COVER_UPLOAD_DIR = str(tmp_path / "covers")
    app = create_app(PaymentConfig)
    provider = Provider(path)
    monkeypatch.setattr(gateway, "payment_account_for", provider.ready)
    monkeypatch.setattr(gateway, "create_checkout", provider.create)
    monkeypatch.setattr(gateway, "retrieve_checkout", provider.get)
    monkeypatch.setattr(gateway, "expire_checkout", provider.expire)
    monkeypatch.setattr(gateway, "refund_payment", provider.refund)
    monkeypatch.setattr(gateway, "retrieve_refund", provider.get_refund)
    with app.app_context():
        db.session.add_all([User(id=index, name=f"Buyer {index}", email=f"buyer{index}@scu.edu",
                                 google_sub=f"paid-{index}", role=role)
                            for index, role in enumerate(("organizer", "student", "student", "admin"), 1)])
        db.session.add(ConnectedAccount(user_id=1, stripe_account_id="acct_organizer",
                                       charges_enabled=True, payouts_enabled=True, details_submitted=True,
                                       configuration_verified=True, livemode=False))
        event = Event(id=1, organizer_id=1, title="Paid event", description="A campus event.",
                      category="Tech", location="Benson", status="published", price_cents=1500,
                      capacity=1, start_time=datetime(2050, 1, 1, 12), end_time=datetime(2050, 1, 1, 13))
        db.session.add(event)
        db.session.add_all([Registration(id=index, event_id=1, student_id=index + 1, status="pending")
                            for index in (1, 2)])
        db.session.add(Report(id=1, event_id=1, reporter_id=2, reason="misleading", details="Please review.", status="pending"))
        db.session.commit()
    yield app, provider
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def start(registration_id=1):
    return start_checkout(db.session.get(Registration, registration_id),
                          "https://broncos.example/receipt", "https://broncos.example/checkout")


def attempt():
    return db.session.scalar(db.select(CheckoutAttempt).order_by(CheckoutAttempt.id.desc()))


def webhook(app, payload, *, valid=True):
    body = json.dumps(payload).encode()
    timestamp = int(time.time())
    signed = f"{timestamp}.".encode() + body
    signature = hmac.new(app.config["STRIPE_WEBHOOK_SECRET"].encode(), signed, hashlib.sha256).hexdigest()
    return app.test_client().post("/billing/webhook", data=body, content_type="application/json",
                                 headers={"Stripe-Signature": f"t={timestamp},v1={signature if valid else 'bad'}"})


def completed(session):
    return {"id": "evt_test", "object": "event", "type": "checkout.session.completed",
            "account": "acct_organizer", "data": {"object": session}}


def test_two_independent_buyers_reserve_only_one_seat(paid_app):
    app, provider = paid_app
    ready = Barrier(2)
    def checkout(registration_id):
        with app.app_context():
            db.session.get(Registration, registration_id)
            ready.wait(timeout=5)
            try:
                start(registration_id)
                return "reserved"
            except CheckoutNotAllowed:
                return "full"
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(checkout, index) for index in (1, 2)]
        assert sorted(future.result(timeout=15) for future in futures) == ["full", "reserved"]
    assert len(provider.sessions) == 1
    with app.app_context():
        assert occupied_seats(1) == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(Payment)) == 0


def test_duplicate_checkout_reuses_one_stable_attempt_after_lost_response(paid_app):
    app, provider = paid_app
    with app.app_context():
        provider.lose_response = True
        with pytest.raises(CheckoutTemporarilyUnavailable):
            start()
        db.session.get(Event, 1).title = "Edited after request"
        db.session.get(User, 2).email = "changed@scu.edu"
        db.session.commit()
        url = start()
        assert url == start()
        assert len(provider.sessions) == 1
        assert provider.create_calls[0] == provider.create_calls[1]
        assert attempt().status == "open"


def test_signed_completion_and_replay_issue_exactly_one_payment_and_ticket(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        session = provider.pay(attempt().session_id)
    assert webhook(app, completed(session), valid=False).status_code == 400
    assert webhook(app, completed(session)).status_code == 200
    assert webhook(app, completed(session)).status_code == 200
    with app.app_context():
        assert db.session.get(Registration, 1).registration_view()["ticket_valid"]
        assert db.session.scalar(db.select(db.func.count()).select_from(Payment)) == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 1
        assert attempt().status == "completed"


@pytest.mark.parametrize("field,value", [("amount_total", 1), ("currency", "eur"),
                                         ("livemode", True), ("id", "cs_wrong"), ("mode", "subscription")])
def test_signed_but_mismatched_session_never_issues_ticket(paid_app, field, value):
    app, provider = paid_app
    with app.app_context():
        start()
        session = provider.pay(attempt().session_id)
    session[field] = value
    assert webhook(app, completed(session)).status_code == 400
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Payment)) == 0
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 0


def test_wrong_connected_account_is_rejected(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        payload = completed(provider.pay(attempt().session_id))
    payload["account"] = "acct_someone_else"
    assert webhook(app, payload).status_code == 400


def test_expired_hold_frees_seat_and_late_payment_is_refunded(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        old = attempt()
        old_session = old.session_id
        old.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        start(2)
        session = provider.pay(old_session)
        process_session(session, "acct_organizer")
        assert db.session.get(Registration, 1).status == "cancelled"
        assert db.session.get(Payment, 1).status == "refunded"
        assert occupied_seats(1) == 1
        assert len(provider.refunds) == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 0


def test_removal_queues_failed_refund_durably_and_worker_retries(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        process_session(provider.pay(attempt().session_id), "acct_organizer")
        resolve_report(db.session.get(Report, 1), db.session.get(User, 4), "remove", "Removed")
        assert attempt().status == "refund_pending"
        provider.fail_refund = True
        reconcile_pending()
        assert attempt().status == "refund_pending"
        assert not db.session.get(Registration, 1).registration_view()["ticket_valid"]
        assert db.session.get(Payment, 1).status == "refund_pending"
    with app.app_context():
        provider.fail_refund = False
        reconcile_pending()
        reconcile_pending()
        assert attempt().status == "refunded"
        assert len(provider.refunds) == 1
        assert db.session.get(Payment, 1).amount_cents == 1500


def test_cancel_before_late_completion_never_issues_ticket(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        session_id = attempt().session_id
        with event_write(1) as event:
            queue_event_cancellations(1)
            event.status = "cancelled"
            db.session.get(Registration, 1).status = "cancelled"
            db.session.commit()
        process_session(provider.pay(session_id), "acct_organizer")
        assert attempt().status == "refunded"
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 0


def test_refund_before_completion_is_observed_from_current_charge(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        session_id = attempt().session_id
        stale_event = provider.pay(session_id)
        provider.sessions[session_id]["payment_intent"]["latest_charge"]["amount_refunded"] = 1500
        provider.sessions[session_id]["payment_intent"]["latest_charge"]["refunds"]["data"] = [
            {"id": "re_external", "amount": 1500, "status": "succeeded"},
        ]
        process_session(stale_event, "acct_organizer")
        assert attempt().status == "refunded"
        assert db.session.get(Payment, 1).status == "refunded"
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 0


def test_return_url_is_only_a_receipt_page_and_never_settles_payment(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        provider.pay(attempt().session_id)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"], session["_fresh"] = "2", True
    response = client.get("/my-registrations/1?paid=true&session_id=cs_1")
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Registration, 1).status == "pending"
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 0


def test_worker_reconciles_paid_session_when_webhook_was_missed(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        provider.pay(attempt().session_id)
    response = app.test_cli_runner().invoke(args=["billing", "reconcile", "--limit", "10"])
    assert response.exit_code == 0, response.output
    with app.app_context():
        assert db.session.get(Registration, 1).status == "confirmed"


def test_unready_organizer_cannot_reserve_or_publish_paid_event(paid_app):
    app, provider = paid_app
    with app.app_context():
        db.session.get(ConnectedAccount, 1).configuration_verified = False
        db.session.commit()
        with pytest.raises(CheckoutNotAllowed):
            start()
        assert db.session.scalar(db.select(db.func.count()).select_from(CheckoutAttempt)) == 0
    client = logged_in(app, 1)
    response = client.post("/organizer/events/new", data=event_form())
    assert response.status_code == 422
    assert b"Finish payment setup" in response.data


def logged_in(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"], session["_fresh"] = str(user_id), True
        session["moderation_csrf"] = {"user": str(user_id), "token": "paid-token"}
    return client


def event_form(**changes):
    fields = dict(csrf_token="paid-token", title="New paid event", description="A campus event.",
                  category="Tech", location="Benson", start_time="2050-01-01T12:00",
                  end_time="2050-01-01T13:00", price="15", capacity="1")
    fields.update(changes)
    return fields


def test_checkout_post_uses_server_price_and_preserves_csrf(paid_app):
    app, provider = paid_app
    client = logged_in(app, 2)
    assert client.post("/registrations/1/checkout", data={"amount_cents": "1"}).status_code == 400
    response = client.post("/registrations/1/checkout", data={"csrf_token": "paid-token", "amount_cents": "1"})
    assert response.status_code == 303
    assert response.location.startswith("https://checkout.stripe.com/")
    assert provider.create_calls[0]["amount_cents"] == 1500


def test_organizer_cannot_reduce_capacity_below_live_checkout_holds(paid_app):
    app, provider = paid_app
    with app.app_context():
        db.session.get(Event, 1).capacity = 2
        db.session.commit()
        start(1)
        start(2)
    response = logged_in(app, 1).post("/organizer/events/1/edit", data=event_form(capacity="1"))
    assert response.status_code == 422
    assert b"2 seats are confirmed or held" in response.data
    with app.app_context():
        assert db.session.get(Event, 1).capacity == 2


def test_organizer_cancel_queues_refunds_in_same_transaction(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        process_session(provider.pay(attempt().session_id), "acct_organizer")
    response = logged_in(app, 1).post("/organizer/events/1/cancel", data={"csrf_token": "paid-token"})
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Event, 1).status == "cancelled"
        assert db.session.get(Registration, 1).status == "cancelled"
        assert attempt().status == "refund_pending"
        assert db.session.get(Payment, 1).status == "refund_pending"


def test_reconciliation_expires_abandoned_checkout(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        session_id = attempt().session_id
        attempt().expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        reconcile_pending()
        assert attempt().status == "expired"
        assert provider.sessions[session_id]["status"] == "expired"
        assert occupied_seats(1) == 0


@pytest.mark.parametrize("metadata", [[], {"attempt_id": str(2 ** 100)}, {"attempt_id": "-1"},
                                      {"attempt_id": "garbage"}, {"attempt_id": "1", "registration_id": "2"}])
def test_malformed_or_wrong_metadata_is_rejected_without_server_error(paid_app, metadata):
    app, provider = paid_app
    with app.app_context():
        start()
        session = provider.pay(attempt().session_id)
    session["metadata"] = metadata
    assert webhook(app, completed(session)).status_code == 400


def test_pending_refund_is_polled_until_completed(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        process_session(provider.pay(attempt().session_id), "acct_organizer")
        resolve_report(db.session.get(Report, 1), db.session.get(User, 4), "remove", "Removed")
        provider.pending_refund = True
        reconcile_pending()
        refund_id = attempt().refund_id
        assert refund_id
        assert attempt().status == "refund_pending"
        reconcile_pending()
        assert attempt().status == "refund_pending"
        provider.refund_objects[refund_id]["status"] = "succeeded"
        reconcile_pending()
        assert attempt().status == "refunded"
        assert len(provider.refunds) == 1


def test_stale_partial_refund_notification_cannot_undo_full_refund(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        session_id = attempt().session_id
        process_session(provider.pay(session_id), "acct_organizer")
        resolve_report(db.session.get(Report, 1), db.session.get(User, 4), "remove", "Removed")
        reconcile_pending()
        stale = {"payment_intent": f"pi_{session_id}", "amount": 1500, "amount_refunded": 100,
                 "currency": "usd", "livemode": False}
        process_refund(stale, "acct_organizer")
        assert attempt().status == "refunded"
        assert db.session.get(Payment, 1).status == "refunded"


def test_external_full_refund_is_recovered_without_issuing_another_refund(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        session_id = attempt().session_id
        process_session(provider.pay(session_id), "acct_organizer")
        resolve_report(db.session.get(Report, 1), db.session.get(User, 4), "remove", "Removed")
        provider.refund("acct_organizer", f"pi_{session_id}", "external-refund")
        provider.fail_refund = True
        reconcile_pending()
        assert attempt().status == "refunded"
        assert len(provider.refunds) == 1


def test_later_refund_failure_is_not_reported_as_completed(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        session_id = attempt().session_id
        process_session(provider.pay(session_id), "acct_organizer")
        resolve_report(db.session.get(Report, 1), db.session.get(User, 4), "remove", "Removed")
        reconcile_pending()
        refund_id = attempt().refund_id
        assert attempt().status == "refunded"
        provider.refund_objects[refund_id]["status"] = "failed"
        payload = {"id": "evt_failed", "object": "event", "type": "refund.failed", "livemode": False,
                   "account": "acct_organizer", "data": {"object": deepcopy(provider.refund_objects[refund_id])}}
    assert webhook(app, payload).status_code == 200
    with app.app_context():
        assert attempt().status == "refund_pending"
        assert "operator review" in attempt().last_error
        assert db.session.get(Payment, 1).status == "refund_pending"
        assert not db.session.get(Registration, 1).registration_view()["ticket_valid"]


def test_simultaneous_checkout_for_same_registration_keeps_one_attempt(paid_app):
    app, provider = paid_app
    ready = Barrier(2)
    def checkout():
        with app.app_context():
            ready.wait(timeout=5)
            return start()
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(checkout) for _ in range(2)]
        urls = [future.result(timeout=15) for future in futures]
    assert urls[0] == urls[1]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(CheckoutAttempt)) == 1


def test_simultaneous_completion_and_removal_leave_no_valid_ticket(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        session = provider.pay(attempt().session_id)
    ready = Barrier(2)
    def settle():
        with app.app_context():
            ready.wait(timeout=5)
            process_session(session, "acct_organizer")
    def remove():
        with app.app_context():
            report, admin = db.session.get(Report, 1), db.session.get(User, 4)
            ready.wait(timeout=5)
            resolve_report(report, admin, "remove", "Removed")
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(settle), workers.submit(remove)]
        for future in futures:
            future.result(timeout=15)
    with app.app_context():
        reconcile_pending()
        assert db.session.get(Event, 1).is_removed
        assert not db.session.get(Registration, 1).registration_view()["ticket_valid"]
        assert attempt().status == "refunded"


def test_reconciliation_limit_rotates_pending_attempts_fairly(paid_app):
    app, provider = paid_app
    with app.app_context():
        db.session.get(Event, 1).capacity = 2
        db.session.commit()
        start(1)
        first_id = attempt().id
        start(2)
        second = attempt()
        provider.pay(second.session_id)
        # First run sees the earliest open unpaid attempt; next must reach paid.
        reconcile_pending(limit=1)
        reconcile_pending(limit=1)
        assert db.session.get(Registration, 2).status == "confirmed"
        assert db.session.get(CheckoutAttempt, first_id).status == "open"


def test_external_partial_refund_notification_then_worker_finishes_remaining_balance(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        session_id = attempt().session_id
        process_session(provider.pay(session_id), "acct_organizer")
        external = {"id": "re_external_partial", "payment_intent": f"pi_{session_id}",
                    "amount": 500, "currency": "usd", "status": "succeeded"}
        provider.refund_objects[external["id"]] = external
        charge = provider.sessions[session_id]["payment_intent"]["latest_charge"]
        charge["amount_refunded"] = 500
        charge["refunds"]["data"] = [external]
        payload = {"id": "evt_partial", "object": "event", "type": "refund.updated", "livemode": False,
                   "account": "acct_organizer", "data": {"object": external}}
    assert webhook(app, payload).status_code == 200
    with app.app_context():
        assert attempt().status == "refund_pending"
        reconcile_pending()
        assert attempt().status == "refunded"
        assert db.session.get(Payment, 1).status == "refunded"
        refunds = provider.sessions[session_id]["payment_intent"]["latest_charge"]["refunds"]["data"]
        assert [refund["amount"] for refund in refunds] == [500, 1000]


def test_public_spots_include_live_holds_but_statistics_only_count_confirmed(paid_app):
    app, provider = paid_app
    with app.app_context():
        start()
        event = db.session.get(Event, 1)
        assert event.event_view()["spots_left"] == 0
        assert event.event_view()["registrations_count"] == 0
        attempt().expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        assert event.event_view()["spots_left"] == 1
        assert event.active_registration_count() == 0
