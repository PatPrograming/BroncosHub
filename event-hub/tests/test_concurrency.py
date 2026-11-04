"""Competing requests use separate sessions against a real file-backed database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import sqlite3
from threading import Barrier, Event as Signal, Lock

import pytest

from app import create_app
from app.extensions import db
from app.models import Event, Payment, Registration, Report, Ticket, User
from app.routes import organizer as organizer_routes
from app.services import payment_service
from app.services.moderation_service import resolve_report
from app.services.registration_service import RegistrationRefused, register_student
from config import TestConfig


@pytest.fixture
def concurrent_app(tmp_path):
    class ConcurrentConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'concurrency.db'}"
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 10, "check_same_thread": False}}
        COVER_UPLOAD_DIR = str(tmp_path / "covers")

    app = create_app(ConcurrentConfig)
    with app.app_context():
        db.session.add_all([
            User(id=index, name=f"Concurrent {index}", email=f"concurrent-{index}@scu.edu",
                 google_sub=f"concurrent-{index}", role=role)
            for index, role in enumerate(("organizer", "student", "student", "admin"), 1)
        ])
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def seed(app, *, price=0, capacity=1, pending=False):
    with app.app_context():
        event = Event(organizer_id=1, title="Concurrent event", description="A campus event.",
                      category="Tech", location="Benson", status="published", price_cents=price,
                      capacity=capacity, start_time=datetime(2050, 1, 1, 12), end_time=datetime(2050, 1, 1, 13))
        db.session.add(event)
        db.session.flush()
        report = Report(event_id=event.id, reporter_id=2, reason="misleading",
                        details="Please review this event.", status="pending")
        db.session.add(report)
        if pending:
            db.session.add_all([Registration(event_id=event.id, student_id=user, status="pending") for user in (2, 3)])
        db.session.commit()
        return event.id, report.id


def browser(app, user):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user)
        session["_fresh"] = True
        session["moderation_csrf"] = {"user": str(user), "token": "race-token"}
    return client


def edit_form(**changes):
    fields = dict(csrf_token="race-token", title="Edited event", description="A campus event.",
                  category="Tech", location="Benson", start_time="2050-01-01T12:00",
                  end_time="2050-01-01T13:00", price="0", capacity="1")
    fields.update(changes)
    return fields


def race(*actions):
    with ThreadPoolExecutor(max_workers=len(actions)) as workers:
        futures = [workers.submit(action) for action in actions]
        return [future.result(timeout=15) for future in futures]


def test_different_students_compete_for_one_free_seat_with_stale_cached_counts(concurrent_app):
    event_id, _ = seed(concurrent_app)
    ready = Barrier(2)
    connections = []

    def signup(user_id):
        with concurrent_app.app_context():
            user, event = db.session.get(User, user_id), db.session.get(Event, event_id)
            assert event.active_registration_count() == 0
            connections.append(id(db.session.connection().connection.driver_connection))
            ready.wait(timeout=5)
            try:
                return register_student(user, event).status
            except RegistrationRefused:
                return "full"

    assert sorted(race(lambda: signup(2), lambda: signup(3))) == ["confirmed", "full"]
    assert len(set(connections)) == 2
    with concurrent_app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Registration)) == 1
        assert db.session.get(Event, event_id).active_registration_count() == 1


def test_simultaneous_duplicate_signup_reuses_one_registration(concurrent_app):
    event_id, _ = seed(concurrent_app)
    ready = Barrier(2)

    def signup():
        with concurrent_app.app_context():
            user, event = db.session.get(User, 2), db.session.get(Event, event_id)
            ready.wait(timeout=5)
            return register_student(user, event).id

    ids = race(signup, signup)
    assert ids[0] == ids[1]
    with concurrent_app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Registration)) == 1


def test_two_paid_checkouts_compete_for_last_seat_before_any_second_charge(concurrent_app, monkeypatch):
    event_id, _ = seed(concurrent_app, price=1500, pending=True)
    ready, calls_lock = Barrier(2), Lock()
    calls = []
    original = payment_service.MockPaymentService.charge

    def charged(service, amount, card):
        with calls_lock:
            calls.append(amount)
        return original(service, amount, card)

    monkeypatch.setattr(payment_service.MockPaymentService, "charge", charged)

    def checkout(user_id):
        with concurrent_app.app_context():
            registration = db.session.scalar(db.select(Registration).where(Registration.student_id == user_id))
            assert registration.payment is None
            assert registration.event.active_registration_count() == 0
            ready.wait(timeout=5)
            try:
                return payment_service.complete_purchase(registration, "4242424242424242").status
            except payment_service.CheckoutNotAllowed:
                return "full"

    assert sorted(race(lambda: checkout(2), lambda: checkout(3))) == ["confirmed", "full"]
    assert calls == [1500]
    with concurrent_app.app_context():
        assert db.session.get(Event, event_id).active_registration_count() == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(Payment)) == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 1


def test_two_submissions_of_same_checkout_charge_once(concurrent_app, monkeypatch):
    seed(concurrent_app, price=1500, pending=True)
    ready = Barrier(2)
    calls = []
    original = payment_service.MockPaymentService.charge

    def charged(service, amount, card):
        calls.append(amount)
        return original(service, amount, card)

    monkeypatch.setattr(payment_service.MockPaymentService, "charge", charged)

    def checkout():
        with concurrent_app.app_context():
            registration = db.session.scalar(db.select(Registration).where(Registration.student_id == 2))
            assert registration.payment is None
            ready.wait(timeout=5)
            return payment_service.complete_purchase(registration, "4242").id

    ids = race(checkout, checkout)
    assert ids[0] == ids[1] and calls == [1500]
    with concurrent_app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Payment)) == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 1


@pytest.mark.parametrize("paid", [False, True])
def test_removal_racing_signup_or_checkout_leaves_no_active_registration(concurrent_app, paid):
    event_id, report_id = seed(concurrent_app, price=1500 if paid else 0, pending=paid)
    ready = Barrier(2)

    def attend():
        with concurrent_app.app_context():
            event, user = db.session.get(Event, event_id), db.session.get(User, 2)
            registration = db.session.scalar(db.select(Registration).where(Registration.student_id == 2)) if paid else None
            if registration is not None:
                assert registration.payment is None
            ready.wait(timeout=5)
            try:
                if paid:
                    payment_service.complete_purchase(registration, "4242")
                else:
                    register_student(user, event)
                return "accepted before removal"
            except (RegistrationRefused, payment_service.CheckoutNotAllowed):
                return "closed"

    def remove():
        with concurrent_app.app_context():
            report, reviewer = db.session.get(Report, report_id), db.session.get(User, 4)
            ready.wait(timeout=5)
            return resolve_report(report, reviewer, "remove", "Remove the reported event.")

    outcome, removed = race(attend, remove)
    assert removed and outcome in ("closed", "accepted before removal")
    with concurrent_app.app_context():
        assert db.session.get(Event, event_id).status == "removed"
        assert db.session.get(Event, event_id).active_registration_count() == 0
        for row in db.session.scalars(db.select(Registration)):
            assert row.status == "cancelled"
            assert row.registration_view()["ticket_valid"] is False


def test_organizer_cancellation_racing_signup_closes_every_registration(concurrent_app):
    event_id, _ = seed(concurrent_app)
    ready = Barrier(2)

    def signup():
        with concurrent_app.app_context():
            user, event = db.session.get(User, 2), db.session.get(Event, event_id)
            ready.wait(timeout=5)
            try:
                register_student(user, event)
                return "accepted before cancellation"
            except RegistrationRefused:
                return "closed"

    def cancel():
        client = browser(concurrent_app, 1)
        ready.wait(timeout=5)
        return client.post(f"/organizer/events/{event_id}/cancel", data={"csrf_token": "race-token"}).status_code

    outcome, status = race(signup, cancel)
    assert status == 302 and outcome in ("closed", "accepted before cancellation")
    with concurrent_app.app_context():
        assert db.session.get(Event, event_id).status == "cancelled"
        assert db.session.get(Event, event_id).active_registration_count() == 0


def test_capacity_reduction_racing_signup_never_goes_below_confirmed_count(concurrent_app):
    event_id, _ = seed(concurrent_app, capacity=2)
    with concurrent_app.app_context():
        register_student(db.session.get(User, 2), db.session.get(Event, event_id))
    ready = Barrier(2)

    def signup():
        with concurrent_app.app_context():
            user, event = db.session.get(User, 3), db.session.get(Event, event_id)
            assert event.active_registration_count() == 1
            ready.wait(timeout=5)
            try:
                register_student(user, event)
                return "confirmed"
            except RegistrationRefused:
                return "full"

    def reduce_capacity():
        client = browser(concurrent_app, 1)
        ready.wait(timeout=5)
        return client.post(f"/organizer/events/{event_id}/edit", data=edit_form(capacity="1")).status_code

    signup_result, edit_status = race(signup, reduce_capacity)
    assert (signup_result, edit_status) in (("confirmed", 422), ("full", 302))
    with concurrent_app.app_context():
        event = db.session.get(Event, event_id)
        assert event.active_registration_count() == event.capacity


def test_edit_loaded_before_moderation_refreshes_state_before_saving(concurrent_app, monkeypatch):
    event_id, report_id = seed(concurrent_app)
    edit_loaded, resume_edit = Signal(), Signal()
    original = organizer_routes._cover_input

    def wait_during_upload_validation(errors):
        result = original(errors)
        edit_loaded.set()
        assert resume_edit.wait(timeout=10)
        return result

    monkeypatch.setattr(organizer_routes, "_cover_input", wait_during_upload_validation)
    client = browser(concurrent_app, 1)
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(client.post, f"/organizer/events/{event_id}/edit", data=edit_form())
        assert edit_loaded.wait(timeout=5)
        try:
            with concurrent_app.app_context():
                assert resolve_report(db.session.get(Report, report_id), db.session.get(User, 4),
                                      "remove", "Removed while editing was open.")
        finally:
            resume_edit.set()
        assert pending.result(timeout=10).status_code == 409
    with concurrent_app.app_context():
        event = db.session.get(Event, event_id)
        assert event.status == "removed" and event.title == "Concurrent event"


def test_failed_purchase_releases_lock_without_partial_payment_and_can_retry(concurrent_app, monkeypatch):
    seed(concurrent_app, price=1500, pending=True)
    original_commit = db.session.commit

    def failed_commit():
        raise RuntimeError("Simulated save failure")

    with concurrent_app.app_context():
        registration = db.session.scalar(db.select(Registration).where(Registration.student_id == 2))
        monkeypatch.setattr(db.session, "commit", failed_commit)
        with pytest.raises(RuntimeError, match="Simulated save failure"):
            payment_service.complete_purchase(registration, "4242")
        monkeypatch.setattr(db.session, "commit", original_commit)
        assert db.session.scalar(db.select(db.func.count()).select_from(Payment)) == 0
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 0
        assert db.session.get(Registration, registration.id).status == "pending"
    # A genuinely separate session can acquire the released lock and finish.
    def retry():
        with concurrent_app.app_context():
            registration = db.session.scalar(db.select(Registration).where(Registration.student_id == 2))
            return payment_service.complete_purchase(registration, "4242").status
    assert race(retry) == ["confirmed"]


def test_cancel_and_moderation_remove_cannot_resurrect_a_removed_event(concurrent_app):
    event_id, report_id = seed(concurrent_app)
    with concurrent_app.app_context():
        register_student(db.session.get(User, 2), db.session.get(Event, event_id))
    ready = Barrier(2)

    def remove():
        with concurrent_app.app_context():
            report, reviewer = db.session.get(Report, report_id), db.session.get(User, 4)
            ready.wait(timeout=5)
            return resolve_report(report, reviewer, "remove", "Remove this event from campus listings.")

    def cancel():
        client = browser(concurrent_app, 1)
        ready.wait(timeout=5)
        return client.post(f"/organizer/events/{event_id}/cancel", data={"csrf_token": "race-token"}).status_code

    assert race(remove, cancel) == [True, 302]
    with concurrent_app.app_context():
        assert db.session.get(Event, event_id).status == "removed"
        assert db.session.get(Report, report_id).status == "removed"
        assert db.session.get(Event, event_id).active_registration_count() == 0


def test_real_sqlite_lock_timeout_returns_retryable_response_without_signup(concurrent_app):
    event_id, _ = seed(concurrent_app)
    client = browser(concurrent_app, 2)
    with concurrent_app.app_context():
        database_path = db.engine.url.database
        # Keep the test fast while exercising an actual second writer, not a mock exception.
        db.session.connection().exec_driver_sql("PRAGMA busy_timeout = 50")
        db.session.commit()
    blocker = sqlite3.connect(database_path)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        response = client.post(f"/events/{event_id}/register", data={"csrf_token": "race-token"})
        assert response.status_code == 503
        assert b"Please wait a moment" in response.data
    finally:
        blocker.rollback()
        blocker.close()
    with concurrent_app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Registration)) == 0
    retry = client.post(f"/events/{event_id}/register", data={"csrf_token": "race-token"})
    assert retry.status_code == 302
    with concurrent_app.app_context():
        assert db.session.get(Event, event_id).active_registration_count() == 1
