# app/models/registration.py
# Registration table: a student signed up for an event. UC-002, UC-004, UC-005.
#
# What goes here:
#   Columns: id, student_id, event_id, status (pending / confirmed / failed / cancelled), created_at
#   Rule: one registration per student per event (unique student_id + event_id)
#   Relationships: student, event, payment (one), ticket (one)
