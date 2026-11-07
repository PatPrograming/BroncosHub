"""Production defaults and consistent responses shared by every page."""
from flask import jsonify, render_template, request
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from werkzeug.exceptions import HTTPException, SecurityError
from werkzeug.middleware.proxy_fix import ProxyFix
from urllib.parse import urlsplit

from app.extensions import db


ERROR_PAGES = {
    400: ("That request didn't work", "Check the details and try again."),
    403: ("This page isn't available to your account", "Use an account with access to this page."),
    404: ("We couldn't find that page", "It may have moved or is no longer available."),
    405: ("That action isn't available", "Go back to the page and use its buttons to continue."),
    409: ("Something changed", "Refresh the page to see the latest details, then try again."),
    413: ("That upload is too large", "Choose one cover image of 5 MB or smaller, then try again."),
    429: ("Please try again shortly", "There have been too many requests. Give it a moment."),
    500: ("Something went wrong", "We couldn't finish that request. Please try again shortly."),
    503: ("We're busy right now", "Please wait a moment and try again. Check My Events before retrying a registration."),
}


def public_base_url_is_valid(app):
    base = app.config.get("PUBLIC_BASE_URL", "")
    try:
        parsed = urlsplit(base)
        return (parsed.scheme == "https" and parsed.hostname in (app.config.get("TRUSTED_HOSTS") or [])
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and parsed.path in ("", "/") and not parsed.query and not parsed.fragment
                and "\\" not in base and not any(ord(c) <= 32 for c in base))
    except ValueError:
        return False


def configure_runtime(app):
    from app.services.remote_cover_storage import validate_cover_storage_config
    validate_cover_storage_config(app.config)
    if app.config.get("REQUIRE_EXTERNAL_STORAGE"):
        if not str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).startswith("postgresql"):
            raise RuntimeError("This deployment requires an external PostgreSQL database.")
        if app.config.get("COVER_STORAGE_BACKEND") != "supabase":
            raise RuntimeError("This deployment requires persistent Supabase cover storage.")
    if app.config.get("APP_ENV") not in {"development", "testing", "production"}:
        raise RuntimeError("APP_ENV must be development, testing, or production.")
    if app.config.get("PAYMENT_PROVIDER") not in {"disabled", "mock", "stripe"}:
        raise RuntimeError("PAYMENT_PROVIDER must be disabled, mock, or stripe.")
    if app.config.get("APP_ENV") == "production":
        secret = app.config.get("SECRET_KEY", "")
        if not isinstance(secret, str) or len(secret) < 32 or secret in {"dev-only-change-me", "test-secret"}:
            raise RuntimeError("Production needs a randomly generated SECRET_KEY of at least 32 characters.")
        if not app.config.get("TRUSTED_HOSTS"):
            raise RuntimeError("Set TRUSTED_HOSTS to the deployed hostname before starting production.")
        if app.config.get("DEBUG") or app.config.get("TESTING"):
            raise RuntimeError("Debug and testing modes must be off in production.")
        domain = (app.config.get("ALLOWED_EMAIL_DOMAIN") or "").strip().lower()
        if domain != "scu.edu":
            raise RuntimeError("ALLOWED_EMAIL_DOMAIN must be scu.edu in production.")
        if not public_base_url_is_valid(app):
            raise RuntimeError("PUBLIC_BASE_URL must be the HTTPS origin of an explicit TRUSTED_HOSTS entry.")
        # Google secrets are required before students can sign in, but the public
        # home/privacy pages must still deploy for Auth Platform branding checks.
        google_id = app.config.get("GOOGLE_CLIENT_ID") or ""
        google_secret = app.config.get("GOOGLE_CLIENT_SECRET") or ""
        if bool(google_id) != bool(google_secret):
            raise RuntimeError("Set both GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET together.")
        if app.config.get("PAYMENT_PROVIDER") == "mock":
            raise RuntimeError("Mock payments cannot run in production. Configure real payments or leave them disabled.")
        if app.config.get("PAYMENT_PROVIDER") == "stripe":
            prefix = "sk_live_" if app.config.get("STRIPE_LIVE_MODE") else "sk_test_"
            if not app.config.get("STRIPE_SECRET_KEY", "").startswith(prefix):
                raise RuntimeError("Set a STRIPE_SECRET_KEY that matches STRIPE_LIVE_MODE.")
            if not app.config.get("STRIPE_WEBHOOK_SECRET", "").startswith("whsec_"):
                raise RuntimeError("Set STRIPE_WEBHOOK_SECRET for the connected-account webhook.")
        app.config.update(SESSION_COOKIE_SECURE=True, REMEMBER_COOKIE_SECURE=True)
    if app.config.get("TRUST_PROXY"):
        # Exactly one trusted reverse proxy. Never trust the forwarded host.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self' https://checkout.stripe.com https://accounts.stripe.com https://connect.stripe.com"
        )
        if response.mimetype == "text/html":
            # Profile, registrations and CSRF tokens must not survive sign-out
            # in a shared browser cache or in a proxy/CDN cache.
            response.headers.setdefault("Cache-Control", "no-store")
        if app.config.get("APP_ENV") == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        if isinstance(error, SecurityError):
            # Host rejection happens before Flask can build template URLs.
            return "<!doctype html><title>Bad request · BroncosHub</title><h1>Bad request</h1><p>Open BroncosHub using its official website address.</p>", 400
        status = error.code or 500
        title, message = ERROR_PAGES.get(status, ("We couldn't finish that request", "Please return to Explore and try again."))
        response = error.get_response()
        response.data = render_template("errors/error.html", status=status, title=title, message=message)
        response.content_type = "text/html; charset=utf-8"
        return response

    @app.errorhandler(500)
    def internal_error(error):
        from app.observability import report_failure
        report_failure(app, "http_500")
        db.session.rollback()
        title, message = ERROR_PAGES[500]
        return render_template("errors/error.html", status=500, title=title, message=message), 500

    @app.errorhandler(OperationalError)
    def database_error(error):
        from app.observability import report_failure
        report_failure(app, "database_failure")
        db.session.rollback()
        # Retryable lock contention has a distinct response. Other DB failures
        # remain server errors; log the exception, never expose it to visitors.
        sqlstate = getattr(error.orig, "sqlstate", None)
        busy = ("database is locked" in str(error).lower()
                or "database is busy" in str(error).lower()
                or sqlstate in {"55P03", "40P01", "40001"})
        status = 503 if busy else 500
        app.logger.exception("Database operation failed")
        title, message = ERROR_PAGES[status]
        return render_template("errors/error.html", status=status, title=title, message=message), status

    @app.get("/healthz")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            from app.observability import report_failure
            report_failure(app, "database_failure")
            db.session.rollback()
            app.logger.exception("Database health check failed")
            return jsonify(status="unavailable"), 503
        return jsonify(status="ok")
