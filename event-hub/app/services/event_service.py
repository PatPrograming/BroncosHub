# app/services/event_service.py
# Event form checks for create and edit. UC-006, UC-007.
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.event import CATEGORY_ART
from app.services.price_service import parse_price_cents

CATEGORIES = list(CATEGORY_ART)
PACIFIC = ZoneInfo("America/Los_Angeles")
FORM_TIME = "%Y-%m-%dT%H:%M"  # what <input type="datetime-local"> sends


def campus_now():
    # Event times are typed in Pacific Time and stored without a timezone,
    # so "now" has to be Pacific Time too or comparisons are off by 7-8 hours.
    return datetime.now(PACIFIC).replace(tzinfo=None)


def _parse_time(text):
    try:
        return datetime.strptime(text, FORM_TIME)
    except ValueError:
        return None


def validate_event_form(form, event=None):
    """Return (data, errors). data is only usable when errors is empty.

    event is the existing Event when editing, or None when creating.
    """
    errors = {}
    title = form.get("title", "").strip()
    description = form.get("description", "").strip()
    location = form.get("location", "").strip()
    category = form.get("category", "")

    if not title:
        errors["title"] = "Give your event a name."
    elif len(title) > 100:
        errors["title"] = "Keep the event name to 100 characters."
    if not description:
        errors["description"] = "Tell people what the event is about."
    elif len(description) > 1200:
        errors["description"] = "Keep the description to 1,200 characters."
    if category not in CATEGORIES:
        errors["category"] = "Choose a category."
    if not location:
        errors["location"] = "Add a location."
    elif len(location) > 100:
        errors["location"] = "Keep the location to 100 characters."

    start = _parse_time(form.get("start_time", ""))
    end = _parse_time(form.get("end_time", ""))
    if start is None:
        errors["start_time"] = "Choose a start date and time."
    elif start <= campus_now() and (event is None or start != event.start_time):
        # Editing may keep an existing time, but nobody can pick a new past time.
        errors["start_time"] = "The start time must be in the future."
    if end is None:
        errors["end_time"] = "Choose an end date and time."
    elif start is not None and end <= start:
        errors["end_time"] = "The event must end after it starts."

    price_cents = None
    try:
        price_cents = parse_price_cents(form.get("price", ""))
        if 0 < price_cents < 50:
            errors["price"] = "Paid tickets must cost at least $0.50. Use 0 for a free event."
    except ValueError as error:
        errors["price"] = f"{error} Use 0 for a free event."
    if event is not None and price_cents is not None and price_cents != event.price_cents:
        if event.registrations:
            errors["price"] = "The price can't change after people have registered."

    capacity = None
    try:
        capacity = int(form.get("capacity", ""))
        if not 1 <= capacity <= 10000:
            raise ValueError
    except ValueError:
        errors["capacity"] = "Enter a capacity from 1 to 10,000."
        capacity = None
    if event is not None and capacity is not None:
        from app.services.event_write_service import occupied_seats
        taken = occupied_seats(event.id)
        if capacity < taken:
            errors["capacity"] = f"{taken} seats are confirmed or held at checkout. Capacity can't be lower."

    data = {
        "title": title,
        "description": description,
        "location": location,
        "category": category,
        "start_time": start,
        "end_time": end,
        "price_cents": price_cents,
        "capacity": capacity,
    }
    return data, errors
