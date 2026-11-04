# app/models/event.py
# Event table. UC-001, UC-006, UC-007.
#
# What goes here:
#   Columns: id, organizer_id, title, description, location, category,
#            start_time, end_time, price_cents (0 = free), capacity (blank = unlimited),
#            status (published / cancelled / removed), created_at
#   Relationships: organizer, registrations
#   Helpers: is_free, is_cancelled, active_registration_count(), spots_left()
#   Store prices in cents (1500 = $15.00) to avoid rounding errors
from datetime import datetime, timezone
from sqlalchemy import inspect
from app.extensions import db

# Default poster artwork per category, matching tools/preview_ui.py.
CATEGORY_ART = {"Arts & Culture": "makers.svg", "Social": "club-fair.svg",
                "Sports": "basketball.svg", "Tech": "tech.svg",
                "Service": "volunteer.svg", "Wellness": "coffee.svg"}


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Event(db.Model):

    __tablename__ = "events"
    id = db.Column(db.Integer, primary_key=True)
    organizer_id = db.Column(db.ForeignKey("users.id"), nullable= False, index= True)
    title= db.Column(db.String(100), nullable = False)
    description= db.Column(db.Text, nullable = True)
    location = db.Column(db.String(100), nullable = False)
    category = db.Column(db.String(50), nullable = False)
    start_time = db.Column(db.DateTime, index = True, nullable = False)
    end_time = db.Column(db.DateTime, nullable = False)
    price_cents = db.Column(db.Integer, default= 0, nullable = False)
    capacity = db.Column(db.Integer, nullable= True,)
    status = db.Column(db.String(20), default = "published", nullable = False)
    created_at = db.Column(db.DateTime ,default= _utcnow)
    cover_image_path = db.Column(db.String(64), nullable=True)
    organizer = db.relationship("User", back_populates='events')
    registrations = db.relationship("Registration", back_populates="event")
    
    @property
    def is_free(self):
        return self.price_cents == 0
    
    @property #allows you to define methods in a class that behave exactly like standard attributes
    #read as event.is_free, no ().
    def is_cancelled(self):
        # Removed events also close registrations, checkout, and organizer edits.
        return self.status in ("cancelled", "removed")

    @property
    def is_removed(self):
        return self.status == "removed"
    
    def active_registration_count(self):
        return sum(1 for registration in self.registrations if registration.is_confirmed)

    def spots_left(self):
        if self.capacity is None:
            return None
        if inspect(self).persistent:
            from app.services.event_write_service import occupied_seats
            taken = occupied_seats(self.id)
        else:
            # Preview fixtures and isolated model tests have no persisted holds.
            taken = self.active_registration_count()
        return max(0, self.capacity - taken)
       
    def event_view(self):
        start, end = self.start_time, self.end_time
        start_clock = start.strftime('%I:%M %p').lstrip('0')
        end_clock = end.strftime('%I:%M %p').lstrip('0')
        date_label = f"{start:%a, %b} {start.day}"
        time_label = f"{start_clock} – {end_clock}"
        if start.date() != end.date():
            # Overnight and multi-day events must expose their end date. Keep
            # years on the date range so a December/January boundary is clear.
            date_label = f"{start:%b} {start.day}, {start.year} – {end:%b} {end.day}, {end.year}"
            time_label = f"{start:%b} {start.day}, {start_clock} – {end:%b} {end.day}, {end_clock}"
        view = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "location": self.location,
            "category": self.category,
            "status": self.status,
            "capacity": self.capacity,
            "price_cents": self.price_cents,
            "organizer": self.organizer.name,
            "registrations_count": self.active_registration_count(),
            "registered": False,
            "cover_url": f"/media/events/{self.id}/cover" if self.cover_image_path else "",
            "art": CATEGORY_ART.get(self.category, "campus.svg"),
            "price_label": "Free" if self.is_free else f"${self.price_cents / 100:,.2f}",
            "date_label": date_label,
            "day": f"{start.day:02d}",
            "month": start.strftime("%b").upper(),
            "time_label": time_label,
            "start_value": start.strftime("%Y-%m-%dT%H:%M"),
            "end_value": end.strftime("%Y-%m-%dT%H:%M"),
        }
        # Unlimited events leave spots_left out; templates compare it with 0.
        spots = self.spots_left()
        if spots is not None:
            view["spots_left"] = spots
        return view

    def attendee_event_view(self):
        """Keep a receipt for attendees without redisplaying removed content.

        Dates and admission amounts remain useful registration history. Admin
        review and the owning organizer use event_view for their private record.
        """
        view = self.event_view()
        if self.is_removed:
            view.update(title="Event removed", description="This event is no longer available.",
                        location="Unavailable", category="Removed event", organizer="Unavailable",
                        cover_url="", art="campus.svg")
        return view
