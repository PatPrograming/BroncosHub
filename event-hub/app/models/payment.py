# app/models/payment.py
# Payment table: admission payments for paid events. UC-003, UC-008.
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    registration_id = db.Column(
        db.ForeignKey("registrations.id"), unique=True, nullable=False
    )
    amount_cents = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False)
    provider_reference = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    registration = db.relationship("Registration", back_populates="payment")
