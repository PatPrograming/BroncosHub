# config.py
# Website settings, read from the .env file.
#
# What goes here:
#   Config class: SECRET_KEY, SQLALCHEMY_DATABASE_URI, ALLOWED_EMAIL_DOMAIN (scu.edu), PAYMENT_PROVIDER
#   TestConfig class: same settings but TESTING = True and an in-memory SQLite database
#   Use python-dotenv's load_dotenv() to read .env
