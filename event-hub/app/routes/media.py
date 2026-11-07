"""Event-bound media access keeps removed posters out of public circulation."""

from io import BytesIO

from flask import Blueprint, abort, current_app, send_file
from flask_login import current_user

from app.extensions import db
from app.models import Event
from app.services.cover_service import read_cover


media_bp = Blueprint("media", __name__, url_prefix="/media")


@media_bp.after_request
def private_uncached_cover(response):
    # Even an image first viewed while public must be checked again after removal.
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@media_bp.get("/events/<int:event_id>/cover")
def event_cover(event_id):
    event = db.session.get(Event, event_id)
    if event is None or not event.cover_image_path:
        abort(404)
    if event.status not in ("published", "cancelled"):
        may_review = current_user.is_authenticated and (
            current_user.role == "admin" or current_user.id == event.organizer_id
        )
        if not may_review:
            abort(404)
    try:
        image = read_cover(event.cover_image_path)
    except OSError:
        current_app.logger.warning("Could not read an event cover from storage.")
        abort(503)
    if image is None:
        abort(404)
    return send_file(BytesIO(image), mimetype="image/webp", conditional=False, etag=False, max_age=0)
