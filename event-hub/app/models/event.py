# app/models/event.py
# Event table. UC-001, UC-006, UC-007.
#
# What goes here:
#   Columns: id, organizer_id, title, description, location, category,
#            start_time, end_time, price_cents (0 = free), capacity (blank = unlimited),
#            status (published / cancelled), created_at
#   Relationships: organizer, registrations
#   Helpers: is_free, is_cancelled, active_registration_count(), spots_left()
#   Store prices in cents (1500 = $15.00) to avoid rounding errors
from datetime import datetime, timezone
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
    organizer = db.relationship("User", back_populates='events')
    registrations = db.relationship("Registration", back_populates="event")
    
    @property
    def is_free(self):
        return self.price_cents == 0
    
    @property #allows you to define methods in a class that behave exactly like standard attributes
    #read as event.is_free, no ().
    def is_cancelled(self):
        return self.status == "cancelled"
    
    def active_registration_count(self):
        return sum(1 for registration in self.registrations if registration.is_confirmed)

    def spots_left(self):
       if self.capacity is None:
           return None
       else:
           return max(0, self.capacity - self.active_registration_count())
       
    def event_view(self):
        start, end = self.start_time, self.end_time
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
            "cover_url": "",
            "art": CATEGORY_ART.get(self.category, "campus.svg"),
            "price_label": "Free" if self.is_free else f"${self.price_cents / 100:,.2f}",
            "date_label": f"{start:%a, %b} {start.day}",
            "day": f"{start.day:02d}",
            "month": start.strftime("%b").upper(),
            "time_label": f"{start.strftime('%I:%M %p').lstrip('0')} – {end.strftime('%I:%M %p').lstrip('0')}",
            "start_value": start.strftime("%Y-%m-%dT%H:%M"),
            "end_value": end.strftime("%Y-%m-%dT%H:%M"),
        }
        # Unlimited events leave spots_left out; templates compare it with 0.
        spots = self.spots_left()
        if spots is not None:
            view["spots_left"] = spots
        return view