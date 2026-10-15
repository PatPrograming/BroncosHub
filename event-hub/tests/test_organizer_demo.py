"""The organizer demo exercises real models without touching real accounts/data."""

import sqlite3
from html.parser import HTMLParser

import pytest
from flask import template_rendered

from app.extensions import db
from app.models import Event, Registration, Report, User
from config import Config
from tools.preview_organizer import create_organizer_demo


@pytest.fixture
def demo():
    application = create_organizer_demo()
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


def page_statistics(application, client, path):
    contexts = []

    def record(sender, template, context, **extra):
        contexts.append(context)

    with template_rendered.connected_to(record, application):
        response = client.get(path)
    assert response.status_code == 200
    return response, contexts[-1]["stats"]


class FormFields(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.csrf_token = None
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "input" and fields.get("name") == "csrf_token":
            self.csrf_token = fields.get("value")


def test_demo_pages_show_real_counts_and_refresh_after_registration_changes(demo):
    client = demo.test_client()
    assert client.get("/organizer/").status_code == 302
    assert client.get("/demo").status_code == 302

    dashboard, overview = page_statistics(demo, client, "/organizer/")
    assert overview["registrations"] == 27
    assert overview["events_count"] == 3
    assert b"<strong>27</strong>" in dashboard.data
    assert b"Another organizer" not in dashboard.data
    details, statistics = page_statistics(demo, client, "/organizer/events/1/stats")
    assert statistics["registrations"] == 18
    assert b"<strong>18</strong>" in details.data
    assert statistics["revenue_available"] is False

    with demo.app_context():
        registration = db.session.scalar(
            db.select(Registration).where(
                Registration.event_id == 1, Registration.status == "confirmed"
            ).order_by(Registration.id)
        )
        registration.status = "cancelled"
        db.session.commit()

    dashboard, overview = page_statistics(demo, client, "/organizer/")
    details, statistics = page_statistics(demo, client, "/organizer/events/1/stats")
    assert overview["registrations"] == 26
    assert statistics["registrations"] == 17
    assert sum(year["count"] for year in statistics["year_breakdown"]) == 17
    assert b"<strong>26</strong>" in dashboard.data
    assert b"<strong>17</strong>" in details.data


def test_demo_ignores_configured_database_and_google_credentials(monkeypatch, tmp_path):
    external_database = tmp_path / "existing-project.sqlite"
    with sqlite3.connect(external_database) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('leave this data alone')")
    original = external_database.read_bytes()
    database_url = "sqlite:///" + str(external_database)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "existing-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "existing-secret")
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", database_url)
    monkeypatch.setattr(Config, "GOOGLE_CLIENT_ID", "existing-client")
    monkeypatch.setattr(Config, "GOOGLE_CLIENT_SECRET", "existing-secret")

    application = create_organizer_demo()
    try:
        with application.app_context():
            assert db.engine.url.database == ":memory:"
            assert db.session.scalar(db.select(db.func.count()).select_from(Registration)) > 0
        assert application.config["GOOGLE_CLIENT_ID"] == ""
        assert application.config["GOOGLE_CLIENT_SECRET"] == ""
        assert external_database.read_bytes() == original
    finally:
        with application.app_context():
            db.session.remove()
            db.engine.dispose()


@pytest.mark.parametrize("host", ["untrusted.example", "localhost.untrusted.example", "127.0.0.1.untrusted.example"])
@pytest.mark.parametrize("path", ["/demo", "/demo/admin", "/demo/student"])
def test_demo_rejects_nonlocal_hosts_before_authentication(demo, host, path):
    # Exercise the explicit guard even where Flask's newer setting is absent.
    demo.config["TRUSTED_HOSTS"] = None
    response = demo.test_client().get(path, headers={"Host": host})
    assert response.status_code == 400
    assert not response.headers.getlist("Set-Cookie")


@pytest.mark.parametrize("path", ["/demo", "/demo/admin", "/demo/student"])
def test_real_application_does_not_expose_demo_login(client, path):
    assert client.get(path).status_code == 404
    assert client.get("/organizer/").status_code == 302
    assert client.get("/profile").status_code == 302


@pytest.mark.parametrize("path,role,destination", [
    ("/demo/admin", "admin", "/admin/reports"),
    ("/demo/student", "student", "/events/1/report"),
])
def test_moderation_demo_entries_use_fictional_accounts_and_real_pages(demo, path, role, destination):
    client = demo.test_client()
    response = client.get(path)
    assert response.status_code == 302
    assert response.location.endswith(destination)
    with client.session_transaction() as session:
        account_id = int(session["_user_id"])
    with demo.app_context():
        account = db.session.get(User, account_id)
        assert account.role == role
        assert account.email.endswith(".demo@scu.edu")
    page = client.get(destination)
    assert page.status_code == 200
    if role == "admin":
        assert b"Build &amp; Brew" in page.data
    else:
        assert b"Sunset Cinema on Mission Lawn" in page.data
        assert FormFields(page.get_data(as_text=True)).csrf_token
        assert client.get("/admin/reports").status_code == 403


def test_demo_event_removal_updates_organizer_counts_and_keeps_history(demo):
    client = demo.test_client()
    client.get("/demo")
    _, before_statistics = page_statistics(demo, client, "/organizer/")
    assert before_statistics["registrations"] == 27
    assert before_statistics["events_count"] == 3

    with demo.app_context():
        workshop = db.session.scalar(db.select(Event).where(Event.title == "Build & Brew"))
        workshop_id = workshop.id
        workshop_reports = db.session.scalars(
            db.select(Report).where(Report.event_id == workshop_id).order_by(Report.id)
        ).all()
        report_id = workshop_reports[0].id
        report_ids = [report.id for report in workshop_reports]
        report_details = {report.id: report.details for report in workshop_reports}
        assert len(workshop_reports) == 2
        other_reports = {
            report.id: (report.status, report.reviewed_at, report.review_note)
            for report in db.session.scalars(db.select(Report).where(Report.event_id != workshop_id))
        }
        registration_snapshot = {
            registration.id: (registration.event_id, registration.status)
            for registration in db.session.scalars(db.select(Registration))
        }
        assert sum(event_id == workshop_id and status == "confirmed"
                   for event_id, status in registration_snapshot.values()) == 8

    client.get("/demo/admin")
    review = client.get(f"/admin/reports/{report_id}")
    assert review.status_code == 200
    token = FormFields(review.get_data(as_text=True)).csrf_token
    assert token
    note = "The event directs students to an unverified payment account."
    response = client.post(f"/admin/reports/{report_id}/remove", data={
        "csrf_token": token, "review_note": note,
    })
    assert response.status_code == 302

    client.get("/demo")
    dashboard, after_statistics = page_statistics(demo, client, "/organizer/")
    assert after_statistics["registrations"] == 19
    assert after_statistics["events_count"] == 2
    assert b"<strong>19</strong>" in dashboard.data
    _, workshop_statistics = page_statistics(demo, client, f"/organizer/events/{workshop_id}/stats")
    assert workshop_statistics["registrations"] == 0
    with demo.app_context():
        assert db.session.get(Event, workshop_id).status == "cancelled"
        registrations = db.session.scalars(db.select(Registration)).all()
        assert {registration.id for registration in registrations} == set(registration_snapshot)
        for registration in registrations:
            event_id, previous_status = registration_snapshot[registration.id]
            expected = "cancelled" if event_id == workshop_id else previous_status
            assert registration.status == expected
        for current_id in report_ids:
            report = db.session.get(Report, current_id)
            assert report.status == "removed"
            assert report.details == report_details[current_id]
            assert report.review_note == note
            assert report.reviewed_at is not None
            assert report.reviewed_by.role == "admin"
        for current_id, original in other_reports.items():
            report = db.session.get(Report, current_id)
            assert (report.status, report.reviewed_at, report.review_note) == original
