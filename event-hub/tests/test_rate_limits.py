"""Limits protect writes across users, sessions, processes and target IDs."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import User
from app.models.rate_limit import RateLimitBucket
from app import rate_limits
from config import TestConfig
from test_postgres import pg_config  # Shared opt-in, uniquely isolated PostgreSQL schema.


class LimitedConfig(TestConfig):
    RATE_LIMIT_ENABLED = True
    RATE_LIMITS = {name: ((2, 60),) for name in rate_limits.DEFAULT_LIMITS}
    GOOGLE_CLIENT_ID = ""
    GOOGLE_CLIENT_SECRET = ""


def build_app(config=LimitedConfig):
    app = create_app(config)
    # The application factory calls this in production. Keeping the helper
    # idempotent also supports isolated test apps that register it separately.
    rate_limits.register_rate_limits(app)
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture
def limited_app(monkeypatch):
    monkeypatch.setattr(rate_limits, "_now", lambda: 100)
    application = build_app()
    with application.app_context():
        db.session.add_all([
            User(name="First Student", email="first@scu.edu", google_sub="first"),
            User(name="Second Student", email="second@scu.edu", google_sub="second"),
        ])
        db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


def signed_in(app, user_id=1):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def test_factory_installs_limits_without_extra_registration(monkeypatch):
    monkeypatch.setattr(rate_limits, "_now", lambda: 100)
    application = create_app(LimitedConfig)
    try:
        client = application.test_client()
        assert client.get("/auth/google").status_code == 302
        assert client.get("/auth/google").status_code == 302
        assert client.get("/auth/google").status_code == 429
    finally:
        with application.app_context():
            db.session.remove()
            db.engine.dispose()


def test_auth_starts_stop_with_branded_429_retry_after_and_no_raw_ip(limited_app):
    client = limited_app.test_client()
    for _ in range(2):
        assert client.get("/auth/google", environ_overrides={"REMOTE_ADDR": "192.0.2.51"}).status_code == 302
    response = client.get("/auth/google", environ_overrides={"REMOTE_ADDR": "192.0.2.51"})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "20"
    assert b"BroncosHub" in response.data and b"Please try again shortly" in response.data
    with limited_app.app_context():
        buckets = db.session.scalars(select(RateLimitBucket)).all()
        assert len(buckets) == 1
        assert "192.0.2.51" not in buckets[0].key
        assert buckets[0].count == 3


def test_auth_buckets_ignore_untrusted_forwarded_headers_and_share_sessions(limited_app):
    client = limited_app.test_client()
    assert client.get("/auth/google", headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 302
    assert client.get("/auth/google", headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 302
    fresh_browser = limited_app.test_client()
    assert fresh_browser.get("/auth/google", headers={"X-Forwarded-For": "198.51.100.3"}).status_code == 429


def test_ipv6_address_rotation_within_subnet_cannot_bypass(limited_app):
    client = limited_app.test_client()
    for suffix, expected in [("1", 302), ("2", 302), ("3", 429)]:
        response = client.get("/auth/google", environ_overrides={"REMOTE_ADDR": f"2001:db8:1:2::{suffix}"})
        assert response.status_code == expected


def test_different_users_do_not_share_write_limits_even_on_same_ip(limited_app):
    first = signed_in(limited_app)
    second = signed_in(limited_app, 2)
    for _ in range(2):
        assert first.post("/profile").status_code == 400  # invalid CSRF still consumes an attempt
    assert first.post("/profile").status_code == 429
    assert second.post("/profile").status_code == 400
    assert signed_in(limited_app, 1).post("/profile").status_code == 429


def test_limited_profile_submission_never_changes_profile(limited_app):
    client = signed_in(limited_app)
    assert client.get("/profile").status_code == 200
    with client.session_transaction() as session:
        token = session["moderation_csrf"]["token"]
    for name, status in [("One", 302), ("Two", 302), ("Must Not Save", 429)]:
        response = client.post("/profile", data={"csrf_token": token, "name": name, "year_level": "unknown"})
        assert response.status_code == status
    with limited_app.app_context():
        assert db.session.get(User, 1).name == "Two"


def test_event_create_edit_and_cancel_share_write_bucket(limited_app):
    client = signed_in(limited_app)
    assert client.post("/organizer/events/new").status_code == 400
    assert client.post("/organizer/events/100/edit").status_code in {400, 404}
    assert client.post("/organizer/events/200/cancel").status_code == 429


@pytest.mark.parametrize("paths", [
    ("/events/1/register", "/my-registrations/2/cancel", "/events/3/register"),
    ("/registrations/1/checkout", "/registrations/2/checkout", "/registrations/3/checkout"),
    ("/events/1/report", "/events/2/report", "/events/3/report"),
    ("/organizer/payments/onboard", "/organizer/payments/refresh", "/organizer/payments/onboard"),
    ("/admin/reports/1/dismiss", "/admin/reports/2/remove", "/admin/reports/3/dismiss"),
])
def test_expensive_actions_share_quota_across_targets(limited_app, paths):
    client = signed_in(limited_app)
    for path in paths[:2]:
        assert client.post(path).status_code != 429
    assert client.post(paths[2]).status_code == 429


def test_stripe_return_shares_setup_quota(limited_app):
    client = signed_in(limited_app)
    assert client.post("/organizer/payments/onboard").status_code == 400
    assert client.post("/organizer/payments/refresh").status_code == 400
    assert client.get("/organizer/payments/return").status_code == 429


def test_window_resets_and_expired_rows_are_removed(limited_app, monkeypatch):
    client = signed_in(limited_app)
    for _ in range(2):
        assert client.post("/profile").status_code == 400
    assert client.post("/profile").status_code == 429
    with limited_app.app_context():
        db.session.add(RateLimitBucket(key="obsolete-actor", count=1, reset_at=110))
        db.session.commit()
    monkeypatch.setattr(rate_limits, "_now", lambda: 120)
    assert client.post("/profile").status_code == 400
    with limited_app.app_context():
        rows = db.session.scalars(select(RateLimitBucket)).all()
        assert len(rows) == 1
        assert rows[0].count == 1 and rows[0].reset_at == 180


def test_day_limit_still_blocks_after_minute_window_resets(monkeypatch):
    app = build_app(type("DailyLimits", (LimitedConfig,), {"RATE_LIMITS": {"google_login": ((2, 60), (3, 86400))}}))
    client = app.test_client()
    monkeypatch.setattr(rate_limits, "_now", lambda: 100)
    assert client.get("/auth/google").status_code == 302
    assert client.get("/auth/google").status_code == 302
    monkeypatch.setattr(rate_limits, "_now", lambda: 120)
    assert client.get("/auth/google").status_code == 302
    assert client.get("/auth/google").status_code == 429


def test_webhook_callback_health_browsing_and_logout_are_not_limited(limited_app, monkeypatch):
    client = signed_in(limited_app)
    monkeypatch.setattr("app.routes.auth.fetch_google_claims", lambda: {})
    for _ in range(5):
        assert client.get("/healthz").status_code == 200
        assert client.get("/events").status_code == 200
        assert client.get("/profile").status_code == 200
        assert client.get("/auth/google/callback").status_code == 302
        assert client.post("/billing/webhook").status_code != 429
        assert client.post("/logout").status_code == 400
    with limited_app.app_context():
        assert db.session.scalar(select(db.func.count()).select_from(RateLimitBucket)) == 0


def test_anonymous_post_redirects_without_allocating_account_buckets(limited_app):
    client = limited_app.test_client()
    for _ in range(5):
        assert client.post("/organizer/events/new").status_code == 302
    with limited_app.app_context():
        assert db.session.scalar(select(db.func.count()).select_from(RateLimitBucket)) == 0


def test_anonymous_bucket_cardinality_is_bounded_and_count_saturates(limited_app, monkeypatch):
    monkeypatch.setattr(rate_limits, "ANONYMOUS_SLOTS", 2)
    client = limited_app.test_client()
    for index in range(1, 31):
        client.get("/auth/google", environ_overrides={"REMOTE_ADDR": f"192.0.2.{index}"})
    with limited_app.app_context():
        rows = db.session.scalars(select(RateLimitBucket)).all()
        assert len(rows) <= 2
        assert all(row.count <= 3 for row in rows)


def test_database_failure_fails_closed_instead_of_allowing_action(limited_app, monkeypatch):
    client = signed_in(limited_app)
    def unavailable(*args, **kwargs):
        from sqlalchemy.exc import OperationalError
        raise OperationalError("private connection", {}, Exception("database is locked"))
    monkeypatch.setattr(rate_limits, "_consume", unavailable)
    response = client.post("/profile")
    assert response.status_code == 503
    assert b"private connection" not in response.data


@pytest.mark.parametrize("invalid", [None, {"not-an-action": [(1, 1)]}, {"profile": []},
    {"profile": [(0, 60)]}, {"profile": [(1, 0)]}, {"profile": [(True, 60)]},
    {"profile": [(1, 60), (2, 60)]}, {"profile": [(1, 90000)]}])
def test_invalid_limits_fail_at_startup(invalid):
    with pytest.raises(RuntimeError, match="[Rr]ate limit|RATE_LIMITS"):
        build_app(type("BadLimits", (LimitedConfig,), {"RATE_LIMITS": invalid}))


def test_parallel_connections_never_allow_more_than_the_quota(tmp_path):
    config = type("ParallelLimits", (LimitedConfig,), {
        "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "limits.db"),
        "SQLALCHEMY_ENGINE_OPTIONS": {"connect_args": {"timeout": 10}},
    })
    app = build_app(config)
    barrier = Barrier(8)
    def attempt():
        with app.app_context():
            barrier.wait(timeout=10)
            return rate_limits._consume("account:test", ("profile",), {"profile": ((3, 60),)}, 100)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: attempt(), range(8)))
    assert results.count(0) == 3
    assert results.count(20) == 5
    with app.app_context():
        assert db.session.scalar(select(RateLimitBucket.count)) == 4
        db.session.remove()
        db.engine.dispose()


def test_counters_survive_new_app_instance(tmp_path, monkeypatch):
    config = type("PersistentLimits", (LimitedConfig,), {
        "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "limits.db"),
        "SQLALCHEMY_ENGINE_OPTIONS": {"connect_args": {"timeout": 10}},
    })
    monkeypatch.setattr(rate_limits, "_now", lambda: 100)
    first = build_app(config)
    client = first.test_client()
    assert client.get("/auth/google").status_code == 302
    assert client.get("/auth/google").status_code == 302
    second = build_app(config)
    assert second.test_client().get("/auth/google").status_code == 429


def test_postgres_parallel_limits_and_persistence(pg_config, monkeypatch):
    """Use TEST_POSTGRES_URL; production schema/data are never reused."""
    config = type("PostgresLimits", (pg_config,), {
        "RATE_LIMIT_ENABLED": True,
        "RATE_LIMITS": LimitedConfig.RATE_LIMITS,
        "GOOGLE_CLIENT_ID": "",
    })
    monkeypatch.setattr(rate_limits, "_now", lambda: 100)
    app = build_app(config)
    barrier = Barrier(8)
    def attempt():
        with app.app_context():
            barrier.wait(timeout=10)
            return rate_limits._consume("account:concurrent", ("profile",), {"profile": ((3, 60),)}, 100)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: attempt(), range(8)))
    assert results.count(0) == 3
    assert results.count(20) == 5
    client = app.test_client()
    assert client.get("/auth/google").status_code == 302
    assert client.get("/auth/google").status_code == 302
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    restarted = build_app(config)
    try:
        assert restarted.test_client().get("/auth/google").status_code == 429
    finally:
        with restarted.app_context():
            db.session.remove()
            db.engine.dispose()
