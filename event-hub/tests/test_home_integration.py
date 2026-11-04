"""The home dashboard's campus-day boundaries and private registration summary."""

from datetime import datetime, timedelta

import pytest
from flask import template_rendered

from app.extensions import db
from app.models import Event, Registration, User
from app.services.event_service import campus_now


HOME_NOW = datetime(2026, 11, 4, 12)


@pytest.fixture
def home_clock(monkeypatch):
    monkeypatch.setattr("app.services.home_service.campus_now", lambda: HOME_NOW)
    monkeypatch.setattr("app.models.registration.campus_now", lambda: HOME_NOW)
    return HOME_NOW


def make_owner():
    owner = User(
        name="Campus Organizer", email="home-organizer@scu.edu",
        google_sub="home-organizer", role="organizer",
    )
    db.session.add(owner)
    db.session.flush()
    return owner


def make_event(owner, title, start, end, status="published"):
    event = Event(
        organizer=owner, title=title, description="A campus gathering.",
        location="Mission Gardens", category="Social", start_time=start,
        end_time=end, status=status, capacity=20, price_cents=0,
    )
    db.session.add(event)
    db.session.flush()
    return event


def home_page(app, client):
    contexts = []

    def capture(sender, template, context, **extra):
        if template.name == "index.html":
            contexts.append(context)

    with template_rendered.connected_to(capture, app):
        response = client.get("/")
    assert response.status_code == 200
    assert len(contexts) == 1
    return response.get_data(as_text=True), contexts[0]


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def make_student(name):
    student = User(name=name, email=f"{name}@scu.edu", google_sub=name, role="student")
    db.session.add(student)
    db.session.flush()
    return student


def test_home_today_includes_ongoing_events_and_excludes_closed_or_other_days(app, client, home_clock):
    now = home_clock
    with app.app_context():
        owner = make_owner()
        overnight = make_event(owner, "Overnight gathering", now - timedelta(days=1),
                               now + timedelta(hours=1))
        started = make_event(owner, "Starting now", now, now + timedelta(hours=2))
        later = make_event(owner, "Later today", now + timedelta(hours=2),
                           now + timedelta(hours=3))
        tomorrow = now.replace(hour=0) + timedelta(days=1)
        make_event(owner, "Tomorrow on campus", tomorrow, tomorrow + timedelta(hours=1))
        make_event(owner, "Already finished", now - timedelta(hours=1), now)
        for status in ("cancelled", "removed", "draft"):
            make_event(owner, f"Hidden {status}", now, now + timedelta(hours=1), status=status)
        db.session.commit()
        expected_ids = [overnight.id, started.id, later.id]

    html, context = home_page(app, client)
    assert context["home_today_label"] == "Wednesday, November 4"
    assert context["active_nav"] == "home"
    assert [event["id"] for event in context["today_events"]] == expected_ids
    assert [event["ongoing"] for event in context["today_events"]] == [True, True, False]
    assert context["today_event_count"] == 3
    assert context["next_campus_event"] is None
    assert context["next_registration"] is None
    for event_id in expected_ids:
        assert f'href="/events/{event_id}"' in html
    assert "Already finished" not in html
    assert "Tomorrow on campus" not in html
    assert all(f"Hidden {status}" not in html for status in ("cancelled", "removed", "draft"))
    assert "category-links" not in html


def test_home_limits_today_to_four_but_counts_all_and_orders_start_then_id(app, client, home_clock):
    with app.app_context():
        owner = make_owner()
        events = [
            make_event(owner, f"Today {index}", home_clock + timedelta(hours=offset),
                       home_clock + timedelta(hours=offset + 1))
            for index, offset in enumerate((5, 1, 3, 1, 4, 2))
        ]
        db.session.commit()
        expected_ids = [event.id for event in sorted(events, key=lambda event: (event.start_time, event.id))[:4]]

    _, context = home_page(app, client)
    assert context["today_event_count"] == 6
    assert [event["id"] for event in context["today_events"]] == expected_ids
    assert len(context["today_events"]) == 4
    assert context["next_campus_event"] is None


def test_home_uses_one_pacific_snapshot_across_a_year_boundary(app, client, monkeypatch):
    now = datetime(2026, 12, 31, 23, 59, 59)
    calls = []

    def clock():
        calls.append(True)
        return now if len(calls) == 1 else now + timedelta(seconds=1)

    monkeypatch.setattr("app.services.home_service.campus_now", clock)
    with app.app_context():
        owner = make_owner()
        tonight = make_event(owner, "Year-end event", now - timedelta(minutes=1),
                             now + timedelta(minutes=30))
        make_event(owner, "New-year event", now + timedelta(seconds=1), now + timedelta(hours=1))
        db.session.commit()
        tonight_id = tonight.id

    _, context = home_page(app, client)
    assert len(calls) == 1
    assert context["home_today_label"] == "Thursday, December 31"
    assert context["today_event_count"] == 1
    assert [event["id"] for event in context["today_events"]] == [tonight_id]
    assert context["today_events"][0]["ongoing"] is True


def test_home_without_today_events_shows_the_earliest_available_future_event(app, client, home_clock):
    tomorrow = home_clock.replace(hour=0) + timedelta(days=1)
    with app.app_context():
        owner = make_owner()
        make_event(owner, "Past event", home_clock - timedelta(hours=2), home_clock)
        make_event(owner, "Cancelled today", home_clock, tomorrow, status="cancelled")
        make_event(owner, "Removed tomorrow", tomorrow, tomorrow + timedelta(hours=1), status="removed")
        make_event(owner, "Later next week", tomorrow + timedelta(days=7), tomorrow + timedelta(days=8))
        first = make_event(owner, "First tomorrow", tomorrow, tomorrow + timedelta(hours=1))
        make_event(owner, "Same-time later ID", tomorrow, tomorrow + timedelta(hours=1))
        db.session.commit()
        first_id = first.id

    html, context = home_page(app, client)
    assert context["today_events"] == []
    assert context["today_event_count"] == 0
    assert context["next_campus_event"]["id"] == first_id
    assert f'href="/events/{first_id}"' in html
    assert "First tomorrow" in html
    assert "Removed tomorrow" not in html


def test_home_empty_state_has_no_event_or_registration_data(app, client, home_clock):
    html, context = home_page(app, client)
    assert context["today_events"] == []
    assert context["today_event_count"] == 0
    assert context["next_campus_event"] is None
    assert context["next_registration"] is None
    assert 'href="/events"' in html


def test_home_next_registration_is_owned_confirmed_published_and_unended(app, client, home_clock):
    with app.app_context():
        owner, student, other = make_owner(), make_student("Alex"), make_student("Other")

        def registered(title, status="confirmed", event_status="published", end=None, start=None, attendee=None):
            event = make_event(owner, title, start or home_clock - timedelta(hours=2),
                               end or home_clock + timedelta(hours=1), status=event_status)
            registration = Registration(student=attendee or student, event=event, status=status)
            db.session.add(registration)
            db.session.flush()
            return registration

        registered("Someone else's registration", attendee=other)
        for status in ("pending", "cancelled", "failed"):
            registered(f"Registration {status}", status=status)
        for status in ("cancelled", "removed", "draft"):
            registered(f"Event {status}", event_status=status)
        registered("Ended at the boundary", end=home_clock)
        expected = registered("My ongoing event", start=home_clock - timedelta(hours=1))
        registered("Same-time later registration", start=home_clock - timedelta(hours=1))
        registered("My future event", start=home_clock + timedelta(days=1), end=home_clock + timedelta(days=2))
        db.session.commit()
        student_id, expected_id = student.id, expected.id

    sign_in(client, student_id)
    html, context = home_page(app, client)
    assert context["next_registration"]["id"] == expected_id
    assert context["next_registration"]["status"] == "confirmed"
    assert context["next_registration"]["event"]["title"] == "My ongoing event"
    assert context["next_registration"]["ended"] is False
    assert f'href="/my-registrations/{expected_id}"' in html


def test_home_does_not_share_registration_data_between_guests_or_accounts(app, client, home_clock):
    with app.app_context():
        owner, student, other = make_owner(), make_student("Alex"), make_student("Other")
        event = make_event(owner, "Public campus event", home_clock, home_clock + timedelta(hours=1))
        registration = Registration(student=student, event=event, status="confirmed")
        db.session.add(registration)
        db.session.commit()
        student_id, other_id, registration_id = student.id, other.id, registration.id

    guest_html, guest_context = home_page(app, client)
    assert guest_context["next_registration"] is None
    assert f'href="/my-registrations/{registration_id}"' not in guest_html
    sign_in(client, student_id)
    _, owner_context = home_page(app, client)
    assert owner_context["next_registration"]["id"] == registration_id
    sign_in(client, other_id)
    other_html, other_context = home_page(app, client)
    assert other_context["next_registration"] is None
    assert f'href="/my-registrations/{registration_id}"' not in other_html


def test_dashboard_orders_later_today_before_tomorrow_in_pacific_time(app, client):
    # A real local-time event ending two hours from now must not be classified
    # as past by comparing its naive Pacific timestamp against naive UTC.
    now = campus_now()
    with app.app_context():
        owner = make_owner()
        make_event(owner, "Tomorrow's gathering", now + timedelta(days=1),
                   now + timedelta(days=1, hours=2))
        make_event(owner, "Coming up in one hour", now + timedelta(hours=1),
                   now + timedelta(hours=2))
        make_event(owner, "Yesterday's gathering", now - timedelta(days=1),
                   now - timedelta(hours=22))
        db.session.commit()
        owner_id = owner.id

    with client.session_transaction() as session:
        session["_user_id"] = str(owner_id)
        session["_fresh"] = True
    response = client.get("/organizer/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert html.index("Coming up in one hour") < html.index("Tomorrow&#39;s gathering")
    assert html.index("Tomorrow&#39;s gathering") < html.index("Yesterday&#39;s gathering")
