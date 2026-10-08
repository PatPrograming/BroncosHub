# app/routes/auth.py
# Account pages. Backlog #1, #2, #22.
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from authlib.integrations.base_client.errors import OAuthError

from app.extensions import db, oauth
from app.models.user import User

auth_bp = Blueprint("auth", __name__)

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
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    return value


def allowed_domain():
    return current_app.config["ALLOWED_EMAIL_DOMAIN"].strip().lower()


def verified_scu_email(claims):
    """Return the lowercase SCU email, or None when Google's claims are not allowed."""
    domain = allowed_domain()
    email = (claims.get("email") or "").strip().lower()
    hosted_domain = (claims.get("hd") or "").strip().lower()
    verified = claims.get("email_verified")
    verified_ok = verified is True or verified == "true"
    local, separator, host = email.partition("@")
    if not verified_ok or hosted_domain != domain or separator != "@":
        return None
    if not local or host != domain:
        return None
    return email


def fetch_google_claims():
    token = oauth.google.authorize_access_token()
    claims = token.get("userinfo")
    if not claims:
        claims = oauth.google.parse_id_token(token)
    return claims or {}


def account_name(claims):
    name = (claims.get("name") or claims.get("given_name") or "Bronco").strip()
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
    return oauth.google.authorize_redirect(redirect_uri, hd=allowed_domain())


@auth_bp.route("/auth/google/callback")
def google_callback():
    try:
        claims = fetch_google_claims()
    except OAuthError:
        flash("Google sign-in could not be completed. Try again.", "error")
        return redirect(url_for("auth.login"))

    email = verified_scu_email(claims)
    google_sub = (claims.get("sub") or "").strip()
    if not email or not google_sub:
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

    login_user(user)
    destination = safe_next(session.pop("auth_next", None)) or url_for("auth.profile")
    return redirect(destination)


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    logout_user()
    return redirect(url_for("home"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    errors = {}
    form_values = {}
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        year_level = request.form.get("year_level") or "unknown"
        form_values = {"name": name, "year_level": year_level}
        if not name:
            errors["name"] = "Enter the name you want other Broncos to see."
        elif len(name) > 80:
            errors["name"] = "Use 80 characters or fewer."
        if year_level not in YEAR_LEVELS:
            errors["year_level"] = "Choose a year level from the list."
        if not errors:
            current_user.name = name
            current_user.year_level = None if year_level == "unknown" else year_level
            db.session.commit()
            flash("Your profile is saved.", "success")
            return redirect(url_for("auth.profile"))

    return render_template(
        "auth/profile.html",
        errors=errors,
        form_values=form_values,
        profile_overview={"upcoming_count": 0, "pending_count": 0, "next_registration": None},
    )
