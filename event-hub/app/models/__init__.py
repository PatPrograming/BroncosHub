# app/models/__init__.py
# Database tables. One file per table; import them all here.
from app.models.event import Event
from app.models.user import User
from app.models.registration import Registration
from app.models.report import Report

__all__ = ["Event", "User", "Registration", "Report"]
