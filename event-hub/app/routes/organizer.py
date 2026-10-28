"""Owner-only organizer dashboard, statistics, and event create/edit/cancel."""

from functools import wraps
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, case, select
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Event
from app.security import csrf_token, validate_csrf
from app.services.event_service import CATEGORIES, validate_event_form
from app.services.stats_service import dashboard_statistics, event_statistics


organizer_bp = Blueprint("organizer", __name__, url_prefix="/organizer")


@organizer_bp.context_processor
def organizer_context():
    return {"csrf_token": csrf_token}


def organizer_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.role not in ("organizer", "admin"):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _owned_events():
    # Admin accounts also see only their own events here. Moderation is separate.
    return select(Event).where(Event.organizer_id == current_user.id).options(
        joinedload(Event.organizer), selectinload(Event.registrations)
    )


def _owned_event(event_id):
    event = db.session.scalar(_owned_events().where(Event.id == event_id))
    if event is None:
        abort(404)
    return event


def _page_context():
    return {
        "active_nav": "organizer",
        "event_management_available": True,
        "event_browsing_available": True,
    }


@organizer_bp.get("/")
@organizer_required
def dashboard():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    upcoming = and_(Event.status == "published", Event.end_time >= now)
    events = db.session.scalars(
        _owned_events().order_by(
            case((upcoming, 0), else_=1),
            case((upcoming, Event.start_time)).asc(),
            Event.start_time.desc(), Event.id.desc(),
        )
    ).all()
    return render_template(
        "organizer/dashboard.html",
        events=[event.event_view() for event in events],
        stats=dashboard_statistics(events),
        **_page_context(),
    )


@organizer_bp.route("/events/new", methods=["GET", "POST"])
@organizer_required
def create_event():
    if request.method == "POST":
        validate_csrf()
        data, errors = validate_event_form(request.form)
        if errors:
            return render_template(
                "organizer/event_form.html", categories=CATEGORIES,
                form_values=request.form, errors=errors, **_page_context(),
            ), 422
        event = Event(organizer_id=current_user.id, status="published", **data)
        db.session.add(event)
        db.session.commit()
        flash("Your event is live.", "success")
        return redirect(url_for("events.detail", event_id=event.id))
    return render_template("organizer/event_form.html", categories=CATEGORIES, **_page_context())


@organizer_bp.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@organizer_required
def edit_event(event_id):
    event = _owned_event(event_id)
    if request.method == "POST":
        validate_csrf()
        if event.is_cancelled:
            flash("Cancelled events can't be edited.", "warning")
            return redirect(url_for("organizer.dashboard"))
        data, errors = validate_event_form(request.form, event=event)
        if errors:
            return render_template(
                "organizer/event_form.html", event=event.event_view(), categories=CATEGORIES,
                form_values=request.form, errors=errors, **_page_context(),
            ), 422
        for field, value in data.items():
            setattr(event, field, value)
        db.session.commit()
        flash("Your changes are saved.", "success")
        return redirect(url_for("events.detail", event_id=event.id))
    return render_template(
        "organizer/event_form.html", event=event.event_view(), categories=CATEGORIES,
        **_page_context(),
    )


@organizer_bp.post("/events/<int:event_id>/cancel")
@organizer_required
def cancel_event(event_id):
    validate_csrf()
    event = _owned_event(event_id)
    if not event.is_cancelled:
        # Soft cancel, like moderation: keep the rows, close registration.
        event.status = "cancelled"
        for registration in event.registrations:
            if registration.status in ("confirmed", "pending"):
                registration.status = "cancelled"
        db.session.commit()
        flash("The event is cancelled. Registration is closed.", "info")
    return redirect(url_for("organizer.dashboard"))


@organizer_bp.get("/events/<int:event_id>/stats")
@organizer_required
def statistics(event_id):
    event = _owned_event(event_id)
    return render_template(
        "organizer/stats.html",
        event=event.event_view(),
        stats=event_statistics(event),
        **_page_context(),
    )


@organizer_bp.errorhandler(403)
def organizer_access_required(error):
    return render_template(
        "errors/error.html", status=403, title="Organizer access needed",
        message="This page is for event organizers. Your account does not have organizer access.",
        return_url=url_for("auth.profile"), return_label="Go to profile",
    ), 403


@organizer_bp.errorhandler(404)
def event_not_found(error):
    return render_template(
        "errors/error.html", status=404, title="Event not found",
        message="This event isn't available in your organizer studio.",
        return_url=url_for("organizer.dashboard"), return_label="Organizer studio",
    ), 404
