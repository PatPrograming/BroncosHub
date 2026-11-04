# app/routes/events.py
# Event browsing pages. UC-001 Discover Events. Backlog #6, #7, #8.
from flask import Blueprint, abort, render_template, request
from flask_login import current_user
from sqlalchemy import and_, case, func
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Event
from app.pagination import paginate
from app.services.calendar_service import calendar_context, date_range, normalize_date_filter
from app.services.event_service import CATEGORIES, campus_now
from app.services.registration_service import find_registration

events_bp = Blueprint("events", __name__)


def _upcoming_events():
    # Published events that haven't ended yet, soonest first.
    return (
        db.select(Event)
        .where(Event.status == "published", Event.end_time > campus_now())
        .options(joinedload(Event.organizer), selectinload(Event.registrations))
        .order_by(Event.start_time, Event.id)
    )


def _date_range(choice):
    return date_range(choice, campus_now().date())


def _overlaps(start, end):
    # An event ending exactly at midnight belongs to the previous day.
    conditions = [Event.end_time > start]
    if end is not None:
        conditions.append(Event.start_time < end)
    return and_(*conditions)


def _apply_search_filters(query, filters):
    if filters["q"]:
        words = f"%{filters['q']}%"
        query = query.where(Event.title.ilike(words) | Event.description.ilike(words))
    if filters["category"]:
        query = query.where(Event.category == filters["category"])
    if filters["location"]:
        query = query.where(Event.location.ilike(f"%{filters['location']}%"))
    return query


@events_bp.get("/events")
def list_events():
    now = campus_now()
    filters = {
        "q": request.args.get("q", "").strip(),
        "category": request.args.get("category", ""),
        "date": normalize_date_filter(request.args.get("date", "")),
        "location": request.args.get("location", "").strip(),
    }
    if filters["category"] not in CATEGORIES:
        filters["category"] = ""
    calendar = calendar_context(filters, request.args.get("calendar", ""), now.date())
    intervals = [date_range(day["iso"], now.date()) for day in calendar["campus_days"]]
    # Seven aggregates over just the visible week: browsing far ahead does not
    # load every event between today and the requested date.
    counts_query = db.select(*[
        func.coalesce(func.sum(case((_overlaps(start, end), 1), else_=0)), 0)
        for start, end in intervals
    ]).where(
        Event.status == "published", Event.end_time > now,
        _overlaps(intervals[0][0], intervals[-1][1]),
    )
    counts = db.session.execute(_apply_search_filters(counts_query, filters)).one()
    for day, count in zip(calendar["campus_days"], counts):
        day["count"] = count

    query = _apply_search_filters(_upcoming_events(), filters)
    selected_range = date_range(filters["date"], now.date())
    if selected_range:
        query = query.where(_overlaps(*selected_range))

    events, pagination = paginate(
        query, parameters={**filters, "calendar": calendar["calendar_value"]},
        anchor="campus-results",
    )
    result_count = pagination["total"]
    announcement = f"{result_count} {'event' if result_count == 1 else 'events'} · {calendar['date_label']}."
    if pagination["pages"] > 1:
        announcement += f" Showing {pagination['first']}–{pagination['last']}."
    return render_template(
        "events/list.html",
        events=[event.event_view() for event in events],
        categories=CATEGORIES,
        filters=filters,
        pagination=pagination,
        **calendar,
        result_announcement=announcement,
        active_nav="explore",
    )


@events_bp.get("/events/<int:event_id>")
def detail(event_id):
    event = db.session.scalar(
        db.select(Event)
        .where(Event.id == event_id)
        .options(joinedload(Event.organizer), selectinload(Event.registrations))
    )
    if event is None or event.is_removed:
        abort(404)

    view = event.event_view()
    view["ended"] = event.end_time <= campus_now()
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


@events_bp.errorhandler(404)
def event_not_available(error):
    return render_template(
        "errors/error.html", status=404, title="Event unavailable",
        message="This event is no longer available.",
        return_url="/events", return_label="Browse events", active_nav="explore",
    ), 404
