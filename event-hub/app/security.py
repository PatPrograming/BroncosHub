"""Session-bound CSRF protection for event reporting and moderation forms."""

import hmac
import secrets

from flask import abort, request, session
from flask_login import current_user


def csrf_token():
    stored = session.get("moderation_csrf")
    if not isinstance(stored, dict) or stored.get("user") != current_user.get_id():
        stored = {"user": current_user.get_id(), "token": secrets.token_urlsafe(32)}
        session["moderation_csrf"] = stored
    return stored["token"]


def validate_csrf():
    stored = session.get("moderation_csrf")
    submitted = request.form.getlist("csrf_token")
    if (not isinstance(stored, dict) or stored.get("user") != current_user.get_id()
            or len(submitted) != 1 or len(submitted[0]) > 200
            or not hmac.compare_digest(submitted[0].encode(), stored["token"].encode())):
        abort(400, description="This form has expired. Reload the page and try again.")
