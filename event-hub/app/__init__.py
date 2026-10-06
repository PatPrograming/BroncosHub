# app/__init__.py
# Builds the website (Flask's standard name for this is the 'app').
#
# What goes here:
#   create_app(config_class) function that:
#     - creates the Flask app and loads settings from config.py
#     - connects the database (db) and sign-in system (login_manager) from extensions.py
#     - imports the models so the database knows every table
#     - registers the blueprints from app/routes/ (auth, events, registrations, organizer, admin)
#     - adds the home page route '/'
#     - creates the database tables
