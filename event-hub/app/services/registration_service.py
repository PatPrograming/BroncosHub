# app/services/registration_service.py
# Registration rules. UC-002, UC-004. Backlog #9, #10, #18.
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Registration
from app.services.event_service import campus_now


class RegistrationRefused(Exception):
    """The event is closed or full, so a new registration isn't allowed."""


class CancellationNotAllowed(Exception):
    """Only the owner can cancel, and only a confirmed free registration."""


def find_registration(student, event):
    return db.session.scalar(
        db.select(Registration).where(
            Registration.student_id == student.id,
            Registration.event_id == event.id,
        )
    )


def register_student(student, event):
    existing = find_registration(student, event)
    # Already signed up (confirmed, pending payment, or failed payment): no duplicates.
    if existing is not None and existing.status != "cancelled":
        return existing

    if event.is_cancelled:
        raise RegistrationRefused("This event has been cancelled. Registration is closed.")
    if event.end_time <= campus_now():
        raise RegistrationRefused("This event has ended. Registration is closed.")
    spots = event.spots_left()
    if spots is not None and spots <= 0:
        raise RegistrationRefused("This event is fully booked.")

    # Free events are confirmed right away; paid events wait for checkout (UC-003).
    status = "confirmed" if event.is_free else "pending"
    if existing is None:
        existing = Registration(student=student, event=event, status=status)
        db.session.add(existing)
    else:
        # The unique (student_id, event_id) rule means a student who cancelled
        # and comes back reuses their old row instead of getting a new one.
        existing.status = status

    try:
        db.session.commit()
    except IntegrityError:
        # Two clicks at the same moment: the other request saved first. Use its row.
        db.session.rollback()
        return find_registration(student, event)
    return existing


def cancel_free_registration(student, registration):
    if registration.student_id != student.id:
        raise CancellationNotAllowed("This isn't your registration.")
    if not registration.event.is_free:
        raise CancellationNotAllowed("Paid tickets can't be cancelled yet.")
    if registration.status != "confirmed":
        raise CancellationNotAllowed("Only confirmed registrations can be cancelled.")
    registration.status = "cancelled"
    db.session.commit()
    return registration
