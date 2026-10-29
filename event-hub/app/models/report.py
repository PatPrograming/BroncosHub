"""Saved event reports and the admin decision, kept for review history."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.extensions import db


REPORT_REASONS = (
    ("spam", "Spam or advertising"),
    ("misleading", "Misleading event information"),
    ("inappropriate", "Inappropriate or unsafe content"),
    ("other", "Something else"),
)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Report(db.Model):
    __tablename__ = "reports"
    __table_args__ = (
        db.UniqueConstraint("event_id", "reporter_id", name="uq_report_event_reporter"),
        db.CheckConstraint("status IN ('pending', 'dismissed', 'removed')", name="ck_report_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.ForeignKey("events.id"), nullable=False, index=True)
    reporter_id = db.Column(db.ForeignKey("users.id"), nullable=False)
    reason = db.Column(db.String(30), nullable=False)
    details = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    reviewed_by_id = db.Column(db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    review_note = db.Column(db.Text, nullable=True)

    event = db.relationship("Event")
    reporter = db.relationship("User", foreign_keys=[reporter_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])

    def report_view(self):
        def date_label(value):
            # Report timestamps are stored as naive UTC; convert only for display.
            return (
                value.replace(tzinfo=timezone.utc)
                .astimezone(ZoneInfo("America/Los_Angeles"))
                .strftime("%b %d, %Y · %H:%M %Z")
                if value else None
            )

        return {
            "id": self.id,
            "event": self.event.event_view(),
            "reason": dict(REPORT_REASONS).get(self.reason, self.reason),
            "details": self.details,
            "reporter": self.reporter.name,
            "date_label": date_label(self.created_at),
            "status": self.status,
            "review_note": self.review_note,
            "reviewed_by": self.reviewed_by.name if self.reviewed_by else None,
            "reviewed_date_label": date_label(self.reviewed_at),
        }
