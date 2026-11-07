"""Keep event removal, affected registrations and report history atomic."""

from datetime import datetime, timezone

from sqlalchemy import update

from app.extensions import db
from app.models import Event, Registration, Report
from app.services.event_write_service import event_write


def resolve_report(report, reviewer, action, note):
    """Return False for an already-reviewed report; never overwrite its audit.

    The caller checks admin access, CSRF and form validation. Conditional writes
    also protect against a stale browser tab or two admins acting together.
    """
    if action not in ("dismiss", "remove"):
        raise ValueError("Unknown moderation action")
    if reviewer.role != "admin":
        raise PermissionError("Only admins can resolve event reports")
    report_id, event_id, reviewer_id = report.id, report.event_id, reviewer.id
    with event_write(event_id) as event:
        if event is None:
            return False
        # The same event lock is used by registration, checkout and organizer
        # updates, so no active signup can arrive between removal and cleanup.
        decision = {
            "status": "removed" if action == "remove" else "dismissed",
            "reviewed_by_id": reviewer_id,
            "reviewed_at": datetime.now(timezone.utc).replace(tzinfo=None),
            "review_note": note,
        }
        claimed = db.session.execute(
            update(Report).where(Report.id == report_id, Report.status == "pending").values(**decision)
        )
        if not claimed.rowcount:
            return False

        if action == "remove":
            from app.services.notification_service import queue_event_notices
            queue_event_notices(event, "removed")
            from app.services.stripe_checkout_service import queue_event_cancellations
            queue_event_cancellations(event_id)
            db.session.execute(update(Event).where(Event.id == event_id).values(status="removed"))
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
