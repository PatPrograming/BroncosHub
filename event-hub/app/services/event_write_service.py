"""Serialize event mutations before deciding availability or lifecycle state."""

from contextlib import contextmanager

from sqlalchemy import select, text

from app.extensions import db
from app.models.event import Event


def occupied_seats(event_id, exclude_registration_id=None):
    """Confirmed seats plus unexpired hosted-checkout reservations."""
    from app.models import Registration
    from app.models.checkout_attempt import CheckoutAttempt, utcnow

    confirmed = select(db.func.count()).select_from(Registration).where(
        Registration.event_id == event_id, Registration.status == "confirmed",
    )
    held = select(db.func.count(db.distinct(CheckoutAttempt.registration_id))).join(
        Registration, Registration.id == CheckoutAttempt.registration_id,
    ).where(
        CheckoutAttempt.event_id == event_id,
        CheckoutAttempt.status.in_(("creating", "open")),
        CheckoutAttempt.expires_at > utcnow(),
        Registration.status != "confirmed",
    )
    if exclude_registration_id is not None:
        confirmed = confirmed.where(Registration.id != exclude_registration_id)
        held = held.where(CheckoutAttempt.registration_id != exclude_registration_id)
    return db.session.scalar(confirmed) + db.session.scalar(held)


@contextmanager
def event_write(event_id):
    """Yield the current event under a write lock; the caller commits its work.

    Request handlers often loaded the event before reaching a service. End that
    read-only transaction first, then refresh under the lock rather than using
    its cached capacity/status. Never discard caller changes silently.
    SQLite serializes writers; other databases lock the affected event row.
    Early returns and exceptions release the lock without saving partial work.
    """
    session = db.session()
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("Acquire the event write lock before changing database records.")
    session.rollback()
    try:
        query = select(Event).where(Event.id == event_id).execution_options(populate_existing=True)
        if session.get_bind().dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        else:
            query = query.with_for_update()
        yield session.scalar(query)
    finally:
        session.rollback()
