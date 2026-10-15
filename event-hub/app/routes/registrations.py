# app/routes/registrations.py
# Student registration pages. UC-002 to UC-005. Backlog #9, #10, #13-#18.
from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Registration
from app.models.event import Event
from app.services.payment_service import (
    CheckoutNotAllowed,
    UnrecognizedCard,
    complete_purchase,
)

registrations_bp = Blueprint("registrations", __name__)


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


@registrations_bp.route("/registrations/<int:registration_id>/checkout", methods=["GET", "POST"])
@login_required
def checkout(registration_id):
    registration = _owned_registration(registration_id)
    view = registration.registration_view()
    if request.method == "POST":
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
        except CheckoutNotAllowed:
            return render_template(
                "registrations/checkout.html",
                registration=view,
                errors={},
                active_nav="my-events",
            )
        return redirect(url_for("registrations.detail", registration_id=registration.id))
    return render_template(
        "registrations/checkout.html",
        registration=view,
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
        active_nav="my-events",
    )
