# app/models/payment.py
# Payment table: admission payments for paid events. UC-003, UC-008.
#
# What goes here:
#   Columns: id, registration_id (unique), amount_cents,
#            status (pending / succeeded / declined / cancelled), provider_reference (unique), created_at
#   Only 'succeeded' payments count as revenue
