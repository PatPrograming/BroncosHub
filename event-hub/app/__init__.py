# app/__init__.py
# Builds the website (Flask's standard name for this is the 'app').
from flask import Flask, render_template
from flask_login import current_user

from app.extensions import db, login_manager, oauth
from config import Config


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    from app.runtime import configure_runtime
    from app.routing import register_id_converter
    configure_runtime(app)
    register_id_converter(app)

    db.init_app(app)
    login_manager.init_app(app)
    from app.rate_limits import register_rate_limits
    register_rate_limits(app)
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
    from app.models.notification import Notification  # noqa: F401
    from app.services.notification_service import configure_notifications
    configure_notifications(app)
    from app.observability import configure_observability
    configure_observability(app)
    from app.routes.auth import auth_bp
    from app.routes.events import events_bp
    from app.services.home_service import home_context
    from app.routes.organizer import organizer_bp
    from app.routes.admin import admin_bp
    from app.routes.reporting import reporting_bp
    from app.routes.registrations import registrations_bp
    from app.routes.media import media_bp
    from app.routes.payments import payments_bp
    from app.routes.billing import billing_bp
    from app.routes.support import support_bp
    from app.services.cover_service import ensure_event_schema

    app.register_blueprint(auth_bp)
    app.register_blueprint(events_bp)
    app.register_blueprint(organizer_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(reporting_bp)
    app.register_blueprint(registrations_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(support_bp)
    from app.commands import register_commands
    register_commands(app)

    @app.context_processor
    def inject_account():
        user = current_user.account_view() if current_user.is_authenticated else None
        if user is not None:
            user["can_organize"] = current_user.can_organize
        return {
            "user": user,
            "preview_mode": False,
            "payment_provider": app.config["PAYMENT_PROVIDER"],
            "payment_live": app.config.get("STRIPE_LIVE_MODE", False),
            "google_site_verification": app.config.get("GOOGLE_SITE_VERIFICATION", ""),
        }

    @app.route("/")
    def home():
        return render_template(
            "index.html",
            **home_context(current_user.id if current_user.is_authenticated else None),
            active_nav="home",
        )

    with app.app_context():
        from app.database import initialize_database
        initialize_database(app)
        ensure_event_schema()

    return app
