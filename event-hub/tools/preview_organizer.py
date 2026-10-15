"""Try the real organizer routes with fictional records in a temporary database.

Run from event-hub: python tools/preview_organizer.py --port 5075
Nothing here is registered by the production application factory.
"""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import secrets
import sys

from flask import abort, redirect, request, url_for
from flask_login import login_user

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from app.extensions import db
from app.models import Event, Registration, Report, User
from config import TestConfig


def create_organizer_demo():
    class DemoConfig(TestConfig):
        TESTING = False
        SECRET_KEY = secrets.token_hex(32)
        SESSION_COOKIE_NAME = "broncoshub_organizer_demo"
        TRUSTED_HOSTS = ["localhost", "127.0.0.1"]
        GOOGLE_CLIENT_ID = ""
        GOOGLE_CLIENT_SECRET = ""

    app = create_app(DemoConfig)
    # TestConfig explicitly selects in-memory SQLite, ignoring DATABASE_URL.
    with app.app_context():
        organizer = User(name="Alex Chen", email="alex.demo@scu.edu",
                         google_sub="demo-organizer", role="organizer")
        other = User(name="Other Demo Host", email="other.demo@scu.edu",
                     google_sub="demo-other-organizer", role="organizer")
        db.session.add_all([organizer, other])
        db.session.flush()
        organizer_id = organizer.id
        today = datetime.now(timezone.utc).replace(tzinfo=None, hour=18, minute=0,
                                                 second=0, microsecond=0)

        def event(title, days, category, location, capacity, **extra):
            row = Event(organizer=organizer, title=title, category=category,
                        description="Fictional event for the local organizer demo.",
                        location=location, start_time=today + timedelta(days=days),
                        end_time=today + timedelta(days=days, hours=2),
                        capacity=capacity, **extra)
            db.session.add(row)
            return row

        cinema = event("Sunset Cinema on Mission Lawn", 2, "Arts & Culture", "Mission Lawn", 40)
        workshop = event("Build & Brew", 4, "Tech", "SCDI Innovation Zone", 8, price_cents=500)
        coffee = event("Coffee & Connections", 6, "Wellness", "Benson Center", None)
        cancelled = event("Club Picnic", -2, "Social", "Alumni Park", 30, status="cancelled")
        private = Event(organizer=other, title="Another organizer's private statistics",
                        category="Social", location="Benson Center", capacity=25,
                        start_time=today, end_time=today + timedelta(hours=1))
        db.session.add(private)
        years = ["first_year"] * 6 + ["second_year"] * 4 + ["third_year"] * 3
        years += ["fourth_year"] * 2 + ["graduate", None, None]
        for number, year in enumerate(years, 1):
            student = User(name=f"Demo Student {number}", email=f"student{number}.demo@scu.edu",
                           google_sub=f"demo-student-{number}", year_level=year)
            db.session.add(Registration(student=student, event=cinema, status="confirmed"))
            if number <= 8:
                db.session.add(Registration(student=student, event=workshop, status="confirmed"))
            if number <= 3:
                db.session.add(Registration(student=student, event=coffee,
                                            status=["pending", "cancelled", "failed"][number - 1]))
            if number == 1:
                db.session.add(Registration(student=student, event=cancelled, status="confirmed"))
                db.session.add(Registration(student=student, event=private, status="confirmed"))
        db.session.commit()

        admin = User(name="Jordan Rivera", email="admin.demo@scu.edu",
                     google_sub="demo-admin", role="admin")
        db.session.add(admin)
        students = db.session.scalars(db.select(User).where(User.role == "student").order_by(User.id)).all()
        db.session.add_all([
            Report(event=cinema, reporter=students[0], reason="misleading",
                   details="The poster and description appear to list different screening times. Could someone check?"),
            Report(event=workshop, reporter=students[1], reason="spam",
                   details="The listing asks students to send payment to an unrelated outside account."),
            Report(event=workshop, reporter=students[2], reason="inappropriate",
                   details="This event directs students to an unverified payment link in its description."),
            Report(event=coffee, reporter=students[3], reason="other", status="dismissed",
                   details="I wasn't sure whether this meetup was open to all year levels.",
                   reviewed_by=admin, reviewed_at=today - timedelta(days=1),
                   review_note="The meetup is open to all SCU students. No change was needed."),
        ])
        db.session.commit()
        admin_id, student_id, report_event_id = admin.id, students[-1].id, cinema.id

    @app.context_processor
    def demo_notice():
        return {"organizer_demo": True}

    @app.before_request
    def local_demo_only():
        # Also enforce this on Flask 3.0, before TRUSTED_HOSTS was supported.
        if request.host.partition(":")[0].lower() not in ("localhost", "127.0.0.1"):
            abort(400)

    @app.get("/demo")
    def enter_demo():
        login_user(db.session.get(User, organizer_id))
        return redirect(url_for("organizer.dashboard"))

    @app.get("/demo/admin")
    def enter_moderation_demo():
        login_user(db.session.get(User, admin_id))
        return redirect(url_for("admin.reports"))

    @app.get("/demo/student")
    def enter_reporting_demo():
        login_user(db.session.get(User, student_id))
        return redirect(url_for("reporting.report_event", event_id=report_event_id))

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real organizer pages with fictional database records")
    parser.add_argument("--port", type=int, default=5075)
    args = parser.parse_args()
    print(f"Organizer demo: http://localhost:{args.port}/demo", flush=True)
    print(f"Moderation demo: http://localhost:{args.port}/demo/admin", flush=True)
    create_organizer_demo().run(host="127.0.0.1", port=args.port, debug=False)
