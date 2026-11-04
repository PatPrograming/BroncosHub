"""Event-bound media access keeps removed posters out of public circulation."""

from flask import Blueprint, abort, send_file
from flask_login import current_user

from app.extensions import db
from app.models import Event
from app.services.cover_service import cover_path


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
    path = cover_path(event.cover_image_path)
    if path is None or not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/webp", conditional=False, etag=False, max_age=0)
