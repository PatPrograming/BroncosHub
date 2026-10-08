# app/models/user.py
# User table: students, organizers and admins. Backlog #1, #2, #22.
from datetime import datetime, timezone

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
