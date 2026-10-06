# app/routes/organizer.py
# Organizer pages. UC-006 to UC-008. Backlog #3-#5, #11, #12, #19-#21, #23.
#
# What goes here:
#   Blueprint named 'organizer', URLs start with /organizer
#   organizer_required check: only organizers/admins (#3)
#   /organizer/                       - dashboard of my events (#23)
#   /organizer/events/new             - create and publish an event with validation (UC-006, #4, #5)
#   /organizer/events/<id>/edit       - edit my event (UC-007, #11)
#   /organizer/events/<id>/cancel     - cancel my event, invalidate tickets (UC-007, #12)
#   /organizer/events/<id>/stats      - statistics (UC-008, #19-#21)
#   Organizers may only manage events they own
