# app/models/user.py
# User table: students, organizers and admins. Backlog #1, #2, #3, #22.
#
# What goes here:
#   Columns: id, email (unique, must end in @scu.edu), name, password_hash,
#            role (student / organizer / admin), year_level (optional), created_at
#   Relationships: events (events they organize), registrations
#   Methods: set_password(), check_password() (use werkzeug.security), is_organizer
#   user_loader function so Flask-Login can load the signed-in user
