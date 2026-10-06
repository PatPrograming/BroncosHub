# app/routes/registrations.py
# Student registration pages. UC-002 to UC-005. Backlog #9, #10, #13-#18.
#
# What goes here:
#   Blueprint named 'registrations'
#   /events/<id>/register                 - confirm and register (UC-002, #9, #10)
#   /registrations/<id>/checkout          - pay for a paid event (UC-003, #13-#15)
#   /my-registrations                     - list my registrations and status (UC-005, #16)
#   /my-registrations/<id>                - registration, payment and ticket details (UC-005, #17)
#   /my-registrations/<id>/cancel         - cancel a free registration (UC-004, #18)
#   Students may only see their own registrations
