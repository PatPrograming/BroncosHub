"""Signed provider callbacks and repeatable recovery for hosted checkout."""

import click
from flask import Blueprint, current_app, request

from app.extensions import db
from app.models.checkout_attempt import CheckoutAttempt

from app.services import stripe_gateway
from app.services.stripe_checkout_service import (
    BillingVerificationError, process_refund, process_refund_notification, process_session, reconcile_pending,
)


billing_bp = Blueprint("billing", __name__, url_prefix="/billing", cli_group="billing")


@billing_bp.post("/webhook")
def webhook():
    if current_app.config.get("PAYMENT_PROVIDER") != "stripe":
        return {"error": "Payments are unavailable."}, 503
    try:
        event = stripe_gateway.construct_event(request.get_data(), request.headers.get("Stripe-Signature", ""))
    except stripe_gateway.PaymentSignatureError:
        return {"error": "Invalid signature."}, 400
    try:
        if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
            raise BillingVerificationError("Missing payment information")
        if event.get("type") in ("checkout.session.completed", "checkout.session.expired"):
            process_session(event["data"].get("object"), event.get("account"))
        elif event.get("type") == "charge.refunded":
            process_refund(event["data"].get("object"), event.get("account"))
        elif event.get("type") in ("refund.created", "refund.updated", "refund.failed"):
            process_refund_notification(event["data"].get("object"), event.get("account"), event.get("livemode"))
    except BillingVerificationError:
        return {"error": "Payment details do not match."}, 400
    except stripe_gateway.PaymentProviderError:
        return {"error": "Provider confirmation is temporarily unavailable."}, 503
    return {"received": True}


@billing_bp.cli.command("reconcile")
@click.option("--limit", default=100, type=click.IntRange(1, 1000), show_default=True)
def reconcile_command(limit):
    """Retry checkout confirmation, expired holds and pending full refunds."""
    if current_app.config.get("PAYMENT_PROVIDER") != "stripe":
        raise click.ClickException("Set PAYMENT_PROVIDER=stripe before reconciling payments.")
    count = reconcile_pending(limit)
    click.echo(f"Checked {count} pending payment attempts.")
    refunds = db.session.scalar(db.select(db.func.count()).select_from(CheckoutAttempt).where(
        CheckoutAttempt.status == "refund_pending",
    ))
    review = db.session.scalar(db.select(db.func.count()).select_from(CheckoutAttempt).where(
        CheckoutAttempt.last_error.contains("operator review required"),
    ))
    if refunds or review:
        click.echo(f"Still pending: {refunds} refunds. Operator review required: {review} payment attempts.")
