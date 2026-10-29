"""Published event discovery and the organizer's Pacific-time ordering."""

from datetime import datetime, timedelta

from app.extensions import db
from app.models import Event, User
from app.services.event_service import campus_now


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


def test_home_shows_real_unended_events_and_category_links(app, client, monkeypatch):
    now = datetime(2050, 10, 15, 12)
    monkeypatch.setattr("app.routes.events.campus_now", lambda: now)
    with app.app_context():
        owner = make_owner()
        make_event(owner, "Tomorrow on campus", now + timedelta(days=1),
                   now + timedelta(days=1, hours=2))
        current = make_event(owner, "Happening now", now - timedelta(hours=1),
                             now + timedelta(hours=1))
        make_event(owner, "Already finished", now - timedelta(hours=3),
                   now - timedelta(hours=1))
        make_event(owner, "Cancelled gathering", now + timedelta(hours=1),
                   now + timedelta(hours=2), status="cancelled")
        db.session.commit()
        current_id = current.id

    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert html.index("Happening now") < html.index("Tomorrow on campus")
    assert f'href="/events/{current_id}"' in html
    assert 'aria-label="Explore Happening now"' in html
    assert 'href="/events?category=Social"' in html
    assert "Already finished" not in html
    assert "Cancelled gathering" not in html
    assert "New campus events will appear here" not in html


def test_home_keeps_empty_state_when_there_are_no_events(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"New campus events will appear here" in response.data
    assert b'href="/events?category=Social"' in response.data


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
