"""Calendar navigation and date ranges for real campus events."""

from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode


def iso_date(value):
    """Accept only complete YYYY-MM-DD dates, not compact or week-date forms."""
    try:
        parsed = date.fromisoformat(value)
        return parsed if parsed.isoformat() == value else None
    except (TypeError, ValueError):
        return None


def normalize_date_filter(value):
    return value if value in ("", "today", "week", "month") or iso_date(value) else ""


def date_range(choice, today):
    """Half-open campus wall-time interval; None upper bound means date.max."""
    selected = iso_date(choice)
    if selected:
        first = selected
        days = 1
    elif choice == "today":
        first, days = today, 1
    elif choice == "week":
        first, days = today, 7 - today.weekday()
    elif choice == "month":
        first = today
        if today.month == 12:
            upper = date(today.year + 1, 1, 1) if today.year < date.max.year else None
        else:
            upper = date(today.year, today.month + 1, 1)
        return datetime.combine(first, time.min), datetime.combine(upper, time.min) if upper else None
    else:
        return None
    upper = first + timedelta(days=days) if (date.max - first).days >= days else None
    return datetime.combine(first, time.min), datetime.combine(upper, time.min) if upper else None


def calendar_context(filters, calendar_value, today):
    """A seven-day window, separate from the selected results date.

    Windows start today and advance by seven days. The only forward limit is
    Python's last representable complete week, rather than a sample-data limit.
    """
    exact = iso_date(filters["date"])
    selected = today if filters["date"] == "today" else exact
    desired = iso_date(calendar_value) or selected or today
    max_offset = max(0, ((date.max - today).days - 6) // 7 * 7)
    offset = max(0, min(max_offset, (desired - today).days // 7 * 7))
    start = today + timedelta(days=offset)
    days = [start + timedelta(days=number) for number in range(min(7, (date.max - start).days + 1))]
    last = days[-1]
    non_date = {key: value for key, value in filters.items() if key != "date" and value}
    all_filters = {key: value for key, value in filters.items() if value}

    def link(params):
        return "/events" + ("?" + urlencode(params) if params else "")

    calendar = {"calendar": start.isoformat()}
    label = {"": "All upcoming", "today": f"Today, {today:%B} {today.day}",
             "week": "This week", "month": "This month"}.get(filters["date"])
    if exact:
        label = f"{exact:%A, %B} {exact.day}, {exact.year}"
    start_label = f"{start:%b} {start.day}" + (f", {start.year}" if start.year != last.year else "")
    return {
        "campus_days": [{
            "iso": day.isoformat(), "weekday": day.strftime("%a"), "day": day.day,
            "month": day.strftime("%b"), "label": f"{day:%A, %B} {day.day}, {day.year}",
            "count": 0, "url": link({**non_date, **calendar, "date": day.isoformat()}),
            "selected": day == selected,
        } for day in days],
        "clear_date_url": link({**non_date, **calendar}),
        "calendar_value": start.isoformat(),
        "calendar_previous": link({**all_filters, "calendar": (start - timedelta(days=7)).isoformat()}) if offset else None,
        "calendar_next": link({**all_filters, "calendar": (start + timedelta(days=7)).isoformat()}) if offset < max_offset else None,
        "calendar_range": f"{start_label} – {last:%b} {last.day}, {last.year}",
        "date_label": label,
        "exact_date_label": label if exact else None,
    }
