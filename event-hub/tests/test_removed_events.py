"""Removal closes paid checkouts without erasing receipts or admin history.

Account sessions are supplied locally; checkout uses the real mock-payment
implementation. These checks do not contact Google or a payment provider.
"""

from datetime import datetime
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import Event, Payment, Registration, Report, Ticket, User
from app.services.payment_service import complete_purchase
from app.services.registration_service import register_student


TITLE = "Private removed listing title"
DESCRIPTION = "Original listing description retained for admin review."
LOCATION = "Original private venue"
REVIEW_NOTE = "Private review note: the listing violates campus event rules."


class TokenField(HTMLParser):
    def __init__(self, response):
        assert response.status_code == 200
        super().__init__()
        self.token = None
        self.feed(response.get_data(as_text=True))
        assert self.token

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name") == "csrf_token":
            self.token = attrs.get("value")


def snapshot():
    return {
        model.__tablename__: [
            tuple(getattr(row, column.name) for column in model.__table__.columns)
            for row in db.session.scalars(db.select(model).order_by(model.id))
        ]
        for model in (Event, Registration, Payment, Ticket, Report)
    }


@pytest.fixture
def paid_case(app):
    def build(card):
        with app.app_context():
            accounts = {
                role: User(name=f"Paid {role}", email=f"paid-{role}@scu.edu",
                           google_sub=f"paid-{role}", role=role)
                for role in ("student", "organizer", "admin")
            }
            event = Event(
                organizer=accounts["organizer"], title=TITLE, description=DESCRIPTION,
                location=LOCATION, category="Tech", start_time=datetime(2050, 12, 1, 18),
                end_time=datetime(2050, 12, 1, 20), price_cents=1250, capacity=10,
                status="published", cover_image_path="removed-poster.webp",
            )
            db.session.add_all([*accounts.values(), event])
            db.session.commit()
            registration = register_student(accounts["student"], event)
            if card:
                complete_purchase(registration, card)
            report = Report(event=event, reporter=accounts["student"], reason="misleading",
                            details="Please investigate the advertised event details.")
            db.session.add(report)
            db.session.commit()
            ids = {role: row.id for role, row in accounts.items()}
            case = SimpleNamespace(event=event.id, registration=registration.id,
                                   report=report.id,
                                   ticket_code=registration.ticket.code if registration.ticket else None)

        case.clients = {}
        for role, user_id in ids.items():
            browser = app.test_client()
            with browser.session_transaction() as session:
                session["_user_id"] = str(user_id)
                session["_fresh"] = True
            case.clients[role] = browser
        # Retain a legitimate form token from before the event is removed.
        case.student_token = TokenField(case.clients["student"].get("/profile")).token
        admin = case.clients["admin"]
        token = TokenField(admin.get(f"/admin/reports/{case.report}")).token
        removed = admin.post(f"/admin/reports/{case.report}/remove", data={
            "csrf_token": token, "review_note": REVIEW_NOTE,
        })
        assert removed.status_code == 302
        with app.app_context():
            assert db.session.get(Event, case.event).status == "removed"
        return case

    return build


@pytest.mark.parametrize("card,payment_status,registration_status,post_status", [
    (None, None, "cancelled", 409),
    ("4999000000000000", "pending", "cancelled", 409),
    ("4000000000000000", "declined", "failed", 409),
    ("4242424242424242", "succeeded", "cancelled", 302),
], ids=["not-yet-paid", "pending-payment", "failed-payment", "already-paid"])
def test_removed_paid_event_closes_stale_checkout_without_charging(
    app, paid_case, monkeypatch, card, payment_status, registration_status, post_status,
):
    case = paid_case(card)
    student = case.clients["student"]

    def must_not_charge(*args, **kwargs):
        pytest.fail("A removed event must never send another payment request")

    monkeypatch.setattr("app.services.payment_service.MockPaymentService.charge", must_not_charge)
    with app.app_context():
        before = snapshot()

    checkout = f"/registrations/{case.registration}/checkout"
    page = student.get(checkout)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "removed" in html.lower()
    assert 'name="card_number"' not in html
    assert TITLE not in html and LOCATION not in html
    response = student.post(checkout, data={
        "csrf_token": case.student_token, "card_number": "4242424242424242",
    })
    assert response.status_code == post_status
    if post_status == 302:
        assert response.location.endswith(f"/my-registrations/{case.registration}")

    with app.app_context():
        assert snapshot() == before
        registration = db.session.get(Registration, case.registration)
        assert registration.status == registration_status
        assert registration.registration_view()["ticket_valid"] is False
        assert registration.registration_view()["ticket_code"] is None
        assert (registration.payment.status if registration.payment else None) == payment_status


def test_removed_paid_receipts_are_redacted_but_admin_history_remains(app, paid_case):
    case = paid_case("4242424242424242")
    student, admin = case.clients["student"], case.clients["admin"]
    with app.app_context():
        before = snapshot()

    for path in ("/my-registrations", f"/my-registrations/{case.registration}",
                 f"/registrations/{case.registration}/checkout", f"/events/{case.event}/report"):
        response = student.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        for private_text in (TITLE, DESCRIPTION, LOCATION, REVIEW_NOTE, case.ticket_code):
            assert private_text not in html
        assert f"/media/events/{case.event}/cover" not in html
        assert f'href="/events/{case.event}"' not in html
        assert "removed" in html.lower()
    assert student.get(f"/events/{case.event}").status_code == 404
    assert student.get(f"/admin/reports/{case.report}").status_code == 403

    review = admin.get(f"/admin/reports/{case.report}")
    assert review.status_code == 200
    html = review.get_data(as_text=True)
    for retained_text in (TITLE, DESCRIPTION, LOCATION, REVIEW_NOTE):
        assert retained_text in html
    assert f'href="/events/{case.event}"' not in html
    with app.app_context():
        assert snapshot() == before
        report = db.session.get(Report, case.report)
        assert report.status == "removed"
        assert report.review_note == REVIEW_NOTE
        assert report.reviewed_by.role == "admin"
        assert report.reviewed_at is not None
        registration = db.session.get(Registration, case.registration)
        assert registration.payment.status == "succeeded"
        assert registration.payment.amount_cents == 1250
