# app/routes/registrations.py
# Student registration pages. UC-002 to UC-005. Backlog #9, #10, #13-#18.
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Registration
from app.models.event import Event
from app.security import csrf_token, validate_csrf
from app.services.event_service import campus_now
from app.services.payment_service import (
    CheckoutNotAllowed,
    UnrecognizedCard,
    checkout_unavailable_reason,
    complete_purchase,
)
from app.services.registration_service import (
    CancellationNotAllowed,
    RegistrationRefused,
    find_registration,
    register_student,
    cancel_free_registration,
)

registrations_bp = Blueprint("registrations", __name__)


@registrations_bp.context_processor
def registrations_context():
    # Templates add a hidden csrf_token field only when this is available.
    return {"csrf_token": csrf_token}


def _owned_registration(registration_id):
    registration = db.session.scalar(
        db.select(Registration)
        .where(Registration.id == registration_id)
        .options(
            joinedload(Registration.event).joinedload(Event.organizer),
            joinedload(Registration.payment),
            joinedload(Registration.ticket),
        )
    )
    if registration is None or registration.student_id != current_user.id:
        abort(404)
    return registration


@registrations_bp.route("/events/<int:event_id>/register", methods=["GET", "POST"])
@login_required
def register(event_id):
    event = db.session.scalar(
        db.select(Event)
        .where(Event.id == event_id)
        .options(joinedload(Event.organizer), selectinload(Event.registrations))
    )
    if event is None:
        abort(404)

    if request.method == "POST":
        validate_csrf()
        try:
            registration = register_student(current_user, event)
        except RegistrationRefused as refusal:
            flash(str(refusal), "warning")
            return redirect(url_for("events.detail", event_id=event.id))
        if registration.status in ("pending", "failed"):
            return redirect(url_for("registrations.checkout", registration_id=registration.id))
        flash("You're registered. See you there!", "success")
        return redirect(url_for("registrations.detail", registration_id=registration.id))

    view = event.event_view()
    view["ended"] = event.end_time <= campus_now()
    existing = find_registration(current_user, event)
    view["registered"] = existing is not None and existing.status != "cancelled"
    return render_template("registrations/confirm.html", event=view, active_nav="explore")


@registrations_bp.get("/my-registrations")
@login_required
def my_registrations():
    registrations = db.session.scalars(
        db.select(Registration)
        .join(Registration.event)
        .where(Registration.student_id == current_user.id)
        .options(
            joinedload(Registration.event).joinedload(Event.organizer),
            joinedload(Registration.event).selectinload(Event.registrations),
            joinedload(Registration.payment),
            joinedload(Registration.ticket),
        )
        .order_by(Event.start_time)
    ).unique().all()
    return render_template(
        "registrations/my_registrations.html",
        registrations=[registration.registration_view() for registration in registrations],
        active_nav="my-events",
    )


@registrations_bp.post("/my-registrations/<int:registration_id>/cancel")
@login_required
def cancel(registration_id):
    validate_csrf()
    registration = _owned_registration(registration_id)
    try:
        cancel_free_registration(current_user, registration)
    except CancellationNotAllowed as refusal:
        flash(str(refusal), "warning")
    else:
        flash("Your registration is cancelled.", "info")
    return redirect(url_for("registrations.detail", registration_id=registration.id))


@registrations_bp.route("/registrations/<int:registration_id>/checkout", methods=["GET", "POST"])
@login_required
def checkout(registration_id):
    registration = _owned_registration(registration_id)
    view = registration.registration_view()
    if request.method == "POST":
        validate_csrf()
        already_paid = (
            registration.payment is not None and registration.payment.status == "succeeded"
        )
        if already_paid:
            return redirect(url_for("registrations.detail", registration_id=registration.id))
        try:
            complete_purchase(registration, request.form.get("card_number", ""))
        except UnrecognizedCard:
            return render_template(
                "registrations/checkout.html",
                registration=view,
                errors={"card_number": "Use a test card starting with 4242, 4000, or 4999."},
                active_nav="my-events",
            ), 422
        except CheckoutNotAllowed as refusal:
            return render_template(
                "registrations/checkout.html",
                registration=view,
                checkout_closed_message=str(refusal),
                errors={},
                active_nav="my-events",
            ), 409
        return redirect(url_for("registrations.detail", registration_id=registration.id))
    return render_template(
        "registrations/checkout.html",
        registration=view,
        checkout_closed_message=checkout_unavailable_reason(registration),
        errors={},
        active_nav="my-events",
    )


@registrations_bp.route("/my-registrations/<int:registration_id>")
@login_required
def detail(registration_id):
    registration = _owned_registration(registration_id)
    return render_template(
        "registrations/detail.html",
        registration=registration.registration_view(),
        checkout_closed_message=checkout_unavailable_reason(registration),
        active_nav="my-events",
    )
