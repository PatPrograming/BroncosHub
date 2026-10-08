# config.py
# Website settings, read from the .env file.
import os

from dotenv import load_dotenv
from sqlalchemy.pool import StaticPool

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-only-change-me"
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///broncoshub.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    ALLOWED_EMAIL_DOMAIN = os.environ.get("ALLOWED_EMAIL_DOMAIN", "scu.edu")
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    ALLOWED_EMAIL_DOMAIN = "scu.edu"
    GOOGLE_CLIENT_ID = "test-client"
    GOOGLE_CLIENT_SECRET = "test-secret"
