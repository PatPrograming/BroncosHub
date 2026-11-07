"""Short-lived shared counters, with no stored email or raw IP address."""

from app.extensions import db


class RateLimitBucket(db.Model):
    __tablename__ = "rate_limit_buckets"

    key = db.Column(db.String(120), primary_key=True)
    count = db.Column(db.Integer, nullable=False)
    reset_at = db.Column(db.BigInteger, nullable=False, index=True)
