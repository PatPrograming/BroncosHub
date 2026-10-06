# app/models/ticket.py
# Ticket table: one ticket per paid, confirmed registration. UC-002, UC-003, UC-007.
#
# What goes here:
#   Columns: id, registration_id (unique), code (random, unique), is_valid, issued_at
#   Set is_valid = False when the event is cancelled
