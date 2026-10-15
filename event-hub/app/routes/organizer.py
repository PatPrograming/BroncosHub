"""Owner-only organizer dashboard and statistics. Event editing is separate."""

from functools import wraps
from datetime import datetime, timezone

from flask import Blueprint, abort, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, case, select
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Event
from app.services.stats_service import dashboard_statistics, event_statistics


organizer_bp = Blueprint("organizer", __name__, url_prefix="/organizer")


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


def _page_context():
    # Enable these when the shared public-detail and event-editing routes land.
    return {
        "active_nav": "organizer",
        "event_management_available": False,
        "event_browsing_available": False,
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


@organizer_bp.get("/events/<int:event_id>/stats")
@organizer_required
def statistics(event_id):
    event = db.session.scalar(_owned_events().where(Event.id == event_id))
    if event is None:
        abort(404)
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
