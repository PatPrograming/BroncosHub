# app/services/payment_service.py
# Payment Service. UC-003. Backlog #13, #14, #15.
import secrets

from flask import current_app, has_app_context

from app.extensions import db
from app.models.payment import Payment
from app.models.registration import Registration
from app.models.ticket import Ticket
from app.services.event_service import campus_now
from app.services.event_write_service import event_write


class CheckoutNotAllowed(Exception):
    """Free, cancelled, or otherwise non-payable registration."""


class UnrecognizedCard(Exception):
    """Card number is not one of the mock test prefixes."""


PAYMENTS_UNAVAILABLE = "Online payments are not available yet. No payment was taken."


def _mock_payments_enabled():
    return current_app.config.get("PAYMENT_PROVIDER", "disabled") == "mock"


def checkout_unavailable_reason(registration):
    """Explain a closed unpaid checkout; already completed purchases stay readable."""
    if registration.payment is not None and registration.payment.status == "succeeded":
        return None
    if current_app.config.get("PAYMENT_PROVIDER") not in ("mock", "stripe"):
        return PAYMENTS_UNAVAILABLE
    event = registration.event
    if current_app.config.get("PAYMENT_PROVIDER") == "stripe":
        from app.models.connected_account import ConnectedAccount
        from app.services.stripe_gateway import configured
        if not configured():
            return PAYMENTS_UNAVAILABLE
        account = db.session.scalar(db.select(ConnectedAccount).where(ConnectedAccount.user_id == event.organizer_id))
        if account is None or not account.ready:
            return "The organizer has not enabled online payments yet."
    if event.is_removed:
        return "This event was removed. Checkout is closed. No payment was taken."
    if event.is_free:
        return "This event is free. No payment is needed."
    if event.is_cancelled or registration.status == "cancelled":
        return "This event or registration has been cancelled. Checkout is closed."
    if event.end_time <= campus_now():
        return "This event has ended. Checkout is closed. No payment was taken."
    if event.capacity is not None:
        # Include active Stripe holds, excluding this buyer's own reservation.
        # Mock purchases are rechecked in the same transaction as confirmation.
        from app.services.event_write_service import occupied_seats
        confirmed = occupied_seats(event.id, registration.id)
        if confirmed >= event.capacity:
            return "This event is fully booked. Checkout is closed. No payment was taken."
    return None


class MockPaymentService:
    """Fake payments for development. No card number is stored or sent anywhere."""

    def charge(self, amount_cents, card_number):
        if has_app_context() and not _mock_payments_enabled():
            raise CheckoutNotAllowed(PAYMENTS_UNAVAILABLE)
        digits = "".join(character for character in card_number if character.isdigit())
        if digits.startswith("4242"):
            status = "succeeded"
        elif digits.startswith("4000"):
            status = "declined"
        elif digits.startswith("4999"):
            status = "pending"
        else:
            return None
        return {
            "status": status,
            "reference": f"mock_{secrets.token_hex(8)}",
            "amount_cents": amount_cents,
        }


def complete_purchase(registration, card_number, service=None):
    """Reserve the last seat and save one mock purchase in the same transaction.

    Hosted Stripe payments use stripe_checkout_service's separate reservation
    and webhook protocol. This branch never makes an external network call.
    """
    if not _mock_payments_enabled():
        raise CheckoutNotAllowed(PAYMENTS_UNAVAILABLE)
    registration_id, event_id = registration.id, registration.event_id
    with event_write(event_id) as event:
        registration = db.session.get(Registration, registration_id, populate_existing=True)
        if event is None or registration is None:
            raise CheckoutNotAllowed("This registration is no longer available.")
        if registration.payment is not None and registration.payment.status == "succeeded":
            return registration
        reason = checkout_unavailable_reason(registration)
        if reason:
            raise CheckoutNotAllowed(reason)

        service = service or MockPaymentService()
        result = service.charge(event.price_cents, card_number)
        if result is None:
            raise UnrecognizedCard

        payment = registration.payment
        if payment is None:
            payment = Payment(
                registration=registration,
                amount_cents=result["amount_cents"],
                status=result["status"],
                provider_reference=result["reference"],
            )
            db.session.add(payment)
        else:
            payment.amount_cents = result["amount_cents"]
            payment.status = result["status"]
            payment.provider_reference = result["reference"]

        if result["status"] == "succeeded":
            registration.status = "confirmed"
            if registration.ticket is None:
                db.session.add(Ticket(registration=registration,
                                      code=f"BH-{secrets.token_hex(4).upper()}", is_valid=True))
        elif result["status"] == "declined":
            registration.status = "failed"
        else:
            registration.status = "pending"
        db.session.commit()
        return registration
