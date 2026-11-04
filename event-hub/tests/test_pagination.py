"""Real lists retain every result, filters, ownership and whole-account totals."""

from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from flask import template_rendered

from app.extensions import db
from app.models import Event, Payment, Registration, Report, User


NOW = datetime(2050, 11, 4, 12)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr("app.routes.events.campus_now", lambda: NOW)
    monkeypatch.setattr("app.routes.organizer.campus_now", lambda: NOW)


def account(name, role="student"):
    row = User(name=name, email=f"{name}@scu.edu", google_sub=f"pagination-{name}", role=role)
    db.session.add(row)
    db.session.flush()
    return row


def events(owner, count, prefix="Music lab", **changes):
    rows = []
    for number in range(count):
        start = NOW + timedelta(days=1, minutes=number)
        values = dict(organizer=owner, title=f"{prefix} {number:03}", description="A campus gathering.",
                      location="Benson Center", category="Tech", status="published",
                      start_time=start, end_time=start + timedelta(hours=2), price_cents=0)
        values.update(changes)
        row = Event(**values)
        db.session.add(row)
        rows.append(row)
    db.session.flush()
    return rows


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


class Navigation(HTMLParser):
    def __init__(self, response):
        super().__init__()
        self.pages = {}
        self.feed(response.get_data(as_text=True))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("rel") in ("prev", "next"):
            assert attrs.get("aria-label")
            self.pages[attrs["rel"]] = attrs["href"]


def page(app, client, url):
    contexts = []

    def capture(sender, template, context, **extra):
        contexts.append(context)

    with template_rendered.connected_to(capture, app):
        response = client.get(url)
    assert response.status_code == 200
    return response, contexts[0], Navigation(response).pages


def test_event_pages_keep_search_filters_calendar_counts_and_all_results(app, client):
    with app.app_context():
        host = account("Host", "organizer")
        wanted = events(host, 30)
        for change in ({"category": "Social"}, {"location": "Library"},
                       {"title": "Unrelated title"}, {"status": "cancelled"},
                       {"status": "removed"}, {"start_time": NOW + timedelta(days=2),
                                             "end_time": NOW + timedelta(days=2, hours=2)}):
            events(host, 1, **change)
        db.session.commit()
        wanted_ids = {row.id for row in wanted}

    filters = {"q": "Music lab", "category": "Tech", "location": "Benson",
               "date": "2050-11-05", "calendar": "2050-11-04"}
    first, context, links = page(app, client, "/events?" + urlencode({**filters, "per_page": 999}))
    assert len(context["events"]) == 24
    assert context["pagination"]["total"] == 30
    assert "Showing 1–24 of 30" in first.get_data(as_text=True)
    assert 'aria-current="page"' in first.get_data(as_text=True)
    assert "prev" not in links
    query = parse_qs(urlsplit(links["next"]).query)
    assert query == {**{key: [value] for key, value in filters.items()}, "page": ["2"]}
    assert urlsplit(links["next"]).fragment == "campus-results"
    selected = next(day for day in context["campus_days"] if day["selected"])
    assert selected["count"] == 30
    first_ids = {row["id"] for row in context["events"]}

    _, second, second_links = page(app, client, links["next"])
    second_ids = {row["id"] for row in second["events"]}
    assert len(second_ids) == 6
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == wanted_ids
    assert "next" not in second_links
    assert parse_qs(urlsplit(second_links["prev"]).query)["page"] == ["1"]
    # Selecting a different day or submitting filters starts at the first page.
    assert all("page=" not in day["url"] for day in second["campus_days"])
    assert 'name="page"' not in first.get_data(as_text=True)


@pytest.mark.parametrize("query", [
    "page=0", "page=-1", "page=abc", "page=1.5", "page=", "page=+1",
    "page=999999999999999999999", "page=1&page=2", "page=%D9%A1",
])
def test_invalid_page_values_are_rejected(client, query):
    assert client.get("/events?" + query).status_code == 400


def test_empty_first_page_works_and_nonexistent_pages_do_not_silently_truncate(app, client):
    response, context, links = page(app, client, "/events?page=1&q=no-matches")
    assert context["pagination"]["total"] == 0
    assert context["events"] == [] and links == {}
    assert b"No events found" in response.data
    assert client.get("/events?page=2").status_code == 404
    assert client.get("/events?page=999999999").status_code == 404


def test_my_registrations_pages_never_include_another_users_records(app, client):
    with app.app_context():
        host, student, other = account("Host", "organizer"), account("Alex"), account("Other")
        campus_events = events(host, 30)
        mine = [Registration(student=student, event=event, status="confirmed") for event in campus_events]
        db.session.add_all(mine)
        db.session.add(Registration(student=other, event=campus_events[0], status="confirmed"))
        db.session.commit()
        wanted_ids = {row.id for row in mine}
        sign_in(client, student.id)

    _, first, links = page(app, client, "/my-registrations")
    assert first["pagination"]["total"] == 30 and len(first["registrations"]) == 24
    _, second, _ = page(app, client, links["next"])
    assert len(second["registrations"]) == 6
    shown = first["registrations"] + second["registrations"]
    assert {row["id"] for row in shown} == wanted_ids
    assert client.get("/my-registrations?page=0").status_code == 400


def test_organizer_totals_cover_all_owned_pages_and_keep_payment_history(app, client):
    with app.app_context():
        host, other, student = account("Host", "organizer"), account("Other", "organizer"), account("Alex")
        mine = events(host, 30, prefix="My event")
        mine[-2].status, mine[-1].status = "cancelled", "removed"
        registrations = [Registration(student=student, event=event,
                                      status="confirmed" if index < 28 else "cancelled")
                         for index, event in enumerate(mine)]
        db.session.add_all(registrations)
        elsewhere = events(other, 2, prefix="Another organizer event")
        foreign = Registration(student=student, event=elsewhere[0], status="confirmed")
        db.session.add(foreign)
        db.session.flush()
        db.session.add_all([
            Payment(registration=registrations[-1], amount_cents=1250, status="succeeded", provider_reference="page-owned-payment"),
            Payment(registration=foreign, amount_cents=9999, status="succeeded", provider_reference="page-other-payment"),
        ])
        db.session.commit()
        wanted_ids = {row.id for row in mine}
        sign_in(client, host.id)

    _, first, links = page(app, client, "/organizer/")
    _, second, _ = page(app, client, links["next"])
    assert len(first["events"]) == 24 and len(second["events"]) == 6
    assert first["pagination"]["total"] == 30
    assert first["stats"] == second["stats"]
    assert first["stats"]["events_count"] == 28
    assert first["stats"]["registrations"] == 28
    assert first["stats"]["revenue_cents"] == 1250
    assert {row["id"] for row in first["events"] + second["events"]} == wanted_ids
    assert client.get("/organizer/?page=0").status_code == 400


def test_admin_report_pages_keep_status_filter_and_full_tab_counts(app, client):
    with app.app_context():
        host, student, admin = account("Host", "organizer"), account("Alex"), account("Admin", "admin")
        campus_events = events(host, 33)
        reports = [Report(event=event, reporter=student, reason="other", details="Please review this listing.",
                          status="pending" if index < 30 else "dismissed",
                          created_at=NOW + timedelta(minutes=index),
                          reviewed_at=NOW + timedelta(days=1) if index >= 30 else None)
                   for index, event in enumerate(campus_events)]
        db.session.add_all(reports)
        db.session.commit()
        pending_ids = {row.id for row in reports[:30]}
        sign_in(client, admin.id)

    _, first, links = page(app, client, "/admin/reports?status=pending")
    _, second, _ = page(app, client, links["next"])
    assert len(first["reports"]) == 24 and len(second["reports"]) == 6
    assert first["counts"] == second["counts"] == {"pending": 30, "resolved": 3}
    assert {row["id"] for row in first["reports"] + second["reports"]} == pending_ids
    assert parse_qs(urlsplit(links["next"]).query) == {"status": ["pending"], "page": ["2"]}
    _, resolved, resolved_links = page(app, client, "/admin/reports?status=resolved")
    assert resolved["pagination"]["total"] == 3 and resolved_links == {}
    assert client.get("/admin/reports?status=resolved&page=2").status_code == 404


def test_admin_homepage_can_start_an_event(app, client):
    with app.app_context():
        admin = account("Admin", "admin")
        db.session.commit()
        sign_in(client, admin.id)
    response = client.get("/")
    assert response.status_code == 200
    assert b'href="/organizer/events/new"' in response.data
    assert b"Try organizer view" not in response.data
