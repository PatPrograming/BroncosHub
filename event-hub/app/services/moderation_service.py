"""Keep event removal, affected registrations and report history atomic."""

from datetime import datetime, timezone

from sqlalchemy import update

from app.extensions import db
from app.models import Event, Registration, Report


def resolve_report(report, reviewer, action, note):
    """Return False for an already-reviewed report; never overwrite its audit.

    The caller checks admin access, CSRF and form validation. Conditional writes
    also protect against a stale browser tab or two admins acting together.
    """
    if action not in ("dismiss", "remove"):
        raise ValueError("Unknown moderation action")
    if reviewer.role != "admin":
        raise PermissionError("Only admins can resolve event reports")
    decision = {
        "status": "removed" if action == "remove" else "dismissed",
        "reviewed_by_id": reviewer.id,
        "reviewed_at": datetime.now(timezone.utc).replace(tzinfo=None),
        "review_note": note,
    }
    event_id = report.event_id
    claimed = db.session.execute(
        update(Report).where(Report.id == report.id, Report.status == "pending").values(**decision)
    )
    if not claimed.rowcount:
        db.session.rollback()
        return False

    if action == "remove":
        # Soft cancellation preserves records and agrees with Event.is_cancelled.
        db.session.execute(update(Event).where(Event.id == event_id).values(status="cancelled"))
        db.session.execute(
            update(Registration).where(
                Registration.event_id == event_id,
                Registration.status.in_(("confirmed", "pending")),
            ).values(status="cancelled")
        )
        db.session.execute(
            update(Report).where(Report.event_id == event_id, Report.status == "pending")
            .values(**decision)
        )
    db.session.commit()
    return True
