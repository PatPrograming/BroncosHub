"""Admission expires at the campus end time without erasing purchase history."""

from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import Event, Payment, Registration, Ticket, User
from app.models import registration as registration_model


NOW = datetime(2030, 11, 1, 17)


def paid_registration(end):
    organizer = User(name="Expiry Organizer", email="expiry-owner@scu.edu", google_sub="expiry-owner", role="organizer")
    student = User(name="Expiry Student", email="expiry-student@scu.edu", google_sub="expiry-student")
    event = Event(organizer=organizer, title="Campus admission", description="A campus gathering.",
                  category="Tech", location="Benson", start_time=NOW - timedelta(hours=1),
                  end_time=end, status="published", price_cents=1500, capacity=10)
    row = Registration(student=student, event=event, status="confirmed")
    row.payment = Payment(amount_cents=1500, status="succeeded", provider_reference="mock_expiry")
    row.ticket = Ticket(code="BH-EXPIRY", is_valid=True)
    return row


@pytest.mark.parametrize("seconds,valid", [(-1, False), (0, False), (1, True)])
def test_ticket_expiry_uses_end_instant_not_start_date(monkeypatch, seconds, valid):
    monkeypatch.setattr(registration_model, "campus_now", lambda: NOW)
    row = paid_registration(NOW + timedelta(seconds=seconds))
    view = row.registration_view()
    assert view["ended"] is not valid
    assert view["ticket_valid"] is valid
    assert view["ticket_code"] == ("BH-EXPIRY" if valid else None)
    assert view["payment_status"] == "succeeded"
    assert row.ticket.code == "BH-EXPIRY" and row.ticket.is_valid is True


def test_overnight_event_remains_valid_after_midnight_until_its_end(monkeypatch):
    midnight = datetime(2030, 11, 2, 0, 30)
    monkeypatch.setattr(registration_model, "campus_now", lambda: midnight)
    row = paid_registration(midnight + timedelta(hours=1))
    row.event.start_time = midnight - timedelta(hours=2)
    assert row.registration_view()["ticket_valid"] is True


def test_viewing_expired_receipt_does_not_mutate_saved_payment_or_ticket(app, monkeypatch):
    monkeypatch.setattr(registration_model, "campus_now", lambda: NOW)
    with app.app_context():
        row = paid_registration(NOW)
        db.session.add(row)
        db.session.commit()
        ids = row.id, row.payment.id, row.ticket.id
        before = row.status, row.payment.amount_cents, row.payment.provider_reference, row.ticket.code, row.ticket.is_valid
        assert row.registration_view()["ticket_valid"] is False
        db.session.expire_all()
        refreshed = db.session.get(Registration, ids[0])
        assert (refreshed.status, refreshed.payment.amount_cents, refreshed.payment.provider_reference,
                refreshed.ticket.code, refreshed.ticket.is_valid) == before
        assert refreshed.payment.id == ids[1] and refreshed.ticket.id == ids[2]


def test_missing_end_time_cannot_make_a_ticket_valid(monkeypatch):
    monkeypatch.setattr(registration_model, "campus_now", lambda: NOW)
    row = paid_registration(None)
    # Isolate admission validity from the Event view, which requires stored dates.
    monkeypatch.setattr(Event, "event_view", lambda self: {"id": self.id})
    view = row.registration_view()
    assert view["ended"] and not view["ticket_valid"] and view["ticket_code"] is None
