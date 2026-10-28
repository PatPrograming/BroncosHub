"""Organizer statistics use saved registrations and respect event ownership."""

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from itertools import count

import pytest
from flask import template_rendered

from app.extensions import db
from app.models import Event, Registration, User
from app.services.stats_service import dashboard_statistics, event_statistics


@pytest.fixture
def records():
    """Small database factories; callers own the application context."""
    numbers = count(1)

    def user(role="student", year=None):
        number = next(numbers)
        row = User(
            name=f"Test Bronco {number}",
            email=f"test-{number}@scu.edu",
            google_sub=f"test-google-{number}",
            role=role,
            year_level=year,
        )
        db.session.add(row)
        db.session.flush()
        return row

    def event(owner, **overrides):
        number = next(numbers)
        values = dict(
            organizer=owner,
            title=f"Test campus event {number}",
            description="A fictional event used by the test suite.",
            location="Test campus room",
            category="Social",
            start_time=datetime(2050, 10, 15, 18),
            end_time=datetime(2050, 10, 15, 20),
            capacity=10,
            price_cents=0,
            status="published",
        )
        values.update(overrides)
        row = Event(**values)
        db.session.add(row)
        db.session.flush()
        return row

    def registration(event, student=None, status="confirmed", year=None):
        row = Registration(
            event=event, student=student or user(year=year), status=status
        )
        db.session.add(row)
        db.session.flush()
        return row

    return user, event, registration


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.urls = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.urls.extend(value for key, value in attrs if key == "href")


def snapshot():
    """Read every persisted field to detect accidental changes from GETs."""
    return {
        model.__tablename__: [
            tuple(getattr(row, column.name) for column in model.__table__.columns)
            for row in db.session.scalars(db.select(model).order_by(model.id))
        ]
        for model in (User, Event, Registration)
    }


def test_empty_statistics_have_zero_counts_and_no_fabricated_revenue(app, records):
    user, event, _ = records
    with app.app_context():
        empty = event(user(role="organizer"), price_cents=1500)
        statistics = event_statistics(empty)
        assert statistics["registrations"] == 0
        assert len(statistics["year_breakdown"]) == 6
        assert all(row["count"] == row["percent"] == 0 for row in statistics["year_breakdown"])
        for result in (statistics, dashboard_statistics([])):
            assert result["registrations"] == 0
            assert result["revenue_cents"] is None
            assert result["revenue_available"] is False
            assert result["revenue_label"] == "—"
        assert dashboard_statistics([])["events_count"] == 0


def test_only_confirmed_registrations_contribute_to_counts_and_years(app, records):
    user, event, register = records
    with app.app_context():
        item = event(user(role="organizer"), price_cents=2500)
        register(item, year="first_year")
        register(item, year="second_year")
        for status in ("pending", "cancelled", "failed"):
            register(item, status=status, year="graduate")
        statistics = event_statistics(item)
        assert statistics["registrations"] == 2
        years = {row["label"]: row for row in statistics["year_breakdown"]}
        assert years["First year"]["count"] == 1
        assert years["Second year"]["count"] == 1
        assert years["First year"]["percent"] == pytest.approx(50)
        assert years["Graduate"]["count"] == 0
        assert sum(row["count"] for row in years.values()) == 2
        assert statistics["revenue_cents"] is None
        assert statistics["revenue_available"] is False


def test_year_breakdown_includes_every_year_and_groups_missing_values(app, records):
    user, event, register = records
    with app.app_context():
        item = event(user(role="organizer"))
        for year in ("first_year", "second_year", "third_year", "fourth_year", "graduate"):
            register(item, year=year)
        for year in (None, "", "unknown", "unexpected"):
            register(item, year=year)
        statistics = event_statistics(item)
        years = {row["label"]: row for row in statistics["year_breakdown"]}
        assert set(years) == {"First year", "Second year", "Third year", "Fourth year", "Graduate", "Unknown"}
        assert statistics["registrations"] == 9
        assert years["Unknown"]["count"] == 4
        for label in set(years) - {"Unknown"}:
            assert years[label]["count"] == 1
        assert years["Unknown"]["percent"] == pytest.approx(400 / 9)
        assert sum(row["percent"] for row in years.values()) == pytest.approx(100)


def test_dashboard_counts_registrations_not_unique_students_and_keeps_history(app, records):
    user, event, register = records
    with app.app_context():
        owner = user(role="organizer")
        student = user(year="third_year")
        upcoming = event(owner, price_cents=2000)
        past = event(owner, start_time=datetime(2020, 1, 1), end_time=datetime(2020, 1, 1, 2))
        cancelled = event(owner, status="cancelled")
        for item in (upcoming, past, cancelled):
            register(item, student=student)
        register(upcoming, status="cancelled")
        statistics = dashboard_statistics([upcoming, past, cancelled])
        assert statistics["registrations"] == 3
        assert statistics["events_count"] == 2
        assert statistics["revenue_cents"] is None
        assert statistics["revenue_available"] is False


@pytest.mark.parametrize("path", ["/organizer/", "/organizer/events/1/stats"])
def test_guests_are_sent_to_login(client, path):
    response = client.get(path)
    assert response.status_code == 302
    assert "/login" in response.location


@pytest.mark.parametrize("path", ["/organizer/?role=organizer", "/organizer/events/1/stats?role=admin"])
def test_students_cannot_use_role_query_parameters_to_access_statistics(app, client, records, path):
    user, _, _ = records
    with app.app_context():
        student = user()
        db.session.commit()
        student_id = student.id
    sign_in(client, student_id)
    assert client.get(path).status_code == 403


@pytest.mark.parametrize("role", ["organizer", "admin"])
def test_dashboard_and_statistics_only_expose_owned_events(app, client, records, role):
    user, event, register = records
    with app.app_context():
        owner = user(role=role)
        owned = event(owner, title="My campus gathering")
        private = event(user(role="organizer"), title="Another organizers private event")
        register(owned)
        register(private)
        register(private)
        db.session.commit()
        owner_id, owned_id, private_id = owner.id, owned.id, private.id
    sign_in(client, owner_id)
    rendered = []

    def record_context(sender, template, context, **extra):
        rendered.append(context)

    with template_rendered.connected_to(record_context, app):
        dashboard = client.get("/organizer/")
    assert dashboard.status_code == 200
    assert b"My campus gathering" in dashboard.data
    assert b"Another organizers private event" not in dashboard.data
    assert rendered[-1]["stats"]["registrations"] == 1
    assert rendered[-1]["stats"]["events_count"] == 1
    with template_rendered.connected_to(record_context, app):
        statistics = client.get(f"/organizer/events/{owned_id}/stats")
    assert statistics.status_code == 200
    assert rendered[-1]["stats"]["registrations"] == 1
    forbidden = client.get(f"/organizer/events/{private_id}/stats")
    assert forbidden.status_code == 404
    assert b"Another organizers private event" not in forbidden.data
    assert client.get("/organizer/events/999999/stats").status_code == 404


@pytest.mark.parametrize("capacity", [None, 0, 2])
def test_capacity_edge_cases_render_without_dead_event_actions(app, client, records, capacity):
    user, event, register = records
    with app.app_context():
        owner = user(role="organizer")
        item = event(owner, capacity=capacity)
        register(item)
        register(item)
        db.session.commit()
        owner_id, event_id = owner.id, item.id
    sign_in(client, owner_id)
    for path in ("/organizer/", f"/organizer/events/{event_id}/stats"):
        response = client.get(path)
        assert response.status_code == 200
        assert b"None" not in response.data
        assert b"NaN" not in response.data
        links = Links(response.get_data(as_text=True)).urls
        assert f"/events/{event_id}" in links


def test_empty_dashboard_is_usable_without_unimplemented_create_action(app, client, records):
    user, _, _ = records
    with app.app_context():
        owner = user(role="organizer")
        db.session.commit()
        owner_id = owner.id
    sign_in(client, owner_id)
    response = client.get("/organizer/")
    assert response.status_code == 200
    assert "/organizer/events/new" in Links(response.get_data(as_text=True)).urls


def test_dashboard_lists_current_and_upcoming_events_before_recent_history(app, client, records):
    user, event, _ = records
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with app.app_context():
        owner = user(role="organizer")

        def dated(title, days, status="published"):
            start = now + timedelta(days=days)
            return event(owner, title=title, start_time=start,
                         end_time=start + timedelta(hours=2), status=status)

        dated("Older finished event", -4)
        dated("Later upcoming event", 3)
        dated("Recently finished event", -1)
        dated("Cancelled future event", 10, status="cancelled")
        dated("Nearest upcoming event", 1)
        event(owner, title="Happening right now", start_time=now - timedelta(hours=1),
              end_time=now + timedelta(hours=1))
        db.session.commit()
        owner_id = owner.id
    sign_in(client, owner_id)
    response = client.get("/organizer/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    expected = ["Happening right now", "Nearest upcoming event", "Later upcoming event",
                "Cancelled future event", "Recently finished event", "Older finished event"]
    positions = [html.index(title) for title in expected]
    assert positions == sorted(positions)


def test_reading_dashboard_and_statistics_does_not_modify_saved_data(app, client, records):
    user, event, register = records
    with app.app_context():
        owner = user(role="organizer")
        item = event(owner, status="cancelled", price_cents=1200)
        register(item, year="fourth_year")
        register(item, status="pending", year="graduate")
        db.session.commit()
        owner_id, event_id = owner.id, item.id
        before = snapshot()
    sign_in(client, owner_id)
    assert client.get("/organizer/").status_code == 200
    assert client.get(f"/organizer/events/{event_id}/stats").status_code == 200
    with app.app_context():
        assert snapshot() == before
