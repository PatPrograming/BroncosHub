# tests/test_registrations.py
# Tests for registrations. UC-002, UC-004, UC-005.
from datetime import datetime

import pytest

from app.extensions import db
from app.models import Event, Registration, User


@pytest.fixture
def setup(app):
    with app.app_context():
        organizer = User(name="Olive Organizer", email="olive@scu.edu", google_sub="g-olive", role="organizer")
        sam = User(name="Sam Student", email="sam@scu.edu", google_sub="g-sam")
        ana = User(name="Ana Student", email="ana@scu.edu", google_sub="g-ana")
        db.session.add_all([organizer, sam, ana])
        db.session.flush()
        free = Event(
            organizer=organizer, title="Free Study Break", location="Benson", category="Wellness",
            start_time=datetime(2050, 10, 15, 18), end_time=datetime(2050, 10, 15, 20),
            capacity=1, price_cents=0,
        )
        paid = Event(
            organizer=organizer, title="Paid Concert", location="Mission Church", category="Arts & Culture",
            start_time=datetime(2050, 10, 16, 18), end_time=datetime(2050, 10, 16, 20),
            capacity=50, price_cents=1500,
        )
        db.session.add_all([free, paid])
        db.session.commit()
        return {"sam": sam.id, "ana": ana.id, "free": free.id, "paid": paid.id}


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["moderation_csrf"] = {"user": str(user_id), "token": "test-token"}


def register(client, event_id):
    return client.post(f"/events/{event_id}/register", data={"csrf_token": "test-token"})


def registrations_for(event_id):
    return db.session.scalars(db.select(Registration).where(Registration.event_id == event_id)).all()


def test_registering_for_a_free_event_confirms_it(app, client, setup):
    sign_in(client, setup["sam"])
    response = register(client, setup["free"])
    assert response.status_code == 302
    with app.app_context():
        [registration] = registrations_for(setup["free"])
        assert registration.status == "confirmed"


def test_registering_for_a_paid_event_goes_to_checkout(app, client, setup):
    sign_in(client, setup["sam"])
    response = register(client, setup["paid"])
    assert "/checkout" in response.headers["Location"]
    with app.app_context():
        [registration] = registrations_for(setup["paid"])
        assert registration.status == "pending"


def test_registering_twice_returns_the_existing_registration(app, client, setup):
    sign_in(client, setup["sam"])
    first = register(client, setup["paid"]).headers["Location"]
    second = register(client, setup["paid"]).headers["Location"]
    assert first == second
    with app.app_context():
        assert len(registrations_for(setup["paid"])) == 1


def test_registering_for_a_full_event_is_refused(app, client, setup):
    sign_in(client, setup["sam"])
    register(client, setup["free"])  # capacity is 1, so Sam takes the only spot
    sign_in(client, setup["ana"])
    register(client, setup["free"])
    with app.app_context():
        students = [r.student_id for r in registrations_for(setup["free"])]
        assert students == [setup["sam"]]


def test_a_free_registration_can_be_cancelled(app, client, setup):
    sign_in(client, setup["sam"])
    register(client, setup["free"])
    with app.app_context():
        registration_id = registrations_for(setup["free"])[0].id
    client.post(f"/my-registrations/{registration_id}/cancel", data={"csrf_token": "test-token"})
    with app.app_context():
        assert db.session.get(Registration, registration_id).status == "cancelled"


def test_a_student_cant_view_another_students_registration(app, client, setup):
    sign_in(client, setup["sam"])
    register(client, setup["free"])
    with app.app_context():
        registration_id = registrations_for(setup["free"])[0].id
    sign_in(client, setup["ana"])
    assert client.get(f"/my-registrations/{registration_id}").status_code == 404


def test_my_registrations_lists_only_my_events(client, setup):
    sign_in(client, setup["sam"])
    register(client, setup["free"])
    page = client.get("/my-registrations").get_data(as_text=True)
    assert "Free Study Break" in page and "Paid Concert" not in page
