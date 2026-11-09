"""Support information and organizer contact, without publishing account emails."""

import re
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, Response, abort, current_app, render_template
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Event


support_bp = Blueprint("support", __name__)
DEFAULT_SUPPORT_EMAIL = "ecan@scu.edu"
# Search Console HTML-file method requires this exact path on the site root:
# https://broncoshub.onrender.com/google70205f6885e476e3.html
GOOGLE_SITE_VERIFICATION_FILE = "google70205f6885e476e3.html"
GOOGLE_SITE_VERIFICATION_BODY = "google-site-verification: google70205f6885e476e3.html\n"


def _email(value):
    """Accept a simple address only; never let headers enter a mailto link."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if len(value) > 255 or not re.fullmatch(
        r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,63}", value
    ):
        return None
    return value


def _mailto(email):
    return "mailto:" + quote(email, safe="@.")


@support_bp.app_context_processor
def support_context():
    email = _email(current_app.config.get("SUPPORT_EMAIL")) or DEFAULT_SUPPORT_EMAIL
    return {"support_available": True, "support_email": email, "support_mailto": _mailto(email)}


@support_bp.get("/support")
def support():
    return render_template("support/index.html")


@support_bp.get("/privacy")
def privacy():
    return render_template("support/privacy.html")


@support_bp.get("/terms")
def terms():
    return render_template("support/terms.html")


@support_bp.get(f"/{GOOGLE_SITE_VERIFICATION_FILE}")
def google_search_console_verification():
    # Serve at the domain root (not /static/...). Prefer the package file, fall
    # back to the known token so ownership checks never 404 after deploy.
    path = Path(__file__).resolve().parent.parent / GOOGLE_SITE_VERIFICATION_FILE
    body = path.read_text(encoding="utf-8") if path.is_file() else GOOGLE_SITE_VERIFICATION_BODY
    if "google-site-verification:" not in body:
        abort(404)
    return Response(
        body if body.endswith("\n") else body + "\n",
        mimetype="text/html; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


@support_bp.get("/events/<int:event_id>/contact")
@login_required
def organizer_contact(event_id):
    # All verified SCU students may host and attend. A stored session alone must
    # not expose contacts to an unlinked or out-of-domain legacy account.
    if not current_user.can_organize:
        abort(403)
    event = db.session.scalar(
        db.select(Event).where(Event.id == event_id).options(joinedload(Event.organizer))
    )
    # Removed listing content stays hidden, including on this secondary route.
    # Those attendees can use site support from their retained receipt instead.
    if event is None or event.status not in {"published", "cancelled"}:
        abort(404)
    organizer = event.organizer
    email = _email(organizer.email) if organizer and organizer.can_organize else None
    return render_template(
        "support/organizer.html", event=event,
        organizer_email=email, organizer_mailto=_mailto(email) if email else None,
    )
