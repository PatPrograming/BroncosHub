# app/routes/events.py
# Event browsing pages. UC-001 Discover Events. Backlog #6, #7, #8.
from datetime import timedelta

from flask import Blueprint, abort, render_template, request
from flask_login import current_user
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Event
from app.services.event_service import CATEGORIES, campus_now
from app.services.registration_service import find_registration

events_bp = Blueprint("events", __name__)

DATE_LABELS = {"today": "Today", "week": "This week", "month": "This month"}


def _upcoming_events():
    # Published events that haven't ended yet, soonest first.
    return (
        db.select(Event)
        .where(Event.status == "published", Event.end_time >= campus_now())
        .options(joinedload(Event.organizer), selectinload(Event.registrations))
        .order_by(Event.start_time, Event.id)
    )


def _date_range(choice):
    today = campus_now().replace(hour=0, minute=0, second=0, microsecond=0)
    if choice == "today":
        return today, today + timedelta(days=1)
    if choice == "week":
        # Through the end of Sunday.
        return today, today + timedelta(days=7 - today.weekday())
    if choice == "month":
        next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        return today, next_month
    return None


@events_bp.get("/events")
def list_events():
    filters = {
        "q": request.args.get("q", "").strip(),
        "category": request.args.get("category", ""),
        "date": request.args.get("date", ""),
        "location": request.args.get("location", "").strip(),
    }
    query = _upcoming_events()
    if filters["q"]:
        words = f"%{filters['q']}%"
        query = query.where(Event.title.ilike(words) | Event.description.ilike(words))
    if filters["category"] in CATEGORIES:
        query = query.where(Event.category == filters["category"])
    if filters["location"]:
        query = query.where(Event.location.ilike(f"%{filters['location']}%"))
    date_range = _date_range(filters["date"])
    if date_range:
        start, end = date_range
        # "Happening in this range": starts before the range ends, ends after it starts.
        query = query.where(Event.start_time < end, Event.end_time >= start)

    events = db.session.scalars(query).all()
    return render_template(
        "events/list.html",
        events=[event.event_view() for event in events],
        categories=CATEGORIES,
        filters=filters,
        date_label=DATE_LABELS.get(filters["date"], ""),
        active_nav="explore",
    )


@events_bp.get("/events/<int:event_id>")
def detail(event_id):
    event = db.session.scalar(
        db.select(Event)
        .where(Event.id == event_id)
        .options(joinedload(Event.organizer), selectinload(Event.registrations))
    )
    if event is None:
        abort(404)

    view = event.event_view()
    registration = None
    if current_user.is_authenticated:
        registration = find_registration(current_user, event)
        if registration is not None and registration.status != "cancelled":
            view["registered"] = True
        else:
            registration = None

    related = db.session.scalars(
        _upcoming_events()
        .where(Event.category == event.category, Event.id != event.id)
        .limit(3)
    ).all()
    return render_template(
        "events/detail.html",
        event=view,
        registration=registration,
        related_events=[other.event_view() for other in related],
        event_reporting_available=(
            current_user.is_authenticated and event.organizer_id != current_user.id
        ),
        active_nav="explore",
    )
