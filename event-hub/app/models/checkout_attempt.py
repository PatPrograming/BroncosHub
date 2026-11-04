"""Durable seat reservations and provider reconciliation for hosted checkout."""

from datetime import datetime, timezone

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CheckoutAttempt(db.Model):
    __tablename__ = "checkout_attempts"

    id = db.Column(db.Integer, primary_key=True)
    registration_id = db.Column(db.ForeignKey("registrations.id"), nullable=False, index=True)
    event_id = db.Column(db.ForeignKey("events.id"), nullable=False, index=True)
    account_id = db.Column(db.String(255), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    event_title = db.Column(db.String(100), nullable=False)
    customer_email = db.Column(db.String(255), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="usd")
    livemode = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(24), nullable=False, default="creating", index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    session_id = db.Column(db.String(255), unique=True)
    payment_intent_id = db.Column(db.String(255), unique=True)
    refund_id = db.Column(db.String(255), unique=True)
    checkout_url = db.Column(db.Text)
    success_url = db.Column(db.Text, nullable=False)
    cancel_url = db.Column(db.Text, nullable=False)
    expire_requested = db.Column(db.Boolean, nullable=False, default=False)
    last_error = db.Column(db.String(500))
    retry_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    registration = db.relationship("Registration")
