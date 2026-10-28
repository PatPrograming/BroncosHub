# app/__init__.py
# Builds the website (Flask's standard name for this is the 'app').
from flask import Flask, render_template
from flask_login import current_user

from app.extensions import db, login_manager, oauth
from config import Config


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)
    oauth.init_app(app)
    oauth.register(
        name="google",
        overwrite=True,
        client_id=app.config["GOOGLE_CLIENT_ID"],
        client_secret=app.config["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

    from app import models  # noqa: F401
    from app.routes.auth import auth_bp
    from app.routes.events import events_bp
    from app.routes.organizer import organizer_bp
    from app.routes.admin import admin_bp
    from app.routes.reporting import reporting_bp
    from app.routes.registrations import registrations_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(events_bp)
    app.register_blueprint(organizer_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(reporting_bp)
    app.register_blueprint(registrations_bp)

    @app.context_processor
    def inject_account():
        user = current_user.account_view() if current_user.is_authenticated else None
        return {"user": user, "preview_mode": False}

    @app.route("/")
    def home():
        return render_template(
            "index.html",
            events=[],
            categories=[],
            active_nav="home",
        )

    with app.app_context():
        db.create_all()

    return app
