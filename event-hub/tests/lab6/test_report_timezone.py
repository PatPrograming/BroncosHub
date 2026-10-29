"""Five new cases for Report's Pacific-time display in Lab 6."""

from datetime import datetime

from app.models import Event, Report, User


def make_report(monkeypatch, created_at, reviewed_at=None):
    # Keep event rendering separate from the report timestamp behavior.
    monkeypatch.setattr(
        Event, "event_view", lambda self: {"id": self.id, "title": "Campus concert"}
    )
    return Report(
        id=12,
        event=Event(id=3),
        reporter=User(name="Alex Chen"),
        reason="misleading",
        details="The advertised venue differs from the organizer's notice.",
        status="dismissed" if reviewed_at is not None else "pending",
        created_at=created_at,
        reviewed_at=reviewed_at,
        reviewed_by=User(name="Jordan Admin") if reviewed_at is not None else None,
        review_note="The organizer confirmed the venue." if reviewed_at is not None else None,
    )


def test_summer_dates_display_pdt_without_changing_stored_utc(monkeypatch):
    """N1: creation and review times use PDT; stored UTC values stay unchanged."""
    created_at = datetime(2026, 7, 15, 18, 5)
    reviewed_at = datetime(2026, 7, 15, 19, 10)
    report = make_report(monkeypatch, created_at, reviewed_at)

    view = report.report_view()

    assert view["date_label"] == "Jul 15, 2026 · 11:05 PDT"
    assert view["reviewed_date_label"] == "Jul 15, 2026 · 12:10 PDT"
    assert report.created_at == created_at
    assert report.reviewed_at == reviewed_at
    assert report.created_at.tzinfo is None
    assert report.reviewed_at.tzinfo is None


def test_winter_date_displays_pst_and_pending_review_stays_empty(monkeypatch):
    """N2: winter uses PST, and an unreviewed report still has no review time."""
    view = make_report(monkeypatch, datetime(2026, 1, 15, 18, 5)).report_view()

    assert view["date_label"] == "Jan 15, 2026 · 10:05 PST"
    assert view["status"] == "pending"
    assert view["reviewed_date_label"] is None
    assert view["reviewed_by"] is None


def test_conversion_can_move_date_into_previous_year(monkeypatch):
    """N3: a UTC New Year's timestamp can still be December 31 in California."""
    view = make_report(monkeypatch, datetime(2026, 1, 1, 1, 30)).report_view()

    assert view["date_label"] == "Dec 31, 2025 · 17:30 PST"


def test_spring_transition_skips_the_missing_local_hour(monkeypatch):
    """N4: one UTC minute crosses from 01:59 PST to 03:00 PDT."""
    report = make_report(
        monkeypatch,
        datetime(2026, 3, 8, 9, 59),
        datetime(2026, 3, 8, 10, 0),
    )

    view = report.report_view()

    assert view["date_label"] == "Mar 08, 2026 · 01:59 PST"
    assert view["reviewed_date_label"] == "Mar 08, 2026 · 03:00 PDT"


def test_fall_transition_labels_the_repeated_hour_unambiguously(monkeypatch):
    """N5: the two local 01:30 times are distinguished by PDT and PST."""
    report = make_report(
        monkeypatch,
        datetime(2026, 11, 1, 8, 30),
        datetime(2026, 11, 1, 9, 30),
    )

    view = report.report_view()

    assert view["date_label"] == "Nov 01, 2026 · 01:30 PDT"
    assert view["reviewed_date_label"] == "Nov 01, 2026 · 01:30 PST"
