# app/services/payment_service.py
# Payment Service. UC-003. Backlog #13, #14, #15.
import secrets

from app.extensions import db
from app.models.payment import Payment
from app.models.ticket import Ticket


class CheckoutNotAllowed(Exception):
    """Free, cancelled, or otherwise non-payable registration."""


class UnrecognizedCard(Exception):
    """Card number is not one of the mock test prefixes."""


class MockPaymentService:
    """Fake payments for development. No card number is stored or sent anywhere."""

    def charge(self, amount_cents, card_number):
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
    """Save exactly one payment. A ticket is issued only after success."""
    event = registration.event
    if event.is_free or event.is_cancelled or registration.status == "cancelled":
        raise CheckoutNotAllowed
    if registration.payment is not None and registration.payment.status == "succeeded":
        return registration

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
            db.session.add(
                Ticket(
                    registration=registration,
                    code=f"BH-{secrets.token_hex(4).upper()}",
                    is_valid=True,
                )
            )
    elif result["status"] == "declined":
        registration.status = "failed"
    else:
        registration.status = "pending"

    db.session.commit()
    return registration
