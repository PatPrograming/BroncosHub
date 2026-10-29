"""Real database/route/template flows across events, registrations and moderation.

Only sign-in is supplied through Flask's session; Google OAuth is not exercised.
All form tokens come from the application's rendered pages, and paid checkout
uses the application's existing mock-payment service (no real charge).
"""

from html.parser import HTMLParser
import re

import pytest

from app.extensions import db
from app.models import Event, Payment, Registration, Report, Ticket, User


class Page(HTMLParser):
    """Read navigation and submitted hidden fields from the actual HTML."""

    def __init__(self, response):
        assert response.status_code == 200
        super().__init__(convert_charrefs=True)
        self.links = set()
        self.forms = {}
        self.words = []
        self.current_form = None
        self.feed(response.get_data(as_text=True))
        self.text = " ".join(" ".join(self.words).split())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "href" in attrs:
            self.links.add(attrs["href"])
        if tag == "form":
            self.current_form = attrs.get("action")
            self.forms[self.current_form] = {}
        if tag == "input" and self.current_form is not None:
            if attrs.get("type") == "hidden" and attrs.get("name"):
                self.forms[self.current_form][attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None

    def handle_data(self, value):
        self.words.append(value)

    def fields(self, action):
        fields = self.forms[action]
        assert fields.get("csrf_token"), "The rendered form must supply a real CSRF token"
        return dict(fields)


@pytest.fixture
def browsers(app):
    """Separate sessions retain each account's own generated CSRF token."""
    with app.app_context():
        users = {
            "organizer": User(name="Flow Organizer", email="flow-organizer@scu.edu",
                              google_sub="flow-organizer", role="organizer"),
            "student": User(name="Flow Student", email="flow-student@scu.edu",
                            google_sub="flow-student", year_level="third_year"),
            "admin": User(name="Flow Admin", email="flow-admin@scu.edu",
                          google_sub="flow-admin", role="admin"),
        }
        db.session.add_all(users.values())
        db.session.commit()
        ids = {role: user.id for role, user in users.items()}
    clients = {}
    for role, user_id in ids.items():
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        clients[role] = client
    return clients, ids


def event_fields(**changes):
    values = {
        "title": "Flow Campus Gathering",
        "description": "A campus event for the integration walkthrough.",
        "category": "Tech", "location": "Learning Commons",
        "start_time": "2050-11-01T17:00", "end_time": "2050-11-01T19:00",
        "price": "0", "capacity": "25",
    }
    values.update(changes)
    return values


def create_event(app, organizer, **changes):
    action = "/organizer/events/new"
    form = Page(organizer.get(action))
    response = organizer.post(action, data={**form.fields(action), **event_fields(**changes)})
    assert response.status_code == 302
    with app.app_context():
        event = db.session.scalar(db.select(Event))
        assert event is not None
        assert event.status == "published"
        event_id = event.id
    assert response.headers["Location"] == f"/events/{event_id}"
    return event_id


def register(app, student, event_id):
    action = f"/events/{event_id}/register"
    form = Page(student.get(action))
    response = student.post(action, data=form.fields(action))
    assert response.status_code == 302
    with app.app_context():
        registration = db.session.scalar(
            db.select(Registration).where(Registration.event_id == event_id)
        )
        assert registration is not None
        registration_id = registration.id
    return registration_id, response


def assert_counts(organizer, event_id, expected):
    dashboard = Page(organizer.get("/organizer/"))
    statistics = Page(organizer.get(f"/organizer/events/{event_id}/stats"))
    for page in (dashboard, statistics):
        assert re.search(rf"Confirmed registrations\s+{expected}\b", page.text)
    assert f"/organizer/events/{event_id}/stats" in dashboard.links
    assert f"/events/{event_id}" in statistics.links
    if expected:
        assert f"Third year: {expected} registrations, 100 percent" in (
            organizer.get(f"/organizer/events/{event_id}/stats").get_data(as_text=True)
        )


def test_free_event_lifecycle_updates_student_pages_and_organizer_counts(app, browsers):
    clients, ids = browsers
    organizer, student = clients["organizer"], clients["student"]
    event_id = create_event(app, organizer)
    edit_url = f"/organizer/events/{event_id}/edit"
    editor = Page(organizer.get(edit_url))
    edited = organizer.post(edit_url, data={
        **editor.fields(edit_url),
        **event_fields(title="Flow Updated Gathering", location="Benson Center"),
    })
    assert edited.status_code == 302
    with app.app_context():
        event = db.session.get(Event, event_id)
        assert (event.title, event.location, event.organizer_id) == (
            "Flow Updated Gathering", "Benson Center", ids["organizer"]
        )

    browse = Page(student.get("/events?q=Updated&category=Tech&location=Benson"))
    assert f"/events/{event_id}" in browse.links
    assert "Flow Updated Gathering" in browse.text
    assert f"/events/{event_id}" not in Page(student.get("/events?q=unrelated-query")).links
    detail = Page(student.get(f"/events/{event_id}"))
    assert "Benson Center" in detail.text
    assert f"/events/{event_id}/register" in detail.links
    assert_counts(organizer, event_id, 0)

    registration_id, response = register(app, student, event_id)
    registration_url = f"/my-registrations/{registration_id}"
    assert response.headers["Location"] == registration_url
    confirmation = Page(student.get(registration_url))
    assert "Registration confirmed." in confirmation.text
    assert registration_url in Page(student.get("/my-registrations")).links
    assert registration_url in Page(student.get(f"/events/{event_id}")).links
    with app.app_context():
        registration = db.session.get(Registration, registration_id)
        assert registration.student_id == ids["student"]
        assert registration.status == "confirmed"
        assert registration.event.active_registration_count() == 1
    assert_counts(organizer, event_id, 1)

    cancel_url = f"{registration_url}/cancel"
    cancelled = student.post(cancel_url, data=confirmation.fields(cancel_url), follow_redirects=True)
    assert "You've cancelled this registration." in Page(cancelled).text
    with app.app_context():
        assert db.session.get(Registration, registration_id).status == "cancelled"
    assert_counts(organizer, event_id, 0)

    repeated_id, _ = register(app, student, event_id)
    assert repeated_id == registration_id
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Registration)) == 1
        assert db.session.get(Registration, registration_id).status == "confirmed"
    assert_counts(organizer, event_id, 1)

    cancel_event_url = f"/organizer/events/{event_id}/cancel"
    editor = Page(organizer.get(edit_url))
    response = organizer.post(cancel_event_url, data=editor.fields(cancel_event_url), follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Event, event_id).status == "cancelled"
        assert db.session.get(Registration, registration_id).status == "cancelled"
    assert_counts(organizer, event_id, 0)
    assert f"/events/{event_id}" not in Page(student.get("/events")).links
    detail = Page(student.get(f"/events/{event_id}"))
    assert "Event cancelled" in detail.text
    assert f"/events/{event_id}/register" not in detail.links
    assert "Event cancelled" in Page(student.get("/my-registrations")).text


def test_paid_registration_checkout_issues_one_ticket_and_updates_counts(app, browsers):
    clients, _ = browsers
    organizer, student = clients["organizer"], clients["student"]
    event_id = create_event(app, organizer, title="Flow Ticketed Concert", price="12.50")
    registration_id, response = register(app, student, event_id)
    checkout_url = f"/registrations/{registration_id}/checkout"
    assert response.headers["Location"] == checkout_url
    with app.app_context():
        registration = db.session.get(Registration, registration_id)
        assert registration.status == "pending"
        assert registration.ticket is None
    assert_counts(organizer, event_id, 0)

    checkout = Page(student.get(checkout_url))
    assert "$12.50" in checkout.text
    payment_fields = {**checkout.fields(checkout_url), "card_number": "4242424242424242"}
    paid = Page(student.post(checkout_url, data=payment_fields, follow_redirects=True))
    with app.app_context():
        registration = db.session.get(Registration, registration_id)
        assert registration.status == "confirmed"
        assert registration.payment.status == "succeeded"
        assert registration.payment.amount_cents == 1250
        ticket_code = registration.ticket.code
        assert registration.registration_view()["ticket_valid"] is True
    assert ticket_code in paid.text
    assert "Registration confirmed." in paid.text
    assert f"/my-registrations/{registration_id}" in Page(student.get("/my-registrations")).links
    assert_counts(organizer, event_id, 1)

    repeated = Page(student.post(checkout_url, data=payment_fields, follow_redirects=True))
    assert ticket_code in repeated.text
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Payment)) == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 1


def test_reported_event_removal_closes_the_existing_registration_flow(app, browsers):
    clients, ids = browsers
    organizer, student, admin = clients["organizer"], clients["student"], clients["admin"]
    event_id = create_event(app, organizer, title="Flow Reported Event")
    registration_id, _ = register(app, student, event_id)
    report_url = f"/events/{event_id}/report"
    assert report_url in Page(student.get(f"/events/{event_id}")).links
    form = Page(student.get(report_url))
    submitted = student.post(report_url, data={
        **form.fields(report_url), "reason": "misleading",
        "details": "The advertised venue is different from the organizer's notice.",
    }, follow_redirects=True)
    assert "Report received." in Page(submitted).text
    with app.app_context():
        report = db.session.scalar(db.select(Report))
        assert report.status == "pending"
        assert report.reporter_id == ids["student"]
        report_id = report.id

    review_url = f"/admin/reports/{report_id}"
    assert review_url in Page(admin.get("/admin/reports")).links
    review = Page(admin.get(review_url))
    assert "Flow Reported Event" in review.text
    remove_url = f"{review_url}/remove"
    note = "The organizer confirmed that this event listing is inaccurate."
    resolved = Page(admin.post(remove_url, data={
        **review.fields(remove_url), "review_note": note,
    }, follow_redirects=True))
    assert "Event removed." in resolved.text
    assert note in resolved.text
    with app.app_context():
        report = db.session.get(Report, report_id)
        assert (report.status, report.reviewed_by_id, report.review_note) == (
            "removed", ids["admin"], note
        )
        assert report.reviewed_at is not None
        assert db.session.get(Event, event_id).status == "cancelled"
        assert db.session.get(Registration, registration_id).status == "cancelled"
    assert review_url in Page(admin.get("/admin/reports?status=resolved")).links
    assert f"/events/{event_id}" not in Page(student.get("/events")).links
    assert f"/events/{event_id}/register" not in Page(student.get(f"/events/{event_id}")).links
    assert "no longer active" in Page(student.get(f"/my-registrations/{registration_id}")).text
    assert_counts(organizer, event_id, 0)
