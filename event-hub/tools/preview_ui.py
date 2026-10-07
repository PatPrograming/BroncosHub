"""Run the UI without touching the team's unfinished application/backend.

    python tools/preview_ui.py --port 5074

Only sample data and reversible browser-session state are used. This module is
not authentication, a payment implementation, or the production Flask app.
"""

import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from io import BytesIO
from pathlib import Path
import secrets
from threading import RLock
from urllib.parse import urlencode, urlsplit
import warnings

from flask import Flask, abort, flash, redirect, render_template, request, send_file, session
from PIL import Image, ImageOps, UnidentifiedImageError

try:
    from .ui_fixtures import CATEGORIES, EVENTS, PREVIEW_TODAY, REGISTRATIONS, REPORTS, YEAR_LEVELS, decorate_event, money
except ImportError:
    from ui_fixtures import CATEGORIES, EVENTS, PREVIEW_TODAY, REGISTRATIONS, REPORTS, YEAR_LEVELS, decorate_event, money

ROOT = Path(__file__).resolve().parents[1]
CATEGORY_ART = {"Arts & Culture": "makers.svg", "Social": "club-fair.svg",
                "Sports": "basketball.svg", "Tech": "tech.svg",
                "Service": "volunteer.svg", "Wellness": "coffee.svg"}
MAX_COVER_BYTES = 5 * 1024 * 1024
MAX_COVER_PIXELS = 20_000_000


class CoverError(ValueError):
    """A readable validation error for an optional demo cover."""


def decode_cover(upload):
    """Decode a bounded raster and save fresh pixels, never original metadata."""
    payload = upload.stream.read(MAX_COVER_BYTES + 1)
    if len(payload) > MAX_COVER_BYTES:
        raise CoverError("Choose a cover image that is 5 MB or smaller.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload), formats=["JPEG", "PNG", "WEBP"]) as source:
                if source.width * source.height > MAX_COVER_PIXELS or max(source.size) > 10000:
                    raise CoverError("Choose an image under 20 megapixels and 10,000 pixels per side.")
                if getattr(source, "n_frames", 1) != 1:
                    raise CoverError("Choose a still JPEG, PNG, or WebP image, without animation.")
                source.verify()
            with Image.open(BytesIO(payload), formats=["JPEG", "PNG", "WEBP"]) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source)
                oriented.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
                clean = Image.new("RGB", oriented.size, "white")
                if "A" in oriented.getbands() or "transparency" in oriented.info:
                    rgba = oriented.convert("RGBA")
                    clean.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    clean.paste(oriented.convert("RGB"))
                output = BytesIO()
                clean.save(output, format="JPEG", quality=88, optimize=True)
    except CoverError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise CoverError("Choose a valid JPEG, PNG, or WebP image that can be opened.") from error
    # The original name is display-only, never a filesystem path or response name.
    filename = (upload.filename or "Event cover").replace("\\", "/").split("/")[-1]
    filename = "".join(char for char in filename if char.isprintable())[:120] or "Event cover"
    return output.getvalue(), filename


def create_preview_app(testing=False):
    app = Flask("broncoshub_ui_preview", template_folder=str(ROOT / "app/templates"),
                static_folder=str(ROOT / "app/static"), static_url_path="/static")
    app.config.update(SECRET_KEY=secrets.token_hex(32), TESTING=testing,
                      TEMPLATES_AUTO_RELOAD=True, SEND_FILE_MAX_AGE_DEFAULT=0,
                      SESSION_COOKIE_NAME="broncoshub_preview", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=MAX_COVER_BYTES + 256 * 1024,
                      MAX_FORM_MEMORY_SIZE=256 * 1024, MAX_FORM_PARTS=24)
    # Preview-only storage: bytes never enter the signed session cookie or Git.
    # Covers last until Reset or process exit. Bounds also limit abandoned drafts.
    covers = {}
    cover_lock = RLock()

    def owned_cover(token):
        with cover_lock:
            cover = covers.get(token)
            return cover if cover and cover["owner"] == session.get("cover_owner") else None

    def store_cover(upload):
        data, name = decode_cover(upload)
        owner = session.setdefault("cover_owner", secrets.token_urlsafe(24))
        with cover_lock:
            owned = [cover for cover in covers.values() if cover["owner"] == owner]
            if (len(owned) >= 32 or len(covers) >= 256
                    or sum(len(cover["data"]) for cover in owned) + len(data) > 20 * 1024 * 1024
                    or sum(len(cover["data"]) for cover in covers.values()) + len(data) > 64 * 1024 * 1024):
                raise CoverError("The local preview's cover storage is full. Reset the preview to clear its sample uploads.")
            token = secrets.token_urlsafe(24)
            covers[token] = {"owner": owner, "data": data, "name": name}
        return token

    def cover_context(token):
        cover = owned_cover(token)
        return {"cover_token": token if cover else "",
                "cover_url": f"/preview/covers/{token}" if cover else "",
                "cover_name": cover["name"] if cover else ""}

    @app.before_request
    def keep_other_forms_small():
        if request.endpoint not in ("create_event", "edit_event") and (request.content_length or 0) > 32 * 1024:
            abort(413)

    @app.get("/preview/covers/<token>")
    def preview_cover(token):
        cover = owned_cover(token)
        if cover is None:
            abort(404)
        response = send_file(BytesIO(cover["data"]), mimetype="image/jpeg", download_name="event-cover.jpg",
                             conditional=False, etag=False, max_age=0)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def state():
        if "preview" not in session:
            session["preview"] = {"role": "student", "registrations": deepcopy(REGISTRATIONS),
                                  "events": {}, "removed_reports": [], "profile": {}}
        return session["preview"]

    def save():
        session.modified = True

    def user():
        role = state()["role"]
        if role == "guest":
            return None
        profile = state()["profile"]
        name = profile.get("name", "Alex Chen")
        return {"id": "preview-user", "name": name, "first_name": name.split()[0],
                "email": "alex.preview@scu.edu", "role": role,
                "year_level": profile.get("year_level", "third_year")}

    def require_role(*roles):
        def decorator(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if state()["role"] not in roles:
                    abort(403)
                return fn(*args, **kwargs)
            return wrapped
        return decorator

    def safe_next(value, fallback="/"):
        if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
            return fallback
        parsed = urlsplit(value)
        return value if not parsed.scheme and not parsed.netloc else fallback

    def all_events():
        events = {event["id"]: deepcopy(event) for event in EVENTS}
        for event_id, event in state()["events"].items():
            events[int(event_id)] = deepcopy(event)
        for event in events.values():
            baseline = sum(r["event_id"] == event["id"] and r["status"] == "confirmed" for r in REGISTRATIONS)
            current = sum(r["event_id"] == event["id"] and r["status"] == "confirmed" for r in state()["registrations"])
            event["registrations_count"] = max(0, event["registrations_count"] + current - baseline)
            event.update(decorate_event(event))
            event.update(cover_context(event.get("cover_token", "")))
            event["registered"] = state()["role"] != "guest" and any(r["event_id"] == event["id"] and r["status"] != "cancelled" for r in state()["registrations"])
            event["price_locked"] = event["registrations_count"] > 0 or any(
                r["event_id"] == event["id"] and r["status"] != "cancelled" for r in state()["registrations"])
        return sorted(events.values(), key=lambda event: event["start_value"])

    def find_event(event_id):
        event = next((event for event in all_events() if event["id"] == event_id), None)
        if event is None:
            abort(404)
        return event

    def owner_event(event_id):
        event = find_event(event_id)
        if event["owner_id"] != "preview-user":
            abort(403)
        return event

    def registration_view(registration):
        result = deepcopy(registration)
        result["event"] = find_event(registration["event_id"])
        valid_payment = result["event"]["price_cents"] == 0 or result["payment_status"] == "succeeded"
        result["ticket_valid"] = result["status"] == "confirmed" and valid_payment and result["event"]["status"] == "published"
        result["ticket_code"] = f"DEMO-BH-{result['id']:04d}" if result["ticket_valid"] else None
        return result

    def find_registration(registration_id):
        registration = next((r for r in state()["registrations"] if r["id"] == registration_id), None)
        if registration is None:
            abort(404)
        return registration

    @app.context_processor
    def shared_context():
        path = request.path
        active_nav = ("home" if path == "/" else "admin" if path.startswith("/admin") else "organizer" if path.startswith("/organizer")
                      else "my-events" if path.startswith(("/my-registrations", "/registrations"))
                      else "explore" if path.startswith("/events") else "")
        return {"preview_mode": True, "user": user(), "active_nav": active_nav,
                "categories": CATEGORIES, "year_levels": YEAR_LEVELS,
                "preview_today": PREVIEW_TODAY, "registration_by_event": {
                    r["event_id"]: registration_view(r) for r in state()["registrations"]
                    if state()["role"] != "guest" and r["status"] != "cancelled"}}

    @app.get("/")
    def index():
        events = [event for event in all_events() if event["status"] == "published"]
        featured = next(event for event in events if event["id"] == 1) if any(e["id"] == 1 for e in events) else (events[0] if events else None)
        return render_template("index.html", events=events, featured_event=featured)

    @app.get("/events")
    def events_list():
        filters = {key: request.args.get(key, "").strip() for key in ("q", "category", "date", "location")}
        events = [event for event in all_events() if event["status"] == "published"
                  and date.fromisoformat(event["start_value"][:10]) >= PREVIEW_TODAY]
        if filters["q"]:
            query = filters["q"].casefold()
            events = [e for e in events if query in " ".join(str(e[key]) for key in ("title", "description", "organizer", "location")).casefold()]
        for key in ("category", "location"):
            if filters[key]:
                events = [e for e in events if filters[key].casefold() in e[key].casefold()]
        date_filter = filters["date"].replace("_", "-")
        date_filter = {"this-week": "week", "this-month": "month"}.get(date_filter, date_filter)
        exact_date = None
        if date_filter not in ("", "today", "week", "month"):
            try:
                exact_date = date.fromisoformat(date_filter)
                if exact_date.isoformat() != date_filter:
                    raise ValueError
            except ValueError:
                date_filter = ""
                exact_date = None
        filters["date"] = date_filter
        non_date_filters = {key: value for key, value in filters.items() if key != "date" and value}
        selected_date = PREVIEW_TODAY if date_filter == "today" else exact_date
        # Browsing the calendar changes its visible week, not the chosen filter.
        calendar_start = selected_date or PREVIEW_TODAY
        if request.args.get("calendar"):
            try:
                calendar_start = date.fromisoformat(request.args["calendar"])
            except ValueError:
                pass
        max_offset = ((date.max - PREVIEW_TODAY).days - 6) // 7 * 7
        offset = max(0, min(max_offset, (calendar_start - PREVIEW_TODAY).days // 7 * 7))
        calendar_start = PREVIEW_TODAY + timedelta(days=offset)
        calendar_end = calendar_start + timedelta(days=6)
        start_label = f"{calendar_start:%b} {calendar_start.day}"
        if calendar_start.year != calendar_end.year:
            start_label += f", {calendar_start.year}"
        calendar_value = calendar_start.isoformat()
        calendar_filters = {"calendar": calendar_value} if offset else {}
        clear_params = {**non_date_filters, **calendar_filters}
        clear_date_url = "/events" + ("?" + urlencode(clear_params) if clear_params else "")
        navigation_filters = {key: value for key, value in filters.items() if value}
        def week_url(days):
            return "/events?" + urlencode({**navigation_filters, "calendar": (calendar_start + timedelta(days=days)).isoformat()})
        campus_days = []
        for day_offset in range(7):
            day = calendar_start + timedelta(days=day_offset)
            campus_days.append({
                "iso": day.isoformat(), "weekday": day.strftime("%a"), "day": day.day,
                "month": day.strftime("%b"), "label": f"{day:%A, %B} {day.day}",
                "count": sum(e["start_value"][:10] == day.isoformat() for e in events),
                "url": "/events?" + urlencode({**non_date_filters, **calendar_filters, "date": day.isoformat()}),
                "selected": day == selected_date,
            })
        date_label = "All upcoming"
        if date_filter == "today":
            events = [e for e in events if datetime.fromisoformat(e["start_value"]).date() == PREVIEW_TODAY]
            date_label = f"Today, {PREVIEW_TODAY:%B} {PREVIEW_TODAY.day}"
        elif date_filter == "week":
            end = PREVIEW_TODAY + timedelta(days=6 - PREVIEW_TODAY.weekday())
            events = [e for e in events if PREVIEW_TODAY <= datetime.fromisoformat(e["start_value"]).date() <= end]
            date_label = "This week"
        elif date_filter == "month":
            events = [e for e in events if e["start_value"].startswith(PREVIEW_TODAY.strftime("%Y-%m"))]
            date_label = "This month"
        elif exact_date:
            events = [e for e in events if e["start_value"][:10] == exact_date.isoformat()]
            date_label = f"{exact_date:%A, %B} {exact_date.day}"
        announcement = f"{len(events)} {'event' if len(events) == 1 else 'events'} · {date_label}."
        return render_template("events/list.html", events=events, filters=filters,
                               locations=sorted({e["location"] for e in all_events()}),
                               campus_days=campus_days, clear_date_url=clear_date_url,
                               calendar_value=calendar_value,
                               calendar_previous=week_url(-7) if offset else None,
                               calendar_next=week_url(7) if offset < max_offset else None,
                               calendar_range=f"{start_label} – {calendar_end:%b} {calendar_end.day}, {calendar_end.year}",
                               campus_total=sum(day["count"] for day in campus_days),
                               date_label=date_label, exact_date_label=date_label if exact_date else None,
                               result_announcement=announcement)

    @app.get("/events/<int:event_id>")
    def event_detail(event_id):
        event = find_event(event_id)
        registration = next((registration_view(r) for r in state()["registrations"]
                             if state()["role"] != "guest" and r["event_id"] == event_id and r["status"] != "cancelled"), None)
        related = [e for e in all_events() if e["id"] != event_id and e["status"] == "published"][:3]
        return render_template("events/detail.html", event=event, registration=registration, related_events=related)

    @app.route("/events/<int:event_id>/register", methods=["GET", "POST"])
    @require_role("student", "organizer", "admin")
    def register(event_id):
        event = find_event(event_id)
        if event["status"] != "published":
            abort(409)
        existing = next((r for r in state()["registrations"] if r["event_id"] == event_id and r["status"] != "cancelled"), None)
        if existing:
            return redirect(f"/my-registrations/{existing['id']}")
        if event["spots_left"] <= 0:
            abort(409)
        if request.method == "POST":
            paid = event["price_cents"] > 0
            registration = {"id": max([r["id"] for r in state()["registrations"]] + [100]) + 1,
                            "event_id": event_id, "status": "pending" if paid else "confirmed",
                            "payment_status": "pending" if paid else "not_required"}
            state()["registrations"].append(registration)
            save()
            flash("Continue to the sample checkout." if paid else "You’re registered. See you there!", "success")
            return redirect(f"/registrations/{registration['id']}/checkout" if paid else f"/my-registrations/{registration['id']}")
        return render_template("registrations/confirm.html", event=event, errors={}, form_values={})

    @app.route("/registrations/<int:registration_id>/checkout", methods=["GET", "POST"])
    @require_role("student", "organizer", "admin")
    def checkout(registration_id):
        registration = find_registration(registration_id)
        event = find_event(registration["event_id"])
        if registration["status"] == "cancelled" or event["status"] != "published":
            abort(409)
        if event["price_cents"] == 0 or registration["payment_status"] == "succeeded":
            return redirect(f"/my-registrations/{registration_id}")
        errors = {}
        if request.method == "POST":
            result = request.form.get("demo_result", "")
            if result not in ("succeeded", "declined", "pending"):
                errors["demo_result"] = "Choose a sample payment result."
            else:
                registration["payment_status"] = "failed" if result == "declined" else result
                registration["status"] = "confirmed" if result == "succeeded" else "pending"
                save()
                flash({"succeeded": "Sample payment confirmed. Your demo ticket is ready.",
                       "declined": "The sample payment was declined. No ticket was issued.",
                       "pending": "The sample payment is pending. No ticket has been issued."}[result],
                      "success" if result == "succeeded" else "info")
                return redirect(f"/my-registrations/{registration_id}")
        return render_template("registrations/checkout.html", registration=registration_view(registration), event=event,
                               errors=errors, form_values=request.form), 422 if errors else 200

    @app.get("/my-registrations")
    @require_role("student", "organizer", "admin")
    def my_registrations():
        return render_template("registrations/my_registrations.html",
                               registrations=[registration_view(r) for r in state()["registrations"]])

    @app.get("/my-registrations/<int:registration_id>")
    @require_role("student", "organizer", "admin")
    def registration_detail(registration_id):
        registration = registration_view(find_registration(registration_id))
        return render_template("registrations/detail.html", registration=registration, event=registration["event"])

    @app.post("/my-registrations/<int:registration_id>/cancel")
    @require_role("student", "organizer", "admin")
    def registration_cancel(registration_id):
        registration = find_registration(registration_id)
        if find_event(registration["event_id"])["price_cents"] > 0:
            abort(409)
        registration["status"] = "cancelled"
        save()
        flash("Your free-event registration has been cancelled.", "success")
        return redirect(f"/my-registrations/{registration_id}")

    @app.get("/organizer/")
    @require_role("organizer")
    def organizer_dashboard():
        events = [e for e in all_events() if e["owner_id"] == "preview-user"]
        stats = stats_for(events)
        return render_template("organizer/dashboard.html", events=events, stats=stats)

    def stats_for(events):
        active_events = [e for e in events if e["status"] == "published"]
        count = sum(e["registrations_count"] for e in active_events)
        # Cancelling admission is not a refund: keep sample confirmed revenue.
        revenue = sum(e["registrations_count"] * e["price_cents"] for e in events)
        years = []
        remaining = count
        for label, fraction in [("First year", .30), ("Second year", .25), ("Third year", .20), ("Fourth year", .15), ("Graduate", .05), ("Unknown", .05)]:
            amount = remaining if label == "Unknown" else int(count * fraction)
            remaining -= amount
            years.append({"label": label, "count": amount, "percent": round(amount / count * 100) if count else 0})
        return {"registrations": count, "revenue_label": f"${revenue / 100:,.2f}",
                "events_count": len(active_events), "year_breakdown": years}

    def event_form(event_id=None):
        event = owner_event(event_id) if event_id is not None else None
        if event and event["status"] != "published":
            abort(409)
        form_values = dict(request.form) if request.method == "POST" else ({
            "title": event["title"], "description": event["description"], "category": event["category"],
            "location": event["location"], "start_time": event["start_value"], "end_time": event["end_value"],
            "price": f"{event['price_cents'] / 100:.2f}", "capacity": str(event["capacity"]),
        } if event else {})
        # Keep the live poster, validation response and published preview consistent.
        draft_category = form_values.get("category", "")
        draft_art = (event["art"] if event and draft_category == event["category"]
                     else CATEGORY_ART.get(draft_category, "campus.svg"))
        errors = {}
        cover_token = event.get("cover_token", "") if event else ""
        if request.method == "POST":
            posted_token = request.form.get("cover_token", "").strip()
            if posted_token:
                if owned_cover(posted_token):
                    cover_token = posted_token
                else:
                    errors["cover_image"] = "That cover is no longer available in this preview. Choose the image again."
            upload = request.files.get("cover_image")
            if upload and upload.filename:
                try:
                    cover_token = store_cover(upload)
                    errors.pop("cover_image", None)
                except CoverError as error:
                    errors["cover_image"] = str(error)
                # A rejected replacement must not silently remove a valid cover.
                form_values["remove_cover"] = ""
            elif request.form.get("remove_cover") == "1":
                cover_token = ""
                errors.pop("cover_image", None)
            for field, label, limit in [("title", "an event title", 120), ("description", "an event description", 1200), ("location", "a location", 120)]:
                value = form_values.get(field, "").strip()
                form_values[field] = value
                if not value:
                    errors[field] = f"Enter {label}."
                elif len(value) > limit:
                    errors[field] = f"Keep this to {limit} characters or fewer."
            if form_values.get("category") not in CATEGORIES:
                errors["category"] = "Choose an event category."
            times = {}
            for field in ("start_time", "end_time"):
                try:
                    parsed = datetime.fromisoformat(form_values.get(field, ""))
                    if parsed.tzinfo is not None or "T" not in form_values.get(field, ""):
                        raise ValueError
                    times[field] = parsed
                except ValueError:
                    errors[field] = "Enter a valid date and time."
            if "start_time" in times and times["start_time"].date() < PREVIEW_TODAY:
                errors["start_time"] = f"Choose {PREVIEW_TODAY:%B} {PREVIEW_TODAY.day}, {PREVIEW_TODAY.year} or a later date for this preview."
            if len(times) == 2 and times["end_time"] <= times["start_time"]:
                errors["end_time"] = "Choose an end time after the start time."
            try:
                price = Decimal(form_values.get("price", "0") or "0")
                if not price.is_finite() or price < 0 or price > 9999 or price != price.quantize(Decimal(".01")):
                    raise InvalidOperation
                price_cents = int(price * 100)
            except (InvalidOperation, ValueError):
                errors["price"] = "Enter a price from 0 to 9999 with no more than two decimal places."
            if "price" not in errors and event and event["price_locked"] and price_cents != event["price_cents"]:
                errors["price"] = "The admission price is fixed after the first registration in this preview."
            try:
                capacity = int(form_values.get("capacity", ""))
                if capacity < 1 or capacity > 10000 or (event and capacity < event["registrations_count"]):
                    raise ValueError
            except ValueError:
                errors["capacity"] = "Enter a capacity from 1 to 10000, at least the current registration count."
            if not errors:
                new_id = event_id if event else max(e["id"] for e in all_events()) + 1
                # Store fixture baseline count; all_events adds session registration changes.
                baseline = next((e["registrations_count"] for e in EVENTS if e["id"] == new_id), 0)
                saved = {"id": new_id, "title": form_values["title"], "description": form_values["description"],
                         "location": form_values["location"], "category": form_values["category"],
                         "start_value": times["start_time"].isoformat(timespec="minutes"),
                         "end_value": times["end_time"].isoformat(timespec="minutes"),
                         "price_cents": price_cents, "capacity": capacity, "registrations_count": baseline,
                         "organizer": event["organizer"] if event else "BroncosHub Preview Host",
                         "owner_id": "preview-user", "status": "published", "art": draft_art}
                if cover_token:
                    saved["cover_token"] = cover_token
                state()["events"][str(new_id)] = saved
                save()
                flash("Sample event updated." if event else "Sample event published. Explore it now!", "success")
                return redirect(f"/events/{new_id}")
        return render_template("organizer/event_form.html", event=event, errors=errors, form_values=form_values,
                               mode="edit" if event else "create", draft_art=draft_art,
                               **cover_context(cover_token)), 422 if errors else 200

    app.add_url_rule("/organizer/events/new", "create_event", require_role("organizer")(event_form), methods=["GET", "POST"])
    app.add_url_rule("/organizer/events/<int:event_id>/edit", "edit_event", require_role("organizer")(event_form), methods=["GET", "POST"])

    @app.post("/organizer/events/<int:event_id>/cancel")
    @require_role("organizer")
    def event_cancel(event_id):
        event = owner_event(event_id)
        existing = state()["events"].get(str(event_id), next((e for e in EVENTS if e["id"] == event_id), event))
        state()["events"][str(event_id)] = {**deepcopy(existing), "status": "cancelled"}
        save()
        flash("Event cancelled. New registrations are closed and existing demo tickets are invalid.", "success")
        return redirect("/organizer/")

    @app.get("/organizer/events/<int:event_id>/stats")
    @require_role("organizer")
    def event_stats(event_id):
        event = owner_event(event_id)
        return render_template("organizer/stats.html", event=event, stats=stats_for([event]))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            state()["role"] = "student"
            save()
            flash("You’re exploring a sample student account. No real sign-in took place.", "info")
            return redirect(safe_next(request.form.get("next") or request.args.get("next")))
        return render_template("auth/login.html", errors={}, form_values={}, next_url=safe_next(request.args.get("next")))

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        errors = {}
        form_values = {}
        if request.method == "POST":
            form_values, errors = validated_profile_fields()
            if not errors:
                state()["role"] = "student"
                state()["profile"] = form_values
                save()
                flash("Sample profile saved. No account was created and no credentials were saved.", "info")
                return redirect("/profile")
        return render_template("auth/signup.html", errors=errors, form_values=form_values), 422 if errors else 200

    def validated_profile_fields():
        # Whitelist only the two fictional profile fields; never read credentials.
        values = {"name": request.form.get("name", "").strip(),
                  "year_level": request.form.get("year_level", "unknown")}
        errors = {}
        if not values["name"] or len(values["name"]) > 80:
            errors["name"] = "Enter a sample display name of 80 characters or fewer."
        if values["year_level"] not in YEAR_LEVELS:
            errors["year_level"] = "Choose a year level, or prefer not to say."
        return values, errors

    def profile_overview():
        upcoming = []
        for registration in state()["registrations"]:
            if registration["status"] not in ("confirmed", "pending"):
                continue
            view = registration_view(registration)
            event = view["event"]
            # The preview has a fixed date, not a simulated clock. Include today,
            # consistently with Explore, and exclude cancelled or past events.
            if event["status"] == "published" and date.fromisoformat(event["start_value"][:10]) >= PREVIEW_TODAY:
                upcoming.append(view)
        upcoming.sort(key=lambda registration: (registration["event"]["start_value"], registration["id"]))
        confirmed = [registration for registration in upcoming if registration["ticket_valid"]]
        return {"upcoming_count": len(confirmed),
                "pending_count": sum(registration["status"] == "pending" for registration in upcoming),
                "next_registration": confirmed[0] if confirmed else None}

    @app.route("/profile", methods=["GET", "POST"])
    @require_role("student", "organizer", "admin")
    def profile():
        errors = {}
        form_values = user()
        if request.method == "POST":
            form_values, errors = validated_profile_fields()
            if not errors:
                state()["profile"] = form_values
                save()
                flash("Preview profile updated for this browser session.", "success")
                return redirect("/profile")
        return render_template("auth/profile.html", errors=errors, form_values=form_values,
                               profile_overview=profile_overview()), 422 if errors else 200

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        state()["role"] = "guest"
        save()
        return redirect("/")

    @app.get("/admin/reports")
    @require_role("admin")
    def admin_reports():
        reports = [{**report, "event": find_event(report["event_id"])} for report in REPORTS if report["id"] not in state()["removed_reports"]]
        return render_template("admin/reports.html", reports=reports)

    @app.post("/admin/reports/<int:report_id>/remove")
    @require_role("admin")
    def report_remove(report_id):
        report = next((r for r in REPORTS if r["id"] == report_id), None)
        if report is None:
            abort(404)
        event = find_event(report["event_id"])
        existing = state()["events"].get(str(event["id"]), next(e for e in EVENTS if e["id"] == event["id"]))
        state()["events"][str(event["id"])] = {**deepcopy(existing), "status": "cancelled"}
        if report_id not in state()["removed_reports"]:
            state()["removed_reports"].append(report_id)
        save()
        flash("Reported sample event removed from discovery.", "success")
        return redirect("/admin/reports")

    @app.route("/preview/role/<role>", methods=["GET", "POST"])
    def preview_role(role):
        if role not in ("student", "organizer", "admin", "guest"):
            abort(404)
        state()["role"] = role
        save()
        fallback = {"organizer": "/organizer/", "admin": "/admin/reports"}.get(role, "/")
        return redirect(safe_next(request.values.get("next"), fallback))

    @app.route("/preview/reset", methods=["GET", "POST"])
    def reset_preview():
        with cover_lock:
            owner = session.get("cover_owner")
            for token in [token for token, cover in covers.items() if cover["owner"] == owner]:
                del covers[token]
        session.clear()
        flash("The sample experience has been reset.", "info")
        return redirect("/")

    for status, title, message in [
        (403, "This page needs a different role", "Choose the student, organizer, or admin preview from the role menu to explore this area."),
        (404, "Looks like this page moved", "The link may be out of date. Head back to Explore and find something new."),
        (409, "This action is unavailable", "The event may be cancelled or full, or this registration needs a different action. Paid cancellations and refunds are not available in this preview."),
        (413, "That form is a little too large", "Choose a cover image of 5 MB or smaller, keep the other fields short, and submit again. Your saved event has not changed."),
    ]:
        def handler(error, status=status, title=title, message=message):
            return render_template("errors/error.html", status=status, title=title, message=message), status
        app.register_error_handler(status, handler)

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local, fictional BroncosHub UI preview")
    parser.add_argument("--port", type=int, default=5074)
    args = parser.parse_args()
    create_preview_app().run(host="127.0.0.1", port=args.port, debug=False)
