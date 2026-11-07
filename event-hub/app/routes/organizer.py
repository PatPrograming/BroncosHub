"""Owner-only organizer dashboard, statistics, and event create/edit/cancel."""

from functools import wraps

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, case, select, update
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Event, Registration
from app.pagination import paginate
from app.security import csrf_token, validate_csrf
from app.services import cover_service
from app.services.event_service import CATEGORIES, campus_now, validate_event_form
from app.services.cover_service import (
    InvalidCover, commit_event_with_cover, discard_cover, normalize_cover,
)
from app.services.stats_service import owner_dashboard_statistics, event_statistics
from app.services.event_write_service import event_write


organizer_bp = Blueprint("organizer", __name__, url_prefix="/organizer")


@organizer_bp.context_processor
def organizer_context():
    return {"csrf_token": csrf_token}


def organizer_required(view):
    """Allow verified SCU accounts to host; individual routes enforce ownership."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.can_organize:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _owned_events():
    # Admin accounts also see only their own events here. Moderation is separate.
    return select(Event).where(Event.organizer_id == current_user.id).options(
        joinedload(Event.organizer), selectinload(Event.registrations)
    )


def _owned_event(event_id):
    event = db.session.scalar(_owned_events().where(Event.id == event_id))
    if event is None:
        abort(404)
    return event


def _page_context():
    return {
        "active_nav": "organizer",
        "event_management_available": True,
        "event_browsing_available": True,
    }


def _cover_input(errors):
    uploads = [item for item in request.files.getlist("cover_image") if item.filename]
    remove = "1" in request.form.getlist("remove_cover")
    try:
        if len(uploads) > 1:
            raise InvalidCover("Choose one cover image at a time.")
        if uploads and remove:
            raise InvalidCover("Choose a new cover or remove the old one, then save.")
        image_data = normalize_cover(uploads[0] if uploads else None)
    except InvalidCover as error:
        errors["cover_image"] = str(error)
        image_data = None
    if errors and image_data is not None:
        errors["cover_image"] = "Choose your cover again after fixing the details above."
    return image_data, remove


def _paid_event_error(form):
    """Check Stripe before entering a write transaction or publishing a price."""
    from app.services.price_service import parse_price_cents
    try:
        price = parse_price_cents(form.get("price", ""))
    except ValueError:
        return None  # The regular form validator explains malformed prices.
    if price == 0 or current_app.config.get("PAYMENT_PROVIDER") == "mock":
        return None
    if current_app.config.get("PAYMENT_PROVIDER") != "stripe":
        return "Online payments aren't enabled yet. Use 0 for a free event."
    from app.services.stripe_gateway import PaymentProviderError, payment_account_for
    try:
        account = payment_account_for(current_user.id)
    except PaymentProviderError:
        return "We couldn't verify your payment account. Please try again before publishing a paid event."
    if account is None or not account.ready:
        return "Finish payment setup in your organizer dashboard before publishing a paid event."
    return None


@organizer_bp.get("/")
@organizer_required
def dashboard():
    now = campus_now()
    upcoming = and_(Event.status == "published", Event.end_time >= now)
    events, pagination = paginate(
        _owned_events().order_by(
            case((upcoming, 0), else_=1),
            case((upcoming, Event.start_time)).asc(),
            Event.start_time.desc(), Event.id.desc(),
        )
    )
    return render_template(
        "organizer/dashboard.html",
        events=[event.event_view() for event in events],
        stats=owner_dashboard_statistics(current_user.id),
        pagination=pagination,
        **_page_context(),
    )


@organizer_bp.route("/events/new", methods=["GET", "POST"])
@organizer_required
def create_event():
    if request.method == "POST":
        validate_csrf()
        data, errors = validate_event_form(request.form)
        image_data, remove = _cover_input(errors)
        if not errors:
            payment_error = _paid_event_error(request.form)
            if payment_error:
                errors["price"] = payment_error
        if errors:
            return render_template(
                "organizer/event_form.html", categories=CATEGORIES,
                form_values=request.form, errors=errors, **_page_context(),
            ), 422
        event = Event(organizer_id=current_user.id, status="published", **data)
        try:
            commit_event_with_cover(event, image_data)
        except OSError:
            db.session.rollback()
            return render_template(
                "organizer/event_form.html", categories=CATEGORIES, form_values=request.form,
                errors={"cover_image": "We couldn't save your image. Please try again."},
                **_page_context(),
            ), 503
        flash("Event published.", "success")
        return redirect(url_for("events.detail", event_id=event.id))
    return render_template("organizer/event_form.html", categories=CATEGORIES, **_page_context())


@organizer_bp.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@organizer_required
def edit_event(event_id):
    event = _owned_event(event_id)
    if event.is_removed:
        if request.method == "POST":
            validate_csrf()
        return render_template(
            "errors/error.html", status=409, title="This event was removed",
            message="An administrator removed this event. It can no longer be edited.",
            return_url=url_for("organizer.dashboard"), return_label="Organizer dashboard",
        ), 409
    if request.method == "POST":
        validate_csrf()
        # Decode uploads before taking the write lock. Capacity, price and status
        # must be checked again against fresh rows after that potentially slow work.
        image_errors = {}
        image_data, remove = _cover_input(image_errors)
        payment_error = _paid_event_error(request.form)
        # Remote uploads can take seconds. Finish them before locking the event;
        # a rejected/stale edit discards its unused object in the finally block.
        prepared_cover = None
        cover_saved = False
        if image_data is not None and not image_errors and not payment_error:
            try:
                prepared_cover = cover_service.store_cover(image_data)
            except OSError:
                db.session.rollback()
                return render_template(
                    "organizer/event_form.html", event=event.event_view(), categories=CATEGORIES,
                    form_values=request.form,
                    errors={"cover_image": "We couldn't save your image. Please try again."},
                    **_page_context(),
                ), 503
        try:
            with event_write(event_id) as event:
                if event is None or event.organizer_id != current_user.id:
                    abort(404)
                if event.is_removed:
                    return render_template(
                        "errors/error.html", status=409, title="This event was removed",
                        message="An administrator removed this event. It can no longer be edited.",
                        return_url=url_for("organizer.dashboard"), return_label="Organizer dashboard",
                    ), 409
                if event.is_cancelled:
                    flash("Cancelled events can't be edited.", "warning")
                    return redirect(url_for("organizer.dashboard"))
                data, errors = validate_event_form(request.form, event=event)
                errors.update(image_errors)
                if payment_error:
                    errors["price"] = payment_error
                if errors:
                    if image_data is not None:
                        errors["cover_image"] = "Choose your cover again after fixing the details above."
                    return render_template(
                        "organizer/event_form.html", event=event.event_view(), categories=CATEGORIES,
                        form_values=request.form, errors=errors, **_page_context(),
                    ), 422
                from app.services.notification_service import event_notice_details, queue_event_notices
                previous_notice = event_notice_details(event)
                for field, value in data.items():
                    setattr(event, field, value)
                if event_notice_details(event) != previous_notice:
                    queue_event_notices(event, "changed")
                try:
                    commit_event_with_cover(event, remove=remove, stored_cover=prepared_cover)
                    cover_saved = True
                except OSError:
                    db.session.rollback()
                    return render_template(
                        "organizer/event_form.html", event=event.event_view(), categories=CATEGORIES,
                        form_values=request.form,
                        errors={"cover_image": "We couldn't save your image. Please try again."},
                        **_page_context(),
                    ), 503
        finally:
            if not cover_saved:
                discard_cover(prepared_cover)
        flash("Changes saved.", "success")
        return redirect(url_for("events.detail", event_id=event.id))
    return render_template(
        "organizer/event_form.html", event=event.event_view(), categories=CATEGORIES,
        **_page_context(),
    )


@organizer_bp.post("/events/<int:event_id>/cancel")
@organizer_required
def cancel_event(event_id):
    validate_csrf()
    _owned_event(event_id)
    with event_write(event_id) as event:
        if event is None or event.organizer_id != current_user.id:
            abort(404)
        # A stale organizer request must never turn removal into cancellation.
        changed = db.session.execute(update(Event).where(
            Event.id == event.id, Event.status == "published"
        ).values(status="cancelled"))
        if changed.rowcount:
            from app.services.notification_service import queue_event_notices
            queue_event_notices(event, "cancelled")
            from app.services.stripe_checkout_service import queue_event_cancellations
            queue_event_cancellations(event_id)
            db.session.execute(update(Registration).where(
                Registration.event_id == event.id,
                Registration.status.in_(("confirmed", "pending")),
            ).values(status="cancelled"))
            flash("Event cancelled. Registration is closed.", "info")
        db.session.commit()
    return redirect(url_for("organizer.dashboard"))


@organizer_bp.get("/events/<int:event_id>/stats")
@organizer_required
def statistics(event_id):
    event = _owned_event(event_id)
    return render_template(
        "organizer/stats.html",
        event=event.event_view(),
        stats=event_statistics(event),
        **_page_context(),
    )


@organizer_bp.errorhandler(403)
def organizer_access_required(error):
    return render_template(
        "errors/error.html", status=403, title="SCU account required",
        message="Sign in with a verified SCU Google account to create and manage events.",
        return_url=url_for("auth.profile"), return_label="Go to profile",
    ), 403


@organizer_bp.errorhandler(404)
def event_not_found(error):
    return render_template(
        "errors/error.html", status=404, title="Event not found",
        message="This event isn't available in your organizer dashboard.",
        return_url=url_for("organizer.dashboard"), return_label="Organizer dashboard",
    ), 404


@organizer_bp.errorhandler(413)
def upload_too_large(error):
    return render_template(
        "errors/error.html", status=413, title="Upload too large",
        message="Choose a JPG, PNG or WebP cover no larger than 5 MB, then try again.",
        return_url=url_for("organizer.dashboard"), return_label="Organizer dashboard",
    ), 413
