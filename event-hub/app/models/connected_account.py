"""The organizer's Stripe account; legal/bank details stay at Stripe."""

from datetime import datetime, timezone
import secrets

from app.extensions import db


class ConnectedAccount(db.Model):
    __tablename__ = "connected_accounts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    stripe_account_id = db.Column(db.String(100), nullable=True, unique=True)
    onboarding_key = db.Column(db.String(80), nullable=False, unique=True,
                               default=lambda: secrets.token_urlsafe(32))
    creation_email = db.Column(db.String(255), nullable=True)
    creation_name = db.Column(db.String(100), nullable=True)
    charges_enabled = db.Column(db.Boolean, nullable=False, default=False)
    payouts_enabled = db.Column(db.Boolean, nullable=False, default=False)
    details_submitted = db.Column(db.Boolean, nullable=False, default=False)
    livemode = db.Column(db.Boolean, nullable=False, default=False)
    configuration_verified = db.Column(db.Boolean, nullable=False, default=False)
    checked_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    user = db.relationship("User", backref=db.backref("connected_account", uselist=False))

    @property
    def ready(self):
        return bool(self.stripe_account_id and self.configuration_verified
                    and self.charges_enabled and self.payouts_enabled and self.details_submitted)
