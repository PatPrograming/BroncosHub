# config.py
# Website settings, read from the .env file.
import os
from datetime import timedelta

from dotenv import load_dotenv
from sqlalchemy.pool import StaticPool

load_dotenv()


class Config:
    APP_ENV = os.environ.get("APP_ENV", "development")
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-only-change-me"
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///broncoshub.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}} if SQLALCHEMY_DATABASE_URI.startswith("sqlite:") else {"pool_pre_ping": True}
    ALLOWED_EMAIL_DOMAIN = os.environ.get("ALLOWED_EMAIL_DOMAIN", "scu.edu")
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = APP_ENV == "production"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = APP_ENV == "production"
    REMEMBER_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    TRUSTED_HOSTS = [host.strip() for host in os.environ.get("TRUSTED_HOSTS", "").split(",") if host.strip()] or None
    # Enable only behind a proxy you control (Render provides one).
    TRUST_PROXY = os.environ.get("TRUST_PROXY", "false").lower() == "true"
    PAYMENT_PROVIDER = os.environ.get("PAYMENT_PROVIDER", "disabled")
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_LIVE_MODE = os.environ.get("STRIPE_LIVE_MODE", "false").lower() == "true"
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")
    # Use a persistent disk path when deploying. The default is instance/uploads.
    COVER_UPLOAD_DIR = os.environ.get("COVER_UPLOAD_DIR") or None
    COVER_MAX_BYTES = 5 * 1024 * 1024
    COVER_MAX_PIXELS = 20_000_000
    # Leave space for multipart form fields around the 5 MB image.
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024
    MAX_FORM_MEMORY_SIZE = 64 * 1024
    MAX_FORM_PARTS = 40


class TestConfig(Config):
    APP_ENV = "testing"
    TESTING = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    TRUSTED_HOSTS = None
    TRUST_PROXY = False
    PAYMENT_PROVIDER = "mock"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    ALLOWED_EMAIL_DOMAIN = "scu.edu"
    GOOGLE_CLIENT_ID = "test-client"
    GOOGLE_CLIENT_SECRET = "test-secret"
