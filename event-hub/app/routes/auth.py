# app/routes/auth.py
# Account pages. Backlog #1, #2, #22.
from urllib.parse import unquote

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from authlib.common.errors import AuthlibBaseError
from requests.exceptions import RequestException
from sqlalchemy.orm import joinedload

from app.extensions import db, oauth
from app.models import Event, Registration
from app.models.user import User
from app.security import csrf_token, validate_csrf
from app.services.event_service import campus_now

auth_bp = Blueprint("auth", __name__)


@auth_bp.context_processor
def account_context():
    return {"csrf_token": csrf_token}


YEAR_LEVELS = {
    "first_year",
    "second_year",
    "third_year",
    "fourth_year",
    "graduate",
    "unknown",
}


def safe_next(value):
    if not value or not isinstance(value, str):
        return None
    decoded = unquote(value)
    if (len(value) > 2048 or not value.startswith("/") or decoded.startswith("//")
            or "\\" in decoded or any(ord(char) < 32 or ord(char) == 127 for char in decoded)):
        return None
    return value


def allowed_domain():
    return current_app.config["ALLOWED_EMAIL_DOMAIN"].strip().lower()


def verified_scu_email(claims):
    """Return the lowercase SCU email, or None when Google's claims are not allowed."""
    if not isinstance(claims, dict):
        return None
    domain = allowed_domain()
    raw_email, raw_domain = claims.get("email"), claims.get("hd")
    if not isinstance(raw_email, str) or not isinstance(raw_domain, str):
        return None
    email = raw_email.strip().lower()
    hosted_domain = raw_domain.strip().lower()
    verified = claims.get("email_verified")
    verified_ok = verified is True or verified == "true"
    local, separator, host = email.partition("@")
    if not verified_ok or hosted_domain != domain or separator != "@":
        return None
    if not local or host != domain or len(email) > 255 or any(char.isspace() for char in email):
        return None
    return email


def fetch_google_claims():
    # Authlib validates the ID token using the saved OAuth state and nonce,
    # then attaches userinfo. Do not reparse a token without that nonce.
    token = oauth.google.authorize_access_token()
    claims = token.get("userinfo") if isinstance(token, dict) else None
    return claims if isinstance(claims, dict) else {}


def account_name(claims):
    name = claims.get("name") or claims.get("given_name") or "Bronco"
    if not isinstance(name, str):
        return "Bronco"
    name = " ".join(name.split())
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        return "Bronco"
    return name[:80] or "Bronco"


@auth_bp.route("/login")
def login():
    next_url = safe_next(request.args.get("next"))
    if current_user.is_authenticated:
        return redirect(next_url or url_for("auth.profile"))
    return render_template(
        "auth/login.html",
        errors={},
        form_values={},
        next_url=next_url or "",
    )


@auth_bp.route("/signup")
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("auth.profile"))
    return render_template("auth/signup.html", errors={}, form_values={})


@auth_bp.route("/auth/google")
def google_login():
    if not current_app.config.get("GOOGLE_CLIENT_ID"):
        flash("Google sign-in is not configured yet.", "error")
        return redirect(url_for("auth.login"))
    next_url = safe_next(request.args.get("next"))
    session["auth_next"] = next_url
    redirect_uri = url_for("auth.google_callback", _external=True)
    try:
        return oauth.google.authorize_redirect(redirect_uri, hd=allowed_domain())
    except (AuthlibBaseError, RequestException):
        session.pop("auth_next", None)
        flash("Google sign-in is temporarily unavailable. Please try again.", "error")
        return redirect(url_for("auth.login"))


@auth_bp.route("/auth/google/callback")
def google_callback():
    try:
        claims = fetch_google_claims()
    except (AuthlibBaseError, RequestException):
        session.pop("auth_next", None)
        flash("Google sign-in could not be completed. Try again.", "error")
        return redirect(url_for("auth.login"))

    email = verified_scu_email(claims)
    raw_sub = claims.get("sub") if isinstance(claims, dict) else None
    google_sub = raw_sub.strip() if isinstance(raw_sub, str) else ""
    if not email or not google_sub or len(google_sub) > 255:
        flash("Sign in with a verified Santa Clara University Google account.", "error")
        return redirect(url_for("auth.login"))

    user = db.session.scalar(db.select(User).filter_by(google_sub=google_sub))
    if user is None:
        taken = db.session.scalar(db.select(User).filter_by(email=email))
        if taken is not None:
            flash("That SCU email is already linked to a different Google account.", "error")
            return redirect(url_for("auth.login"))
        user = User(
            email=email,
            google_sub=google_sub,
            name=account_name(claims),
            role="student",
        )
        db.session.add(user)
        db.session.commit()

    destination = safe_next(session.pop("auth_next", None)) or url_for("auth.profile")
    # Keep only the checked destination across sign-in. Old anonymous/account
    # form tokens and cached session data must not survive the account change.
    session.clear()
    login_user(user)
    csrf_token()
    return redirect(destination)


@auth_bp.post("/logout")
@login_required
def logout():
    validate_csrf()
    session.clear()
    logout_user()
    csrf_token()
    return redirect(url_for("home"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    errors = {}
    form_values = {}
    if request.method == "POST":
        validate_csrf()
        name = (request.form.get("name") or "").strip()
        year_level = request.form.get("year_level") or "unknown"
        form_values = {"name": name, "year_level": year_level}
        if not name:
            errors["name"] = "Enter the name you want other Broncos to see."
        elif len(name) > 80:
            errors["name"] = "Use 80 characters or fewer."
        elif any(ord(char) < 32 or ord(char) == 127 for char in name):
            errors["name"] = "Enter your name on one line."
        if year_level not in YEAR_LEVELS:
            errors["year_level"] = "Choose a year level from the list."
        if not errors:
            current_user.name = name
            current_user.year_level = None if year_level == "unknown" else year_level
            db.session.commit()
            flash("Profile saved.", "success")
            return redirect(url_for("auth.profile"))

    return render_template(
        "auth/profile.html",
        errors=errors,
        form_values=form_values,
        profile_overview=_profile_overview(),
    ), 422 if errors else 200


@auth_bp.errorhandler(400)
def expired_account_form(error):
    signed_in = current_user.is_authenticated
    return render_template(
        "errors/error.html", status=400, title="Please try again",
        message=error.description,
        return_url=url_for("auth.profile" if signed_in else "auth.login"),
        return_label="Back to profile" if signed_in else "Back to sign in",
    ), 400


def _profile_overview():
    # Include ongoing events until they end, just like the event browser.
    registrations = db.session.scalars(
        db.select(Registration)
        .join(Registration.event)
        .where(
            Registration.student_id == current_user.id,
            Registration.status.in_(("confirmed", "pending")),
            Event.status == "published",
            Event.end_time > campus_now(),
        )
        .options(
            joinedload(Registration.event).joinedload(Event.organizer),
            joinedload(Registration.event).selectinload(Event.registrations),
            joinedload(Registration.payment),
            joinedload(Registration.ticket),
        )
        .order_by(Event.start_time, Registration.id)
    ).unique().all()
    confirmed = [row for row in registrations if row.is_confirmed]
    return {
        "upcoming_count": len(confirmed),
        "pending_count": sum(
            row.status == "pending" and not row.event.is_free
            for row in registrations
        ),
        "next_registration": confirmed[0].registration_view() if confirmed else None,
    }
