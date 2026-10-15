"""Reports and moderation preserve records and enforce account permissions."""

from datetime import datetime
from html.parser import HTMLParser
from itertools import count
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Event, Registration, Report, User
from app.services.stats_service import event_statistics


@pytest.fixture
def moderation_records():
    numbers = count(1)

    def user(role="student", **overrides):
        number = next(numbers)
        values = dict(
            name=f"Moderation Bronco {number}",
            email=f"moderation-{number}@scu.edu",
            google_sub=f"moderation-google-{number}",
            role=role,
        )
        values.update(overrides)
        row = User(**values)
        db.session.add(row)
        db.session.flush()
        return row

    def event(owner=None, **overrides):
        number = next(numbers)
        values = dict(
            organizer=owner or user("organizer"),
            title=f"Moderation campus event {number}",
            description="Fictional event for the moderation tests.",
            location="Test campus room",
            category="Social",
            start_time=datetime(2050, 10, 15, 18),
            end_time=datetime(2050, 10, 15, 20),
            capacity=10,
            price_cents=0,
            status="published",
        )
        values.update(overrides)
        row = Event(**values)
        db.session.add(row)
        db.session.flush()
        return row

    def report(item, reporter=None, **overrides):
        values = dict(
            event=item,
            reporter=reporter or user(),
            reason="misleading",
            details="The listed venue is different from the organizer's announcement.",
            status="pending",
        )
        values.update(overrides)
        row = Report(**values)
        db.session.add(row)
        db.session.flush()
        return row

    def registration(item, status="confirmed", **overrides):
        values = dict(event=item, student=user(), status=status)
        values.update(overrides)
        row = Registration(**values)
        db.session.add(row)
        db.session.flush()
        return row

    return SimpleNamespace(user=user, event=event, report=report, registration=registration)


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


class HiddenFields(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.fields = {}
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("type") == "hidden":
            self.fields[attributes.get("name")] = attributes.get("value")


def csrf_token(client, path):
    response = client.get(path)
    assert response.status_code == 200
    token = HiddenFields(response.get_data(as_text=True)).fields.get("csrf_token")
    assert token, "The form needs a hidden CSRF token."
    return token


def stored_snapshot():
    return {
        model.__tablename__: [
            tuple(getattr(row, column.name) for column in model.__table__.columns)
            for row in db.session.scalars(db.select(model).order_by(model.id))
        ]
        for model in (User, Event, Registration, Report)
    }


@pytest.fixture
def moderation_case(app, moderation_records):
    r = moderation_records
    with app.app_context():
        owner = r.user("organizer")
        student = r.user()
        admin = r.user("admin")
        item = r.event(owner)
        report = r.report(item, student)
        db.session.commit()
        return SimpleNamespace(
            owner=owner.id, student=student.id, admin=admin.id,
            event=item.id, report=report.id,
        )


@pytest.mark.parametrize("path", [
    "/events/1/report", "/admin/reports", "/admin/reports/1",
])
def test_moderation_guests_must_sign_in(client, path):
    response = client.get(path)
    assert response.status_code == 302
    assert "/login" in response.location


@pytest.mark.parametrize("path", [
    "/events/1/report", "/admin/reports/1/dismiss", "/admin/reports/1/remove",
])
def test_guests_cannot_change_moderation_records(client, path):
    response = client.post(path, data={"role": "admin"})
    assert response.status_code == 302
    assert "/login" in response.location


@pytest.mark.parametrize("role", ["student", "organizer"])
def test_non_admins_cannot_promote_themselves_to_review_reports(
    app, client, moderation_records, moderation_case, role,
):
    with app.app_context():
        account = moderation_records.user(role)
        db.session.commit()
        account_id = account.id
        before = stored_snapshot()
    sign_in(client, account_id)
    for path in ("/admin/reports", f"/admin/reports/{moderation_case.report}"):
        assert client.get(path + "?role=admin").status_code == 403
    for action in ("dismiss", "remove"):
        response = client.post(
            f"/admin/reports/{moderation_case.report}/{action}?role=admin",
            data={"role": "admin", "review_note": "This must not be accepted."},
        )
        assert response.status_code == 403
    with app.app_context():
        assert stored_snapshot() == before


@pytest.mark.parametrize("role", ["student", "organizer", "admin"])
def test_signed_in_accounts_can_report_someone_elses_event(
    app, client, moderation_records, role,
):
    with app.app_context():
        actor = moderation_records.user(role)
        item = moderation_records.event()
        db.session.commit()
        actor_id, event_id = actor.id, item.id
    sign_in(client, actor_id)
    path = f"/events/{event_id}/report"
    token = csrf_token(client, path)
    response = client.post(path, data={
        "csrf_token": token, "reason": "misleading",
        "details": "The event location does not match the announcement.",
        "reporter_id": "999999", "status": "removed", "reviewed_by_id": actor_id,
    })
    assert response.status_code == 302
    with app.app_context():
        report = db.session.scalar(db.select(Report))
        assert report.reporter_id == actor_id
        assert report.event_id == event_id
        assert report.reason == "misleading"
        assert report.status == "pending"
        assert report.created_at is not None
        assert report.reviewed_by_id is None
        assert report.reviewed_at is None
        assert report.review_note in (None, "")
        assert db.session.get(Event, event_id).status == "published"


@pytest.mark.parametrize("reason", ["spam", "misleading", "inappropriate", "other"])
def test_supported_report_reasons_are_accepted(app, client, moderation_records, reason):
    with app.app_context():
        actor, item = moderation_records.user(), moderation_records.event()
        db.session.commit()
        actor_id, event_id = actor.id, item.id
    sign_in(client, actor_id)
    path = f"/events/{event_id}/report"
    token = csrf_token(client, path)
    assert client.post(path, data={
        "csrf_token": token, "reason": reason, "details": "Please review this event listing.",
    }).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(Report)).reason == reason


@pytest.mark.parametrize("reason,details", [
    ("", "Please review the event details."),
    ("invented", "Please review the event details."),
    ("spam", ""),
    ("spam", "x" * 9),
    ("spam", " " * 20),
    ("spam", "x" * 2001),
], ids=["missing-reason", "unknown-reason", "empty", "too-short", "whitespace", "too-long"])
def test_report_fields_are_validated_on_server(
    app, client, moderation_records, reason, details,
):
    with app.app_context():
        actor, item = moderation_records.user(), moderation_records.event()
        db.session.commit()
        actor_id, event_id = actor.id, item.id
    sign_in(client, actor_id)
    path = f"/events/{event_id}/report"
    token = csrf_token(client, path)
    response = client.post(path, data={
        "csrf_token": token, "reason": reason, "details": details,
    })
    assert response.status_code == 422
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Report.id))) == 0


@pytest.mark.parametrize("length", [10, 2000])
def test_report_detail_limits_are_inclusive(app, client, moderation_records, length):
    with app.app_context():
        actor, item = moderation_records.user(), moderation_records.event()
        db.session.commit()
        actor_id, event_id = actor.id, item.id
    sign_in(client, actor_id)
    path = f"/events/{event_id}/report"
    token = csrf_token(client, path)
    assert client.post(path, data={
        "csrf_token": token, "reason": "other", "details": "x" * length,
    }).status_code == 302
    with app.app_context():
        assert len(db.session.scalar(db.select(Report)).details) == length


@pytest.mark.parametrize("token", [None, "not-the-session-token"])
def test_reporting_requires_csrf(app, client, moderation_records, token):
    with app.app_context():
        actor, item = moderation_records.user(), moderation_records.event()
        db.session.commit()
        actor_id, event_id = actor.id, item.id
    sign_in(client, actor_id)
    path = f"/events/{event_id}/report"
    csrf_token(client, path)
    data = {"reason": "spam", "details": "Please review this event listing."}
    if token is not None:
        data["csrf_token"] = token
    assert client.post(path, data=data).status_code == 400
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Report.id))) == 0


def test_a_csrf_token_from_another_session_is_not_accepted(app, client, moderation_records):
    with app.app_context():
        actor, item = moderation_records.user(), moderation_records.event()
        db.session.commit()
        actor_id, event_id = actor.id, item.id
    other_client = app.test_client()
    sign_in(client, actor_id)
    sign_in(other_client, actor_id)
    path = f"/events/{event_id}/report"
    token = csrf_token(other_client, path)
    assert client.post(path, data={
        "csrf_token": token, "reason": "spam", "details": "Please review this listing.",
    }).status_code == 400
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Report.id))) == 0


def test_organizers_cannot_report_their_own_events(
    app, client, moderation_records, moderation_case,
):
    with app.app_context():
        other = moderation_records.event()
        db.session.commit()
        other_id = other.id
    sign_in(client, moderation_case.owner)
    token = csrf_token(client, f"/events/{other_id}/report")
    path = f"/events/{moderation_case.event}/report"
    assert client.get(path).status_code == 403
    assert client.post(path, data={
        "csrf_token": token, "reason": "spam", "details": "My own event listing.",
    }).status_code == 403
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Report.id))) == 1


def test_missing_and_cancelled_events_cannot_receive_new_reports(
    app, client, moderation_records,
):
    with app.app_context():
        actor = moderation_records.user()
        cancelled = moderation_records.event(status="cancelled")
        active = moderation_records.event()
        db.session.commit()
        actor_id, cancelled_id, active_id = actor.id, cancelled.id, active.id
    sign_in(client, actor_id)
    token = csrf_token(client, f"/events/{active_id}/report")
    for event_id, expected in ((999999, 404), (cancelled_id, 409)):
        path = f"/events/{event_id}/report"
        assert client.get(path).status_code == expected
        assert client.post(path, data={
            "csrf_token": token, "reason": "spam", "details": "Please review this listing.",
        }).status_code == expected
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Report.id))) == 0


def test_duplicate_submission_preserves_original_report_and_review(
    app, client, moderation_records,
):
    with app.app_context():
        actor, admin = moderation_records.user(), moderation_records.user("admin")
        item = moderation_records.event()
        db.session.commit()
        actor_id, admin_id, event_id = actor.id, admin.id, item.id
    sign_in(client, actor_id)
    path = f"/events/{event_id}/report"
    token = csrf_token(client, path)
    original = "The listing has an incorrect venue."
    assert client.post(path, data={
        "csrf_token": token, "reason": "misleading", "details": original,
    }).status_code == 302
    with app.app_context():
        report = db.session.scalar(db.select(Report))
        report.status = "dismissed"
        report.reviewed_by_id = admin_id
        report.reviewed_at = datetime(2026, 10, 14, 12)
        report.review_note = "The venue was checked and is correct."
        db.session.commit()
        before = stored_snapshot()
    confirmation = client.get(path)
    assert confirmation.status_code == 200
    assert b"The venue was checked and is correct." not in confirmation.data
    assert client.post(path, data={
        "csrf_token": token, "reason": "spam", "details": "An attempted replacement report.",
    }).status_code == 302
    with app.app_context():
        assert stored_snapshot() == before


def test_database_prevents_duplicate_reports_per_person_and_event(app, moderation_records):
    with app.app_context():
        actor, item = moderation_records.user(), moderation_records.event()
        moderation_records.report(item, actor)
        db.session.commit()
        with pytest.raises(IntegrityError):
            moderation_records.report(item, actor)
        db.session.rollback()
        assert db.session.scalar(db.select(db.func.count(Report.id))) == 1


def test_admin_queue_filters_reports_and_reads_do_not_change_records(
    app, client, moderation_records, moderation_case,
):
    with app.app_context():
        closed_event = moderation_records.event(title="Previously reviewed campus event")
        moderation_records.report(
            closed_event, status="dismissed", reviewed_by_id=moderation_case.admin,
            reviewed_at=datetime(2026, 10, 14, 12), review_note="This listing was checked.",
        )
        db.session.commit()
        before = stored_snapshot()
        pending_title = db.session.get(Event, moderation_case.event).title
    sign_in(client, moderation_case.admin)
    pending = client.get("/admin/reports?status=pending")
    resolved = client.get("/admin/reports?status=resolved")
    detail = client.get(f"/admin/reports/{moderation_case.report}")
    assert pending.status_code == resolved.status_code == detail.status_code == 200
    assert pending_title in pending.get_data(as_text=True)
    assert "Previously reviewed campus event" not in pending.get_data(as_text=True)
    assert "Previously reviewed campus event" in resolved.get_data(as_text=True)
    assert pending_title not in resolved.get_data(as_text=True)
    assert client.get("/admin/reports?status=not-a-status").status_code == 400
    assert client.get("/admin/reports/999999").status_code == 404
    with app.app_context():
        assert stored_snapshot() == before


@pytest.mark.parametrize("action", ["dismiss", "remove"])
@pytest.mark.parametrize("token", [None, "incorrect-token"])
def test_admin_changes_require_csrf(app, client, moderation_case, action, token):
    sign_in(client, moderation_case.admin)
    csrf_token(client, f"/admin/reports/{moderation_case.report}")
    data = {"review_note": "The review has been completed."}
    if token is not None:
        data["csrf_token"] = token
    with app.app_context():
        before = stored_snapshot()
    assert client.post(
        f"/admin/reports/{moderation_case.report}/{action}", data=data,
    ).status_code == 400
    with app.app_context():
        assert stored_snapshot() == before


@pytest.mark.parametrize("action", ["dismiss", "remove"])
@pytest.mark.parametrize(
    "note", ["", "x" * 9, " " * 20, "x" * 1001],
    ids=["empty", "too-short", "whitespace", "too-long"],
)
def test_admin_review_note_limits_are_enforced_on_server(
    app, client, moderation_case, action, note,
):
    sign_in(client, moderation_case.admin)
    token = csrf_token(client, f"/admin/reports/{moderation_case.report}")
    with app.app_context():
        before = stored_snapshot()
    response = client.post(f"/admin/reports/{moderation_case.report}/{action}", data={
        "csrf_token": token, "review_note": note,
    })
    assert response.status_code == 422
    with app.app_context():
        assert stored_snapshot() == before


@pytest.mark.parametrize("action", ["dismiss", "remove"])
def test_review_actions_cannot_be_triggered_with_get(app, client, moderation_case, action):
    sign_in(client, moderation_case.admin)
    with app.app_context():
        before = stored_snapshot()
    assert client.get(f"/admin/reports/{moderation_case.report}/{action}").status_code == 405
    with app.app_context():
        assert stored_snapshot() == before


def test_dismissing_a_report_keeps_event_registrations_and_other_reports(
    app, client, moderation_records, moderation_case,
):
    with app.app_context():
        item = db.session.get(Event, moderation_case.event)
        other = moderation_records.report(item)
        registration = moderation_records.registration(item)
        db.session.commit()
        other_id, registration_id = other.id, registration.id
    sign_in(client, moderation_case.admin)
    token = csrf_token(client, f"/admin/reports/{moderation_case.report}")
    note = "The organizer verified that the event listing is accurate."
    assert client.post(f"/admin/reports/{moderation_case.report}/dismiss", data={
        "csrf_token": token, "review_note": note,
    }).status_code == 302
    with app.app_context():
        report = db.session.get(Report, moderation_case.report)
        assert report.status == "dismissed"
        assert report.reviewed_by_id == moderation_case.admin
        assert report.reviewed_at is not None
        assert report.review_note == note
        assert db.session.get(Report, other_id).status == "pending"
        assert db.session.get(Event, moderation_case.event).status == "published"
        assert db.session.get(Registration, registration_id).status == "confirmed"


def test_removal_cancels_active_signups_resolves_pending_reports_and_preserves_history(
    app, client, moderation_records, moderation_case,
):
    with app.app_context():
        item = db.session.get(Event, moderation_case.event)
        registrations = {
            status: moderation_records.registration(item, status=status).id
            for status in ("confirmed", "pending", "failed", "cancelled")
        }
        second_pending = moderation_records.report(item)
        historical = moderation_records.report(
            item, status="dismissed", reviewed_by_id=moderation_case.admin,
            reviewed_at=datetime(2026, 10, 13, 12), review_note="Earlier review remains on record.",
        )
        unrelated = moderation_records.event()
        unrelated_report = moderation_records.report(unrelated)
        unrelated_registration = moderation_records.registration(unrelated)
        db.session.commit()
        second_id, historical_id = second_pending.id, historical.id
        unrelated_id = unrelated.id
        unrelated_report_id, unrelated_registration_id = unrelated_report.id, unrelated_registration.id
        original_details = db.session.get(Report, moderation_case.report).details
        assert event_statistics(item)["registrations"] == 1
        original_count = db.session.scalar(db.select(db.func.count(Registration.id)))
    sign_in(client, moderation_case.admin)
    token = csrf_token(client, f"/admin/reports/{moderation_case.report}")
    note = "The event listing violates the campus event rules."
    assert client.post(f"/admin/reports/{moderation_case.report}/remove", data={
        "csrf_token": token, "review_note": note,
    }).status_code == 302
    with app.app_context():
        item = db.session.get(Event, moderation_case.event)
        assert item.status == "cancelled"
        assert event_statistics(item)["registrations"] == 0
        assert item.spots_left() == item.capacity
        reports = [db.session.get(Report, report_id) for report_id in (moderation_case.report, second_id)]
        for report in reports:
            assert report.status == "removed"
            assert report.reviewed_by_id == moderation_case.admin
            assert report.reviewed_at is not None
            assert report.review_note == note
        assert reports[0].reviewed_at == reports[1].reviewed_at
        assert reports[0].details == original_details
        old = db.session.get(Report, historical_id)
        assert old.status == "dismissed"
        assert old.reviewed_at == datetime(2026, 10, 13, 12)
        assert old.review_note == "Earlier review remains on record."
        for status, registration_id in registrations.items():
            expected = "cancelled" if status in ("confirmed", "pending") else status
            assert db.session.get(Registration, registration_id).status == expected
        assert db.session.scalar(db.select(db.func.count(Registration.id))) == original_count
        assert db.session.get(Event, unrelated_id).status == "published"
        assert db.session.get(Report, unrelated_report_id).status == "pending"
        assert db.session.get(Registration, unrelated_registration_id).status == "confirmed"
    # The original reporter can still see the outcome after the event is removed.
    sign_in(client, moderation_case.student)
    assert client.get(f"/events/{moderation_case.event}/report").status_code == 200


@pytest.mark.parametrize("original_action", ["dismiss", "remove"])
def test_repeated_or_stale_review_cannot_overwrite_audit_or_change_event(
    app, client, moderation_records, moderation_case, original_action,
):
    with app.app_context():
        another_admin = moderation_records.user("admin")
        db.session.commit()
        another_admin_id = another_admin.id
    second_client = app.test_client()
    sign_in(second_client, another_admin_id)
    second_token = csrf_token(second_client, f"/admin/reports/{moderation_case.report}")
    sign_in(client, moderation_case.admin)
    token = csrf_token(client, f"/admin/reports/{moderation_case.report}")
    assert client.post(f"/admin/reports/{moderation_case.report}/{original_action}", data={
        "csrf_token": token, "review_note": "The original moderation decision.",
    }).status_code == 302
    with app.app_context():
        before = stored_snapshot()
    # Both admins loaded a pending report; each retains their own valid token.
    for browser, browser_token in ((client, token), (second_client, second_token)):
        for action in ("dismiss", "remove"):
            assert browser.post(f"/admin/reports/{moderation_case.report}/{action}", data={
                "csrf_token": browser_token, "review_note": "A later conflicting moderation decision.",
            }).status_code == 302
    with app.app_context():
        assert stored_snapshot() == before


@pytest.mark.parametrize("action", ["dismiss", "remove"])
@pytest.mark.parametrize("length", [10, 1000])
def test_review_note_limits_are_inclusive(app, client, moderation_case, action, length):
    sign_in(client, moderation_case.admin)
    token = csrf_token(client, f"/admin/reports/{moderation_case.report}")
    assert client.post(f"/admin/reports/{moderation_case.report}/{action}", data={
        "csrf_token": token, "review_note": "x" * length,
    }).status_code == 302
    with app.app_context():
        assert len(db.session.get(Report, moderation_case.report).review_note) == length


def test_user_content_is_escaped_when_admin_reviews_reports(
    app, client, moderation_records, moderation_case,
):
    payload = '<script>alert("not executable")</script>'
    with app.app_context():
        item = db.session.get(Event, moderation_case.event)
        item.title = payload
        report = db.session.get(Report, moderation_case.report)
        report.details = payload
        reporter = db.session.get(User, moderation_case.student)
        reporter.name = payload
        db.session.commit()
    sign_in(client, moderation_case.admin)
    for path in ("/admin/reports", f"/admin/reports/{moderation_case.report}"):
        response = client.get(path)
        assert response.status_code == 200
        assert payload not in response.get_data(as_text=True)
        assert "&lt;script&gt;" in response.get_data(as_text=True)
    token = csrf_token(client, f"/admin/reports/{moderation_case.report}")
    assert client.post(f"/admin/reports/{moderation_case.report}/dismiss", data={
        "csrf_token": token, "review_note": payload,
    }).status_code == 302
    response = client.get(f"/admin/reports/{moderation_case.report}")
    assert payload not in response.get_data(as_text=True)
    assert "&lt;script&gt;" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Report, moderation_case.report).review_note == payload
