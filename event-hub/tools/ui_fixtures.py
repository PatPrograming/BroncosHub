"""Fictional, deterministic sample data for the isolated BroncosHub UI preview."""

from copy import deepcopy
from datetime import date, datetime

PREVIEW_TODAY = date(2026, 10, 8)
CATEGORIES = ["Arts & Culture", "Social", "Sports", "Tech", "Service", "Wellness"]
YEAR_LEVELS = {
    "first_year": "First year", "second_year": "Second year",
    "third_year": "Third year", "fourth_year": "Fourth year",
    "graduate": "Graduate", "unknown": "Prefer not to say",
}


def money(cents):
    return "Free" if cents == 0 else f"${cents / 100:,.2f}"


def decorate_event(event):
    """Derive display strings once, so every template shows the same details."""
    event = deepcopy(event)
    start = datetime.fromisoformat(event["start_value"])
    end = datetime.fromisoformat(event["end_value"])
    event.update(
        date_label=f"{start:%a, %b} {start.day}",
        day=f"{start.day:02d}", month=start.strftime("%b").upper(),
        time_label=f"{start.strftime('%I:%M %p').lstrip('0')} – {end.strftime('%I:%M %p').lstrip('0')}",
        price_label=money(event["price_cents"]),
        spots_left=max(0, event["capacity"] - event["registrations_count"]),
        registered=False,
    )
    return event


def make_event(event_id, title, category, location, organizer, start, end, price,
               capacity, count, art, description, owner="campus-club"):
    return decorate_event({
        "id": event_id, "title": title, "description": description,
        "category": category, "location": location, "organizer": organizer,
        "owner_id": owner, "start_value": start, "end_value": end,
        "price_cents": price, "capacity": capacity,
        "registrations_count": count, "art": art, "status": "published",
    })


EVENTS = [
    make_event(1, "Sunset Cinema on Mission Lawn", "Arts & Culture", "Mission Lawn",
               "Broncos Film Club", "2026-10-09T18:30", "2026-10-09T21:00", 0,
               120, 86, "sunset.svg",
               "Bring a blanket, grab a spot on the lawn, and settle in for a movie under the stars. "
               "Meet other Broncos before the screening, with popcorn and a golden-hour playlist to start the evening.",
               "preview-user"),
    make_event(2, "Find Your People: Club Fair", "Social", "Benson Memorial Center",
               "Campus Activities Board", "2026-10-08T12:00", "2026-10-08T15:00", 0,
               250, 174, "club-fair.svg",
               "Meet student clubs, try a quick activity, and discover a new community. "
               "Drop in between classes and see what campus life has to offer."),
    make_event(3, "After Hours: Live on Campus", "Arts & Culture", "Locatelli Student Activity Center",
               "SCU Music Collective", "2026-10-10T19:00", "2026-10-10T22:00", 800,
               160, 112, "music.svg",
               "A relaxed night of live sets from student musicians. Discover your next favorite campus band "
               "and catch up with friends between performances."),
    make_event(4, "Build Night: Ideas into Apps", "Tech", "Sobrato Campus for Discovery and Innovation",
               "Broncos Builders", "2026-10-13T18:00", "2026-10-13T21:00", 500,
               48, 30, "tech.svg",
               "Bring an idea or join a team for a hands-on evening of building. All experience levels are welcome. "
               "Bring a laptop; we will bring prompts, snacks, and time to share what you made.",
               "preview-user"),
    make_event(5, "Courtside: Pickup & Pizza", "Sports", "Leavey Center",
               "Campus Recreation", "2026-10-15T17:00", "2026-10-15T19:00", 0,
               40, 28, "basketball.svg",
               "A friendly pickup basketball session followed by pizza. Come with friends or join a team when you arrive. "
               "Bring comfortable shoes and a water bottle.", "preview-user"),
    make_event(6, "A Little Time, a Big Difference", "Service", "SCU Forge Garden",
               "Broncos Give Back", "2026-10-17T09:00", "2026-10-17T12:00", 0,
               30, 21, "volunteer.svg",
               "Spend a morning helping with community garden projects alongside other students. "
               "Wear clothes that can get a little muddy; tools and guidance are provided."),
    make_event(7, "Slow Sunday: Coffee & Connection", "Wellness", "Benson Garden",
               "Student Wellness Collective", "2026-10-11T10:00", "2026-10-11T11:30", 0,
               36, 19, "coffee.svg",
               "Take a gentle break from the week with coffee, conversation, and a short guided reflection. "
               "Come as you are; there is no preparation needed."),
    make_event(8, "Make Something: Print Studio", "Arts & Culture", "Art and Art History Building",
               "Campus Makers", "2026-10-20T16:30", "2026-10-20T18:30", 1200,
               24, 14, "makers.svg",
               "Create a small print to take home in this beginner-friendly studio session. "
               "Materials are included, and student hosts will walk you through each step.", "preview-user"),
]

REGISTRATIONS = [
    {"id": 101, "event_id": 2, "status": "confirmed", "payment_status": "not_required"},
    {"id": 102, "event_id": 3, "status": "confirmed", "payment_status": "succeeded"},
    {"id": 103, "event_id": 4, "status": "pending", "payment_status": "pending"},
]

REPORTS = [
    {"id": 1, "event_id": 6, "reason": "Please confirm the listed meeting location.",
     "reporter": "Sample student", "date_label": "Oct 8"},
    {"id": 2, "event_id": 8, "reason": "The event description may need a materials clarification.",
     "reporter": "Sample student", "date_label": "Oct 8"},
]
