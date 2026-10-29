# tests/test_payments.py
# Tests for the mock Payment Service. UC-003.
from datetime import datetime
from itertools import count

from app.extensions import db
from app.models import Event, Payment, Registration, Ticket, User
from app.services.payment_service import complete_purchase

CARD = "4242424242424242"


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def make_pending_registration(price_cents=1500):
    numbers = count(1)
    number = next(numbers)
    organizer = User(
        name=f"Organizer {number}",
        email=f"organizer-{number}@scu.edu",
        google_sub=f"google-organizer-{number}",
        role="organizer",
    )
    student = User(
        name="Alex Bronco",
        email="alex-pay@scu.edu",
        google_sub="google-alex-pay",
        role="student",
    )
    db.session.add_all([organizer, student])
    db.session.flush()
    event = Event(
        organizer=organizer,
        title="Paid campus night",
        description="A fictional paid event.",
        location="Mission Santa Clara",
        category="Social",
        start_time=datetime(2050, 10, 15, 18),
        end_time=datetime(2050, 10, 15, 20),
        capacity=10,
        price_cents=price_cents,
        status="published",
    )
    registration = Registration(event=event, student=student, status="pending")
    db.session.add(registration)
    db.session.commit()
    return registration


def stored_text():
    chunks = []
    for model in (Payment, Ticket, Registration, User, Event):
        for row in db.session.scalars(db.select(model)):
            chunks.extend(str(getattr(row, column.name)) for column in model.__table__.columns)
    return " ".join(chunks)


def test_successful_payment_issues_one_ticket(app):
    with app.app_context():
        registration = make_pending_registration()
        complete_purchase(registration, CARD)
        assert registration.status == "confirmed"
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 1
        assert registration.ticket.is_valid is True
        assert registration.payment.status == "succeeded"


def test_declined_payment_issues_no_ticket(app):
    with app.app_context():
        registration = make_pending_registration()
        complete_purchase(registration, "4000000000000002")
        assert registration.status == "failed"
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 0
        assert registration.payment.status == "declined"


def test_pending_payment_keeps_registration_pending(app):
    with app.app_context():
        registration = make_pending_registration()
        complete_purchase(registration, "4999000000000003")
        assert registration.status == "pending"
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 0
        assert registration.payment.status == "pending"


def test_second_success_does_not_duplicate_payment_or_ticket(app):
    with app.app_context():
        registration = make_pending_registration()
        complete_purchase(registration, CARD)
        complete_purchase(registration, CARD)
        assert db.session.scalar(db.select(db.func.count()).select_from(Payment)) == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 1


def test_card_number_is_not_stored(app, client):
    with app.app_context():
        registration = make_pending_registration()
        registration_id = registration.id
        student_id = registration.student_id
    sign_in(client, student_id)
    checkout_url = f"/registrations/{registration_id}/checkout"
    assert client.get(checkout_url).status_code == 200
    with client.session_transaction() as session:
        csrf_token = session["moderation_csrf"]["token"]
    response = client.post(
        checkout_url,
        data={"card_number": CARD, "csrf_token": csrf_token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"BH-" in response.data
    with app.app_context():
        assert CARD not in stored_text()


def test_other_student_cannot_open_checkout(app, client):
    with app.app_context():
        registration = make_pending_registration()
        registration_id = registration.id
        other = User(
            name="Other Bronco",
            email="other-pay@scu.edu",
            google_sub="google-other-pay",
            role="student",
        )
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    sign_in(client, other_id)
    assert client.get(f"/registrations/{registration_id}/checkout").status_code == 404
    assert client.get(f"/my-registrations/{registration_id}").status_code == 404
