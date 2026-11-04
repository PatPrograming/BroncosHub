# app/models/user.py
# SCU accounts can attend and host. Admin moderation is a separate permission.
from datetime import datetime, timezone

from flask import current_app
from flask_login import UserMixin

from app.extensions import db, login_manager


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    google_sub = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(20), nullable=False, default="student")
    year_level = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    events = db.relationship("Event", back_populates = 'organizer' )
    registrations = db.relationship("Registration", back_populates="student")

    @property
    def can_organize(self):
        """Hosting is available to SCU accounts without changing their role.

        Google sign-in verifies the identity before creating the account. Check
        the stored link and domain as well so unlinked/legacy rows do not gain
        hosting access just because they have an organizer role.
        """
        if (self.role not in ("student", "organizer", "admin")
                or not isinstance(self.google_sub, str) or not self.google_sub.strip()
                or not isinstance(self.email, str)):
            return False
        email = self.email.strip().lower()
        local, separator, domain = email.partition("@")
        allowed = current_app.config["ALLOWED_EMAIL_DOMAIN"].strip().lower()
        return bool(local and separator == "@" and domain == allowed
                    and len(email) <= 255 and not any(char.isspace() for char in email))

    def account_view(self):
        name = self.name or "Bronco"
        return {
            "id": self.id,
            "name": name,
            "first_name": name.split()[0],
            "email": self.email,
            "role": self.role,
            "year_level": self.year_level,
        }


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))
