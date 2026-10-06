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
