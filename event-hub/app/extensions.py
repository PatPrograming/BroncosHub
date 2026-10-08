# app/extensions.py
# Shared tools created once and imported by other files.
from authlib.integrations.flask_client import OAuth
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
oauth = OAuth()
