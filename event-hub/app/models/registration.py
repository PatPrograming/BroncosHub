# app/models/registration.py
# Registration table: a student signed up for an event. UC-002, UC-004, UC-005.
#
# What goes here:
#   Columns: id, student_id, event_id, status (pending / confirmed / failed / cancelled), created_at
#   Rule: one registration per student per event (unique student_id + event_id)
#   Relationships: student, event, payment (one), ticket (one)
from datetime import datetime, timezone
from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Registration(db.Model):
    __tablename__ = "registrations"
    __table_args__ = (db.UniqueConstraint("student_id","event_id"),)
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.ForeignKey("users.id"),index= True, nullable = False)
    event_id = db.Column(db.ForeignKey("events.id"),nullable = False, index = True)  
    status = db.Column(db.String(20), nullable= False, default = "pending")
    created_at = db.Column(db.DateTime, default= _utcnow, nullable= False)
    student = db.relationship("User", back_populates="registrations")
    event = db.relationship("Event", back_populates = "registrations")
     
    @property
    def is_confirmed(self):
        return self.status == "confirmed"

     