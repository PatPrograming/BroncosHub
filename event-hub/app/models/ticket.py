# app/models/ticket.py
# Ticket table: one ticket per paid, confirmed registration. UC-002, UC-003, UC-007.
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Ticket(db.Model):
    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)
    registration_id = db.Column(
        db.ForeignKey("registrations.id"), unique=True, nullable=False
    )
    code = db.Column(db.String(32), unique=True, nullable=False)
    is_valid = db.Column(db.Boolean, nullable=False, default=True)
    issued_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    registration = db.relationship("Registration", back_populates="ticket")
