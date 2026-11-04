"""Organizer-owned Stripe setup. Financial details are entered on Stripe."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.extensions import db
from app.models.connected_account import ConnectedAccount
from app.routes.organizer import organizer_required
from app.security import csrf_token, validate_csrf
from app.services import stripe_gateway


payments_bp = Blueprint("payments", __name__, url_prefix="/organizer/payments")


@payments_bp.context_processor
def payments_context():
    return {"csrf_token": csrf_token}


@payments_bp.get("")
@organizer_required
def settings():
    account = db.session.scalar(db.select(ConnectedAccount).where(ConnectedAccount.user_id == current_user.id))
    return render_template("organizer/payments.html", account=account,
                           payments_configured=stripe_gateway.configured(),
                           active_nav="organizer")


@payments_bp.post("/onboard")
@organizer_required
def onboard():
    validate_csrf()
    try:
        countries = request.form.getlist("country")
        if len(countries) > 1:
            raise stripe_gateway.PaymentProviderError("Choose one payment account country.")
        link = stripe_gateway.begin_onboarding(current_user, countries[0] if countries else None)
    except stripe_gateway.PaymentProviderError as error:
        flash(str(error), "warning")
        return redirect(url_for("payments.settings"))
    return redirect(link["url"], code=303)


def _refresh():
    try:
        account = stripe_gateway.refresh_connected_account(current_user.id)
    except stripe_gateway.PaymentProviderError as error:
        flash(str(error), "warning")
    else:
        flash("Your account is ready to accept ticket payments." if account and account.ready
              else "Stripe still needs to finish your account setup or verification.",
              "success" if account and account.ready else "info")
    return redirect(url_for("payments.settings"))


@payments_bp.post("/refresh")
@organizer_required
def refresh():
    validate_csrf()
    return _refresh()


@payments_bp.get("/return")
@organizer_required
def onboarding_return():
    # Query parameters are deliberately ignored. Stripe's live API is authoritative.
    return _refresh()


@payments_bp.get("/expired")
@organizer_required
def expired():
    flash("That Stripe setup link expired. Continue setup below to get a new one.", "info")
    return redirect(url_for("payments.settings"))
