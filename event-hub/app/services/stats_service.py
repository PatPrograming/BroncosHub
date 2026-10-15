"""Organizer signup summaries (UC-008), using the shared event models."""

from sqlalchemy import func, select

from app.extensions import db
from app.models import Registration, User


YEAR_LEVELS = (
    ("first_year", "First year"),
    ("second_year", "Second year"),
    ("third_year", "Third year"),
    ("fourth_year", "Fourth year"),
    ("graduate", "Graduate"),
    ("unknown", "Unknown"),
)


def _payment_summary():
    # Payment is still a scaffold. A ticket price or signup is not payment proof.
    return {
        "revenue_available": False,
        "revenue_cents": None,
        "revenue_label": "—",
    }


def dashboard_statistics(events):
    """Summarize the owner's events, including historical signup records.

    The route must pass only events owned by the current user. Registrations
    should be eager-loaded when displaying several event cards together.
    """
    return {
        "events_count": sum(event.status == "published" for event in events),
        "registrations": sum(event.active_registration_count() for event in events),
        **_payment_summary(),
    }


def event_statistics(event):
    """Count confirmed registrations; optional/unrecognized years are Unknown."""
    rows = db.session.execute(
        select(User.year_level, func.count(Registration.id))
        .select_from(Registration)
        .outerjoin(User, Registration.student_id == User.id)
        .where(Registration.event_id == event.id, Registration.status == "confirmed")
        .group_by(User.year_level)
    )
    counts = {key: 0 for key, _ in YEAR_LEVELS}
    for year, count in rows:
        counts[year if year in counts else "unknown"] += count

    total = sum(counts.values())
    return {
        "registrations": total,
        "year_breakdown": [
            {"label": label, "count": counts[key],
             "percent": counts[key] / total * 100 if total else 0}
            for key, label in YEAR_LEVELS
        ],
        **_payment_summary(),
    }
