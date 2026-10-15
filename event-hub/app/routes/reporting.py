"""A signed-in student can flag an event for admin review."""

from datetime import datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import insert, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Event, Report
from app.models.report import REPORT_REASONS
from app.security import csrf_token, validate_csrf


reporting_bp = Blueprint("reporting", __name__)


@reporting_bp.context_processor
def reporting_context():
    return {"csrf_token": csrf_token, "active_nav": "explore", "event_browsing_available": False}


def _existing_report(event_id):
    return db.session.scalar(select(Report).where(
        Report.event_id == event_id, Report.reporter_id == current_user.id
    ))


@reporting_bp.route("/events/<int:event_id>/report", methods=["GET", "POST"])
@login_required
def report_event(event_id):
    if current_user.role not in ("student", "organizer", "admin"):
        abort(403)
    if request.method == "POST":
        validate_csrf()
    event = db.session.scalar(select(Event).where(Event.id == event_id).options(
        joinedload(Event.organizer), selectinload(Event.registrations)
    ))
    if event is None:
        abort(404)
    if event.organizer_id == current_user.id:
        abort(403, description="You can't report your own event.")
    existing = _existing_report(event_id)
    if existing and request.method == "POST":
        flash("Your report is already saved. You only need to report an event once.", "info")
        return redirect(url_for("reporting.report_event", event_id=event_id))
    if not existing and event.status != "published":
        abort(409)
    errors, form_values = {}, {}
    if request.method == "POST":
        reason = request.form.get("reason", "")
        details = request.form.get("details", "").strip()
        form_values = {"reason": reason, "details": details}
        if reason not in dict(REPORT_REASONS):
            errors["reason"] = "Choose a reason for your report."
        if not 10 <= len(details) <= 2000:
            errors["details"] = "Explain the issue in 10 to 2,000 characters."
        if not errors:
            try:
                # Recheck publication in the insert itself so a simultaneous
                # removal cannot leave a new pending report on a closed event.
                created = db.session.execute(insert(Report).from_select(
                    ["event_id", "reporter_id", "reason", "details", "status", "created_at"],
                    select(Event.id, literal(current_user.id), literal(reason), literal(details),
                           literal("pending"), literal(datetime.now(timezone.utc).replace(tzinfo=None)))
                    .where(Event.id == event_id, Event.status == "published"),
                ))
                if not created.rowcount:
                    db.session.rollback()
                    abort(409)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                if _existing_report(event_id) is None:
                    raise
            flash("Your report has been sent to the admin review queue.", "success")
            return redirect(url_for("reporting.report_event", event_id=event_id))
    # Reporter confirmation deliberately excludes private admin review notes.
    own_report = {"id": existing.id, "status": existing.status,
                  "date_label": existing.created_at.strftime("%b %d, %Y · %H:%M UTC")} if existing else None
    return render_template(
        "events/report.html", event=event.event_view(),
        reasons=[{"value": value, "label": label} for value, label in REPORT_REASONS],
        errors=errors, form_values=form_values, existing_report=own_report,
        report_submitted=bool(existing),
    ), 422 if errors else 200


@reporting_bp.errorhandler(400)
@reporting_bp.errorhandler(403)
@reporting_bp.errorhandler(404)
@reporting_bp.errorhandler(409)
def reporting_error(error):
    titles = {400: "Please try again", 403: "Report unavailable", 404: "Event not found",
              409: "This event is already closed"}
    messages = {400: error.description, 403: "You can't report your own event or use this account to submit a report.",
                404: "This event isn't available.", 409: "Cancelled events don't accept new reports."}
    return render_template("errors/error.html", status=error.code, title=titles[error.code],
                           message=messages[error.code], return_url=url_for("auth.profile"),
                           return_label="Go to profile"), error.code
