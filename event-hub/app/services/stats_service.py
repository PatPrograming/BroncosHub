"""Organizer signup summaries (UC-008), using the shared event models."""

from sqlalchemy import func, select

from app.extensions import db
from app.models import Event, Payment, Registration, User


YEAR_LEVELS = (
    ("first_year", "First year"),
    ("second_year", "Second year"),
    ("third_year", "Third year"),
    ("fourth_year", "Fourth year"),
    ("graduate", "Graduate"),
    ("unknown", "Unknown"),
)


def _payment_summary(event_ids):
    """Sum saved successful payments that are not refunding or refunded.

    Use each recorded payment amount, never the current event price. Stripe
    cancellation moves payments out of this total as soon as a refund is queued;
    this is not a bank settlement balance. Historical mock payments stay saved.
    """
    revenue_cents = db.session.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0))
        .join(Registration, Payment.registration_id == Registration.id)
        .where(Registration.event_id.in_(event_ids), Payment.status == "succeeded")
    )
    return {
        "revenue_available": True,
        "revenue_cents": revenue_cents,
        "revenue_label": f"${revenue_cents / 100:,.2f}",
    }


def dashboard_statistics(events):
    """Summarize the owner's events, including historical signup records.

    The route must pass only events owned by the current user. Registrations
    should be eager-loaded when displaying several event cards together.
    """
    events = list(events)
    return {
        "events_count": sum(event.status == "published" for event in events),
        "registrations": sum(event.active_registration_count() for event in events),
        **_payment_summary([event.id for event in events]),
    }


def owner_dashboard_statistics(owner_id):
    """Keep whole-account totals independent of the visible event page."""
    owned_ids = select(Event.id).where(Event.organizer_id == owner_id)
    return {
        "events_count": db.session.scalar(
            select(func.count(Event.id)).where(
                Event.organizer_id == owner_id, Event.status == "published",
            )
        ),
        "registrations": db.session.scalar(
            select(func.count(Registration.id)).where(
                Registration.event_id.in_(owned_ids), Registration.status == "confirmed",
            )
        ),
        **_payment_summary(owned_ids),
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
        **_payment_summary([event.id]),
    }
