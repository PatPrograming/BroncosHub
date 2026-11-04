"""The profile shows saved plans for its owner and protects account edits."""

from datetime import datetime, timedelta

import pytest
from flask import template_rendered

from app.extensions import db
from app.models import Event, Payment, Registration, User


NOW = datetime(2050, 11, 4, 12)


@pytest.fixture(autouse=True)
def profile_clock(monkeypatch):
    monkeypatch.setattr("app.routes.auth.campus_now", lambda: NOW)


def user(name, role="student"):
    row = User(name=name, email=f"{name}@scu.edu", google_sub=name, role=role)
    db.session.add(row)
    db.session.flush()
    return row


def register(student, owner, title, status="confirmed", price=0,
             event_status="published", start=None, end=None, payment_status=None):
    event = Event(
        organizer=owner, title=title, description="A campus event.",
        location="Mission Gardens", category="Social", price_cents=price,
        status=event_status, start_time=start or NOW + timedelta(days=1),
        end_time=end or NOW + timedelta(days=1, hours=2),
    )
    row = Registration(student=student, event=event, status=status)
    db.session.add(row)
    db.session.flush()
    if payment_status:
        db.session.add(Payment(
            registration=row, amount_cents=price, status=payment_status,
            provider_reference=f"profile-payment-{row.id}",
        ))
    return row


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def get_profile(app, client):
    contexts = []

    def capture(sender, template, context, **extra):
        contexts.append(context["profile_overview"])

    with template_rendered.connected_to(capture, app):
        response = client.get("/profile")
    assert response.status_code == 200
    return response, contexts[0]


def test_profile_without_registrations_shows_empty_state(app, client):
    with app.app_context():
        student = user("Alex")
        db.session.commit()
        sign_in(client, student.id)

    response, overview = get_profile(app, client)
    assert overview == {"upcoming_count": 0, "pending_count": 0, "next_registration": None}
    assert b"No upcoming events" in response.data
    assert b"Register for an event to see it here." in response.data
    assert b"name=\"csrf_token\"" in response.data


def test_profile_counts_confirmed_free_and_paid_plans_and_pending_payments(app, client):
    with app.app_context():
        student, owner = user("Alex"), user("Host", "organizer")
        free = register(student, owner, "Free picnic", start=NOW + timedelta(hours=1))
        register(student, owner, "Paid workshop", price=500, payment_status="succeeded")
        register(student, owner, "Pending workshop", status="pending", price=500,
                 payment_status="pending")
        register(student, owner, "Declined workshop", status="failed", price=500,
                 payment_status="declined")
        register(student, owner, "Cancelled signup", status="cancelled")
        # A free registration never needs payment, even if old data says pending.
        register(student, owner, "Unconfirmed free event", status="pending")
        db.session.commit()
        free_id = free.id
        sign_in(client, student.id)

    response, overview = get_profile(app, client)
    assert overview["upcoming_count"] == 2
    assert overview["pending_count"] == 1
    assert overview["next_registration"]["id"] == free_id
    assert f'href="/my-registrations/{free_id}"' in response.get_data(as_text=True)
    assert b"1 registration needs payment" in response.data


@pytest.mark.parametrize("event_status,end", [
    ("cancelled", NOW + timedelta(hours=2)),
    ("removed", NOW + timedelta(hours=2)),
    ("draft", NOW + timedelta(hours=2)),
    ("published", NOW),
    ("published", NOW - timedelta(seconds=1)),
])
def test_profile_excludes_closed_events_for_both_counts(app, client, event_status, end):
    with app.app_context():
        student, owner = user("Alex"), user("Host", "organizer")
        for status in ("confirmed", "pending"):
            register(student, owner, f"Hidden {status}", status=status, price=500,
                     event_status=event_status, start=NOW - timedelta(hours=1), end=end)
        db.session.commit()
        sign_in(client, student.id)

    _, overview = get_profile(app, client)
    assert overview == {"upcoming_count": 0, "pending_count": 0, "next_registration": None}


def test_profile_never_shows_another_students_plans(app, client):
    with app.app_context():
        student, other, owner = user("Alex"), user("Other"), user("Host", "organizer")
        own = register(student, owner, "Alex's plan")
        register(other, owner, "Someone else's plan", start=NOW + timedelta(minutes=1))
        register(other, owner, "Someone else's payment", status="pending", price=500)
        db.session.commit()
        own_id = own.id
        sign_in(client, student.id)

    response, overview = get_profile(app, client)
    assert overview["upcoming_count"] == 1
    assert overview["pending_count"] == 0
    assert overview["next_registration"]["id"] == own_id
    assert b"Someone else" not in response.data


def test_next_plan_uses_event_time_and_keeps_ongoing_confirmed_events(app, client):
    with app.app_context():
        student, owner = user("Alex"), user("Host", "organizer")
        register(student, owner, "Tomorrow")
        register(student, owner, "Earlier pending", status="pending", price=500,
                 start=NOW - timedelta(hours=2), end=NOW + timedelta(minutes=30))
        first = register(student, owner, "Happening now", start=NOW - timedelta(hours=1),
                         end=NOW + timedelta(hours=1))
        register(student, owner, "Same start, later registration", start=first.event.start_time)
        db.session.commit()
        first_id = first.id
        sign_in(client, student.id)

    _, overview = get_profile(app, client)
    assert overview["upcoming_count"] == 3
    assert overview["pending_count"] == 1
    assert overview["next_registration"]["id"] == first_id


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_profile_rejects_edits_without_a_valid_csrf_token(app, client, token):
    with app.app_context():
        student = user("Alex")
        db.session.commit()
        student_id = student.id
        sign_in(client, student_id)
    client.get("/profile")
    data = {"name": "Changed", "year_level": "third_year"}
    if token is not None:
        data["csrf_token"] = token

    assert client.post("/profile", data=data).status_code == 400
    with app.app_context():
        saved = db.session.get(User, student_id)
        assert saved.name == "Alex"
        assert saved.year_level is None


def test_profile_saves_valid_edits_only_for_the_signed_in_user(app, client):
    with app.app_context():
        student, other = user("Alex"), user("Other")
        db.session.commit()
        student_id, other_id = student.id, other.id
        sign_in(client, student_id)
    client.get("/profile")
    with client.session_transaction() as session:
        token = session["moderation_csrf"]["token"]

    response = client.post("/profile", data={
        "csrf_token": token, "name": "Alex Bronco", "year_level": "third_year",
    })
    assert response.status_code == 302
    assert response.location.endswith("/profile")
    with app.app_context():
        saved = db.session.get(User, student_id)
        assert (saved.name, saved.year_level) == ("Alex Bronco", "third_year")
        assert db.session.get(User, other_id).name == "Other"


def test_profile_rejects_a_token_issued_for_another_account(app, client):
    with app.app_context():
        student, other = user("Alex"), user("Other")
        db.session.commit()
        student_id, other_id = student.id, other.id
    sign_in(client, student_id)
    client.get("/profile")
    with client.session_transaction() as session:
        token = session["moderation_csrf"]["token"]
    sign_in(client, other_id)

    response = client.post("/profile", data={
        "csrf_token": token, "name": "Changed", "year_level": "third_year",
    })
    assert response.status_code == 400
    with app.app_context():
        assert db.session.get(User, other_id).name == "Other"
