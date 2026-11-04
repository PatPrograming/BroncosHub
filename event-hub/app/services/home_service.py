"""Today's campus events and the signed-in student's next registration."""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Event, Registration
from app.services.event_service import campus_now


def home_context(user_id=None):
    now = campus_now()
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    available = (Event.status == "published", Event.end_time > now)
    today = (*available, Event.start_time < tomorrow)
    event_query = select(Event).options(
        joinedload(Event.organizer), selectinload(Event.registrations),
    )
    today_count = db.session.scalar(select(func.count(Event.id)).where(*today))
    today_rows = db.session.scalars(
        event_query.where(*today).order_by(Event.start_time, Event.id).limit(4)
    ).all()

    next_event = None
    if not today_count:
        next_event = db.session.scalar(
            event_query.where(*available, Event.start_time >= tomorrow)
            .order_by(Event.start_time, Event.id).limit(1)
        )

    registration = None
    if user_id is not None:
        registration = db.session.scalar(
            select(Registration).join(Registration.event).where(
                Registration.student_id == user_id,
                Registration.status == "confirmed",
                *available,
            ).options(
                joinedload(Registration.event).joinedload(Event.organizer),
                joinedload(Registration.event).selectinload(Event.registrations),
                joinedload(Registration.payment),
                joinedload(Registration.ticket),
            ).order_by(Event.start_time, Registration.id).limit(1)
        )

    return {
        "home_today_label": f"{now:%A, %B} {now.day}",
        "today_events": [
            {**event.event_view(), "ongoing": event.start_time <= now}
            for event in today_rows
        ],
        "today_event_count": today_count,
        "next_campus_event": next_event.event_view() if next_event is not None else None,
        "next_registration": registration.registration_view() if registration is not None else None,
    }
