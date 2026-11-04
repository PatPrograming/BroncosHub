"""Deployment guards and shared HTTP behavior, without a live hosting account."""
from io import BytesIO
from sqlalchemy.exc import OperationalError
import pytest
from app import create_app
from app.extensions import db
from config import TestConfig


class ProductionTestConfig(TestConfig):
    APP_ENV = "production"
    TESTING = False
    SECRET_KEY = "a" * 64
    TRUSTED_HOSTS = ["broncoshub.example"]
    PAYMENT_PROVIDER = "disabled"


@pytest.mark.parametrize("values, message", [
    ({"SECRET_KEY": "dev-only-change-me"}, "SECRET_KEY"),
    ({"TRUSTED_HOSTS": None}, "TRUSTED_HOSTS"),
    ({"PAYMENT_PROVIDER": "mock"}, "Mock payments"),
    ({"DEBUG": True}, "Debug and testing"),
    ({"TESTING": True}, "Debug and testing"),
])
def test_production_refuses_unsafe_config(values, message):
    config = type("UnsafeConfig", (ProductionTestConfig,), values)
    with pytest.raises(RuntimeError, match=message):
        create_app(config)


def test_production_hosts_and_cookie_flags():
    app = create_app(ProductionTestConfig)
    client = app.test_client()
    good = client.get("/healthz", base_url="https://broncoshub.example")
    assert good.status_code == 200
    assert good.json == {"status": "ok"}
    assert "max-age=" in good.headers["Strict-Transport-Security"]
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["REMEMBER_COOKIE_SECURE"] is True
    bad = client.get("/healthz", base_url="https://attacker.example")
    assert bad.status_code == 400
    assert b"attacker.example" not in bad.data


def test_headers_and_styled_missing_page(client):
    response = client.get("/missing-page")
    assert response.status_code == 404
    assert b"BroncosHub" in response.data
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert "no-store" in response.headers["Cache-Control"]
    assert "Strict-Transport-Security" not in response.headers


def test_global_error_keeps_method_allow_header(client):
    response = client.post("/healthz")
    assert response.status_code == 405
    assert "GET" in response.headers["Allow"]
    assert b"BroncosHub" in response.data


def test_health_failure_does_not_reveal_database_details(app, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("secret database password")
    monkeypatch.setattr(db.session, "execute", broken)
    response = app.test_client().get("/healthz")
    assert response.status_code == 503
    assert response.json == {"status": "unavailable"}
    assert b"secret" not in response.data


def test_upload_limits_return_useful_branded_page(app):
    @app.post("/_test/upload")
    def upload():
        from flask import request
        return str(len(request.files))
    response = app.test_client().post("/_test/upload", data={"cover": (BytesIO(b"x" * (7 * 1024 * 1024)), "large.png")})
    assert response.status_code == 413
    assert b"5 MB" in response.data and b"BroncosHub" in response.data


def test_database_lock_is_retryable_but_not_exposed(app):
    @app.get("/_test/locked")
    def locked():
        raise OperationalError("secret SQL", {}, Exception("database is locked"))
    response = app.test_client().get("/_test/locked")
    assert response.status_code == 503
    assert b"secret SQL" not in response.data
    assert b"Check My Events" in response.data


@pytest.mark.parametrize('overrides,message', [
    ({'APP_ENV': 'prodution'}, 'APP_ENV'),
    ({'PAYMENT_PROVIDER': 'stirpe'}, 'PAYMENT_PROVIDER'),
    ({'STRIPE_SECRET_KEY': ''}, 'STRIPE_SECRET_KEY'),
    ({'STRIPE_LIVE_MODE': True}, 'STRIPE_SECRET_KEY'),
    ({'STRIPE_WEBHOOK_SECRET': ''}, 'STRIPE_WEBHOOK_SECRET'),
    ({'PUBLIC_BASE_URL': 'http://broncoshub.example'}, 'PUBLIC_BASE_URL'),
    ({'PUBLIC_BASE_URL': 'https://attacker.example'}, 'PUBLIC_BASE_URL'),
    ({'PUBLIC_BASE_URL': 'https://broncoshub.example/path'}, 'PUBLIC_BASE_URL'),
])
def test_production_rejects_broken_payment_settings(overrides, message):
    settings = dict(PAYMENT_PROVIDER='stripe', STRIPE_SECRET_KEY='sk_test_example',
                    STRIPE_WEBHOOK_SECRET='whsec_example', STRIPE_LIVE_MODE=False,
                    PUBLIC_BASE_URL='https://broncoshub.example')
    settings.update(overrides)
    with pytest.raises(RuntimeError, match=message):
        create_app(type('BadPayments', (ProductionTestConfig,), settings))


def test_production_launcher_never_spawns_with_development_settings(monkeypatch):
    import app as app_module
    from tools import start_production
    from types import SimpleNamespace
    monkeypatch.setattr(app_module, 'create_app', lambda: SimpleNamespace(config={'APP_ENV': 'development'}))
    def unexpected(*args, **kwargs):
        pytest.fail('Web server must not launch with development settings')
    monkeypatch.setattr(start_production.subprocess, 'Popen', unexpected)
    with pytest.raises(RuntimeError, match='APP_ENV=production'):
        start_production.main()
