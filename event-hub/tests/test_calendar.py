"""Real event discovery, calendar links, and campus-date boundaries."""

from contextlib import contextmanager
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import template_rendered

from app.extensions import db
from app.models import Event, User
from app.routes import events as event_routes


@contextmanager
def rendered(app):
    contexts = []

    def record(sender, template, context, **extra):
        contexts.append(context)

    template_rendered.connect(record, app)
    try:
        yield contexts
    finally:
        template_rendered.disconnect(record, app)


@pytest.fixture
def calendar_events(app, monkeypatch):
    monkeypatch.setattr(event_routes, "campus_now", lambda: datetime(2026, 11, 4, 9))
    with app.app_context():
        organizer = User(name="Calendar Organizer", email="calendar@scu.edu", google_sub="calendar-owner", role="organizer")
        db.session.add(organizer)
        db.session.commit()
        owner = organizer.id

    def add(title, start="2026-11-05T12:00", end="2026-11-05T13:00", **changes):
        with app.app_context():
            values = dict(organizer_id=owner, title=title, description="Campus gathering",
                          category="Tech", location="Benson", start_time=datetime.fromisoformat(start),
                          end_time=datetime.fromisoformat(end), price_cents=0, capacity=20, status="published")
            values.update(changes)
            event = Event(**values)
            db.session.add(event)
            db.session.commit()
            return event.id
    return add


def page(app, client, url="/events"):
    with rendered(app) as contexts:
        response = client.get(url)
    assert response.status_code == 200
    return response.get_data(as_text=True), contexts[-1]


def dates(context):
    return {day["iso"]: day for day in context["campus_days"]}


def params(url):
    return parse_qs(urlsplit(url).query)


def test_initial_calendar_counts_real_published_events_without_expired_or_removed_records(app, client, calendar_events):
    calendar_events("Tomorrow")
    calendar_events("Ongoing", "2026-11-03T20:00", "2026-11-04T11:00")
    calendar_events("Already over", "2026-11-04T07:00", "2026-11-04T08:00")
    calendar_events("Cancelled", status="cancelled")
    calendar_events("Removed", status="removed")
    html, context = page(app, client)
    days = dates(context)
    assert list(days) == [f"2026-11-{day:02d}" for day in range(4, 11)]
    assert [day["count"] for day in days.values()] == [1, 1, 0, 0, 0, 0, 0]
    assert context["calendar_previous"] is None
    assert params(context["calendar_next"]) == {"calendar": ["2026-11-11"]}
    assert "Choose a date" in html and 'data-campus-date="2026-11-05"' in html
    assert {event["title"] for event in context["events"]} == {"Tomorrow", "Ongoing"}


def test_exact_day_counts_match_results_for_overnight_events_and_midnight_boundaries(app, client, calendar_events):
    calendar_events("Overnight", "2026-11-05T23:00", "2026-11-06T01:00")
    calendar_events("Ends at midnight", "2026-11-05T22:00", "2026-11-06T00:00")
    calendar_events("Starts next day", "2026-11-07T00:00", "2026-11-07T01:00")
    _, context = page(app, client, "/events?date=2026-11-06")
    assert [event["title"] for event in context["events"]] == ["Overnight"]
    assert dates(context)["2026-11-06"]["count"] == 1
    assert dates(context)["2026-11-05"]["count"] == 2
    assert dates(context)["2026-11-06"]["selected"] is True
    assert context["exact_date_label"] == "Friday, November 6, 2026"


def test_native_week_day_and_clear_links_preserve_filters_and_keep_week_separate_from_selection(app, client, calendar_events):
    calendar_events("Robotics meetup", "2026-11-12T12:00", "2026-11-12T13:00")
    calendar_events("Robotics workshop", "2026-11-13T12:00", "2026-11-13T13:00")
    calendar_events("Other club", "2026-11-12T12:00", "2026-11-12T13:00")
    calendar_events("Robotics away", "2026-11-12T12:00", "2026-11-12T13:00", location="Library")
    calendar_events("Robotics social", "2026-11-12T12:00", "2026-11-12T13:00", category="Social")
    _, context = page(app, client, "/events?q=Robotics&category=Tech&location=Benson&date=2026-11-12")
    expected = {"q": ["Robotics"], "category": ["Tech"], "location": ["Benson"]}
    assert context["calendar_value"] == "2026-11-11"
    assert dates(context)["2026-11-12"]["count"] == 1
    assert dates(context)["2026-11-13"]["count"] == 1
    assert [event["title"] for event in context["events"]] == ["Robotics meetup"]
    assert params(context["calendar_next"]) == {**expected, "date": ["2026-11-12"], "calendar": ["2026-11-18"]}

    # Follow the actual server-rendered links, as a browser without JS would.
    _, next_week = page(app, client, context["calendar_next"])
    assert next_week["calendar_value"] == "2026-11-18"
    assert [event["title"] for event in next_week["events"]] == ["Robotics meetup"]
    _, previous = page(app, client, next_week["calendar_previous"])
    assert previous["calendar_value"] == "2026-11-11"
    _, chosen = page(app, client, dates(previous)["2026-11-13"]["url"])
    assert [event["title"] for event in chosen["events"]] == ["Robotics workshop"]
    assert params(chosen["clear_date_url"]) == {**expected, "calendar": ["2026-11-11"]}
    _, cleared = page(app, client, chosen["clear_date_url"])
    assert cleared["filters"]["date"] == ""
    assert {event["title"] for event in cleared["events"]} == {"Robotics meetup", "Robotics workshop"}


@pytest.mark.parametrize("bad", ["garbage", "2026-13-01", "2026-02-30", "20261112", "2026-W46-4", "-99999999", "10000-01-01"])
def test_invalid_calendar_and_date_inputs_fall_back_to_current_week(app, client, calendar_events, bad):
    _, context = page(app, client, f"/events?calendar={bad}&date={bad}")
    assert context["calendar_value"] == "2026-11-04"
    assert context["filters"]["date"] == ""
    assert context["date_label"] == "All upcoming"
    assert len(context["campus_days"]) == 7


def test_past_calendar_cannot_go_back_before_today_but_distant_future_is_reachable(app, client, calendar_events):
    _, past = page(app, client, "/events?calendar=0001-01-01")
    assert past["calendar_value"] == "2026-11-04" and past["calendar_previous"] is None
    calendar_events("Future event", "2050-11-05T12:00", "2050-11-05T13:00")
    _, future = page(app, client, "/events?date=2050-11-05")
    assert dates(future)["2050-11-05"]["count"] == 1
    assert [event["title"] for event in future["events"]] == ["Future event"]
    assert future["calendar_previous"] and future["calendar_next"]


def test_last_representable_calendar_week_does_not_overflow(app, client, calendar_events):
    _, context = page(app, client, "/events?calendar=9999-12-31&date=9999-12-31")
    assert len(context["campus_days"]) == 7
    assert context["calendar_next"] is None
    assert context["calendar_previous"]
    assert context["events"] == []
    assert all(day["count"] == 0 for day in context["campus_days"])


def test_year_rollover_week_and_month_ranges_use_campus_today(app, client, calendar_events, monkeypatch):
    monkeypatch.setattr(event_routes, "campus_now", lambda: datetime(2026, 12, 31, 23, 30))
    calendar_events("New Year", "2027-01-01T00:00", "2027-01-01T02:00")
    calendar_events("Late December", "2026-12-31T23:45", "2027-01-01T00:00")
    calendar_events("Next Monday", "2027-01-04T12:00", "2027-01-04T13:00")
    _, month = page(app, client, "/events?date=month")
    assert [event["title"] for event in month["events"]] == ["Late December"]
    assert month["calendar_range"] == "Dec 31, 2026 – Jan 6, 2027"
    _, week = page(app, client, "/events?date=week")
    assert {event["title"] for event in week["events"]} == {"Late December", "New Year"}
    _, today = page(app, client, "/events?date=today")
    assert [event["title"] for event in today["events"]] == ["Late December"]
    assert dates(today)["2026-12-31"]["selected"] is True


def test_far_future_counts_and_invalid_category_use_actual_matching_events(app, client, calendar_events):
    calendar_events("Far future", "2050-01-01T12:00", "2050-01-01T13:00")
    # The calendar produces a fixed seven-day result even at a far-future link.
    _, context = page(app, client, "/events?date=2050-01-01&category=invalid")
    assert len(context["campus_days"]) == 7
    assert context["filters"]["category"] == ""
    assert sum(day["count"] for day in context["campus_days"]) == 1
    assert context["result_announcement"].startswith("1 event ·")
