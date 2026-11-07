"""Durable event notices saved in the same transaction as the event change."""
from datetime import datetime, timezone

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.String(32), primary_key=True)
    registration_id = db.Column(db.ForeignKey("registrations.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)
    # Snapshot of the new time/location, without email addresses or event descriptions.
    details = db.Column(db.JSON, nullable=False, default=dict)
    # Freeze the provider envelope before the first attempt so a timeout retry
    # uses the same idempotency key AND payload. Cleared on terminal outcomes.
    delivery_payload = db.Column(db.JSON)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    available_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    first_attempt_at = db.Column(db.DateTime)
    lease_token = db.Column(db.String(32))
    lease_until = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime, index=True)
    failure_code = db.Column(db.String(40))
    provider_id = db.Column(db.String(128))
