# tests/test_events.py
# Tests for events. UC-001, UC-006, UC-007.
from datetime import datetime

import pytest

from app.extensions import db
from app.models import Event, User


@pytest.fixture
def people(app):
    with app.app_context():
        organizer = User(name="Olive Organizer", email="olive@scu.edu", google_sub="g-olive", role="organizer")
        other = User(name="Otto Organizer", email="otto@scu.edu", google_sub="g-otto", role="organizer")
        student = User(name="Sam Student", email="sam@scu.edu", google_sub="g-sam")
        db.session.add_all([organizer, other, student])
        db.session.commit()
        return {"organizer": organizer.id, "other": other.id, "student": student.id}


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["moderation_csrf"] = {"user": str(user_id), "token": "test-token"}


def make_event(organizer_id, **overrides):
    values = dict(
        organizer_id=organizer_id, title="Sunset Cinema", description="A movie on the lawn.",
        location="Mission Gardens", category="Social",
        start_time=datetime(2050, 10, 15, 18), end_time=datetime(2050, 10, 15, 20),
        capacity=10, price_cents=0, status="published",
    )
    values.update(overrides)
    event = Event(**values)
    db.session.add(event)
    db.session.commit()
    return event.id


def form(**overrides):
    values = dict(
        csrf_token="test-token", title="Build & Brew", description="Coffee and side projects.",
        category="Tech", location="Learning Commons", start_time="2050-11-01T17:00",
        end_time="2050-11-01T19:00", price="0", capacity="25",
    )
    values.update(overrides)
    return values


def test_creating_an_event_publishes_it(app, client, people):
    sign_in(client, people["organizer"])
    response = client.post("/organizer/events/new", data=form())
    assert response.status_code == 302
    with app.app_context():
        event = db.session.scalar(db.select(Event))
        assert event.title == "Build & Brew"
        assert event.organizer_id == people["organizer"]
        assert event.status == "published"


def test_creating_an_event_rejects_a_past_start_time(app, client, people):
    sign_in(client, people["organizer"])
    response = client.post("/organizer/events/new", data=form(start_time="2020-01-01T10:00"))
    assert response.status_code == 422
    assert b"must be in the future" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(Event)) is None


def test_creating_an_event_rejects_a_negative_price(app, client, people):
    sign_in(client, people["organizer"])
    response = client.post("/organizer/events/new", data=form(price="-5"))
    assert response.status_code == 422
    with app.app_context():
        assert db.session.scalar(db.select(Event)) is None


def test_students_cannot_create_events(client, people):
    sign_in(client, people["student"])
    assert client.post("/organizer/events/new", data=form()).status_code == 403


def test_a_cancelled_event_hides_the_register_button(app, client, people):
    with app.app_context():
        event_id = make_event(people["organizer"], status="cancelled")
    sign_in(client, people["student"])
    page = client.get(f"/events/{event_id}").get_data(as_text=True)
    assert "Event cancelled" in page
    assert f"/events/{event_id}/register" not in page


def test_only_the_owner_can_edit_an_event(app, client, people):
    with app.app_context():
        event_id = make_event(people["organizer"])
    sign_in(client, people["other"])
    response = client.post(f"/organizer/events/{event_id}/edit", data=form(title="Hijacked"))
    assert response.status_code == 404
    with app.app_context():
        assert db.session.get(Event, event_id).title == "Sunset Cinema"


def test_browsing_lists_published_events_and_filters_by_category(app, client, people):
    with app.app_context():
        make_event(people["organizer"], title="Pickup Basketball", category="Sports")
        make_event(people["organizer"], title="Hack Night", category="Tech")
        make_event(people["organizer"], title="Called Off", category="Tech", status="cancelled")
    everything = client.get("/events").get_data(as_text=True)
    assert "Pickup Basketball" in everything and "Hack Night" in everything
    assert "Called Off" not in everything
    tech = client.get("/events?category=Tech").get_data(as_text=True)
    assert "Hack Night" in tech and "Pickup Basketball" not in tech


def test_browsing_shows_a_message_when_nothing_matches(client, people):
    page = client.get("/events?q=nothing-like-this").get_data(as_text=True)
    assert "No plans found" in page
