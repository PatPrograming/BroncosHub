# app/services/registration_service.py
# Registration rules. UC-002, UC-004. Backlog #9, #10, #18.
#
# What goes here:
#   register_student(student, event):
#     - refuse cancelled or full events
#     - if already registered, return the existing registration (no duplicates)
#     - free event -> confirmed; paid event -> pending until payment (UC-003)
#   cancel_free_registration(student, registration):
#     - only the owner, only free events, only confirmed registrations
