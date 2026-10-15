"""Admin-only reported-event review (backlog #29)."""

from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Event, Report
from app.security import csrf_token, validate_csrf
from app.services.moderation_service import resolve_report


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.role != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@admin_bp.context_processor
def moderation_context():
    return {"active_nav": "admin", "moderation_enabled": True,
            "event_browsing_available": False, "csrf_token": csrf_token}


def _reports_query():
    return select(Report).options(
        joinedload(Report.event).joinedload(Event.organizer),
        joinedload(Report.event).selectinload(Event.registrations),
        joinedload(Report.reporter), joinedload(Report.reviewed_by),
    )


def _find_report(report_id):
    report = db.session.scalar(_reports_query().where(Report.id == report_id))
    if report is None:
        abort(404)
    return report


@admin_bp.get("/reports")
@admin_required
def reports():
    filter_status = request.args.get("status", "pending")
    if filter_status not in ("pending", "resolved"):
        abort(400, description="Choose pending or resolved reports.")
    statuses = ("pending",) if filter_status == "pending" else ("dismissed", "removed")
    query = _reports_query().where(Report.status.in_(statuses))
    query = query.order_by(Report.created_at, Report.id) if filter_status == "pending" else query.order_by(
        Report.reviewed_at.desc(), Report.id.desc()
    )
    counts = {"pending": 0, "resolved": 0}
    for status, count in db.session.execute(select(Report.status, func.count()).group_by(Report.status)):
        counts["pending" if status == "pending" else "resolved"] += count
    return render_template("admin/reports.html",
                           reports=[row.report_view() for row in db.session.scalars(query)],
                           counts=counts, filter_status=filter_status)


@admin_bp.get("/reports/<int:report_id>")
@admin_required
def report_detail(report_id):
    report = _find_report(report_id).report_view()
    return render_template("admin/report_detail.html", report=report, event=report["event"],
                           errors={}, form_values={}, attempted_action=None)


def _resolve(report_id, action):
    validate_csrf()
    report = _find_report(report_id)
    if report.status != "pending":
        flash("This report has already been reviewed. The saved decision was kept.", "info")
        return redirect(url_for("admin.report_detail", report_id=report_id))
    note = request.form.get("review_note", "").strip()
    if not 10 <= len(note) <= 1000:
        view = report.report_view()
        return render_template(
            "admin/report_detail.html", report=view, event=view["event"],
            errors={"review_note": "Add a short explanation between 10 and 1,000 characters."},
            form_values={"review_note": note}, attempted_action=action,
        ), 422
    if resolve_report(report, current_user, action, note):
        flash("Event removed and active registrations cancelled. All pending reports for this event were resolved."
              if action == "remove" else "Report dismissed. The event was left unchanged.", "success")
    else:
        flash("Another review was saved first. The existing decision was kept.", "info")
    return redirect(url_for("admin.report_detail", report_id=report_id))


@admin_bp.post("/reports/<int:report_id>/dismiss")
@admin_required
def dismiss_report(report_id):
    return _resolve(report_id, "dismiss")


@admin_bp.post("/reports/<int:report_id>/remove")
@admin_required
def remove_report(report_id):
    return _resolve(report_id, "remove")


@admin_bp.errorhandler(400)
@admin_bp.errorhandler(403)
@admin_bp.errorhandler(404)
def moderation_error(error):
    titles = {400: "Please try again", 403: "Admin access needed", 404: "Report not found"}
    messages = {400: error.description, 403: "Only admins can review event reports.",
                404: "This report isn't available. It may have been removed from the system."}
    is_admin = current_user.is_authenticated and current_user.role == "admin"
    return render_template(
        "errors/error.html", status=error.code, title=titles[error.code], message=messages[error.code],
        return_url=url_for("admin.reports" if is_admin else "auth.profile"),
        return_label="Back to reports" if is_admin else "Go to profile",
    ), error.code
