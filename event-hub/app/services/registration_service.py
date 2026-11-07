# app/services/registration_service.py
# Registration rules. UC-002, UC-004. Backlog #9, #10, #18.
from app.extensions import db
from app.models import Registration
from app.services.event_service import campus_now
from app.services.event_write_service import event_write, occupied_seats


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
    student_id, event_id = student.id, event.id
    with event_write(event_id) as event:
        if event is None or event.is_removed:
            raise RegistrationRefused("This event is no longer available. Registration is closed.")
        if event.is_cancelled:
            raise RegistrationRefused("This event has been cancelled. Registration is closed.")
        if event.end_time <= campus_now():
            raise RegistrationRefused("This event has ended. Registration is closed.")
        existing = db.session.scalar(db.select(Registration).where(
            Registration.student_id == student_id, Registration.event_id == event_id,
        ))
        # Retrying an existing signup never consumes another seat.
        if existing is not None and existing.status != "cancelled":
            return existing
        taken = occupied_seats(event_id)
        if event.capacity is not None and taken >= event.capacity:
            raise RegistrationRefused("This event is fully booked.")
        status = "confirmed" if event.is_free else "pending"
        if existing is None:
            existing = Registration(student_id=student_id, event_id=event_id, status=status)
            db.session.add(existing)
        else:
            existing.status = status
        if status == "confirmed":
            from app.services.notification_service import queue_registration_notice
            queue_registration_notice(existing, "registered")
        db.session.commit()
        return existing


def cancel_free_registration(student, registration):
    student_id, registration_id, event_id = student.id, registration.id, registration.event_id
    with event_write(event_id) as event:
        registration = db.session.get(Registration, registration_id, populate_existing=True)
        if registration is None or registration.student_id != student_id:
            raise CancellationNotAllowed("This isn't your registration.")
        if not event.is_free:
            raise CancellationNotAllowed("Paid tickets can't be cancelled yet.")
        if event.status != "published" or event.end_time <= campus_now():
            raise CancellationNotAllowed("This event is closed. Its registration history is kept.")
        if registration.status != "confirmed":
            raise CancellationNotAllowed("Only confirmed registrations can be cancelled.")
        registration.status = "cancelled"
        from app.services.notification_service import queue_registration_notice
        queue_registration_notice(registration, "unregistered")
        db.session.commit()
        return registration
