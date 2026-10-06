# app/services/stats_service.py
# Organizer statistics. UC-008. Backlog #19, #20, #21.
#
# What goes here:
#   event_statistics(event) returns:
#     - active: confirmed registrations (cancellations excluded) (#19)
#     - by_year: counts per year level, missing = 'Unknown' (#20)
#     - revenue_cents: total of succeeded payments only (#21)
#   Counts are registrations, not actual attendance
