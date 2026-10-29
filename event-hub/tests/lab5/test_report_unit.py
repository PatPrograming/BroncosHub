"""Five Report.report_view tests; these do not test admin removal actions."""

from datetime import datetime

from app.models import Event, Report, User


def make_report(monkeypatch, **changes):
    # Event rendering is a collaborator, so give it a fixed unit-test result.
    monkeypatch.setattr(
        Event, "event_view", lambda self: {"id": self.id, "title": "Campus concert"}
    )
    values = {
        "id": 12,
        "event": Event(id=3),
        "reporter": User(name="Alex Chen"),
        "reason": "misleading",
        "details": "The advertised venue differs from the organizer's notice.",
        "status": "pending",
        "created_at": datetime(2026, 10, 22, 18, 5),
        "reviewed_by": None,
        "reviewed_at": None,
        "review_note": None,
    }
    values.update(changes)
    return Report(**values)


def test_known_reason_has_readable_label(monkeypatch):
    """Normal: a known report reason has a readable label."""
    # Normal: the saved reason code becomes a label in the review screen.
    view = make_report(monkeypatch).report_view()

    assert view["reason"] == "Misleading event information"
    assert view["reporter"] == "Alex Chen"
    assert view["event"] == {"id": 3, "title": "Campus concert"}
    assert view["date_label"] == "Oct 22, 2026 · 11:05 PDT"


def test_completed_review_includes_audit_details(monkeypatch):
    """Normal: a completed review shows the reviewer, time, and note."""
    # Normal: a resolved report retains who reviewed it, when, and why.
    view = make_report(
        monkeypatch,
        status="dismissed",
        reviewed_by=User(name="Jordan Admin"),
        reviewed_at=datetime(2026, 10, 22, 19, 10),
        review_note="The organizer confirmed the venue is correct.",
    ).report_view()

    assert view["status"] == "dismissed"
    assert view["reviewed_by"] == "Jordan Admin"
    assert view["reviewed_date_label"] == "Oct 22, 2026 · 12:10 PDT"
    assert view["review_note"] == "The organizer confirmed the venue is correct."


def test_pending_report_has_no_review_details(monkeypatch):
    """Edge: a pending report has no reviewer, review time, or note."""
    # Edge: an unreviewed report has no reviewer, review time, or review note.
    view = make_report(monkeypatch).report_view()

    assert view["status"] == "pending"
    assert view["reviewed_by"] is None
    assert view["reviewed_date_label"] is None
    assert view["review_note"] is None


def test_unknown_reason_is_preserved_as_fallback(monkeypatch):
    """Invalid: an unknown stored reason is preserved as a display fallback."""
    # Invalid stored value: display the original code instead of losing it.
    # This checks display fallback, not whether the submission route accepts it.
    view = make_report(monkeypatch, reason="unknown_reason").report_view()

    assert view["reason"] == "unknown_reason"
    assert view["status"] == "pending"


def test_missing_creation_date_has_no_date_label(monkeypatch):
    """Edge: an unsaved report without a creation date has no date label."""
    # Edge: an unsaved report may not have received its database timestamp yet.
    view = make_report(monkeypatch, created_at=None).report_view()

    assert view["date_label"] is None
    assert view["reason"] == "Misleading event information"
    assert view["details"] == "The advertised venue differs from the organizer's notice."
