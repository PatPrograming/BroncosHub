"""Real PostgreSQL checks; opt in with TEST_POSTGRES_URL to a test database.

Each case creates and removes only a uniquely named test_broncoshub_* schema.
Never point this at a database where the test role cannot create/drop its own
temporary schemas. Production tables are not cleared or reused.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
from threading import Event as Signal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import Event, Registration, Report, User
from app.routes import reporting as reporting_routes
from app.services.moderation_service import resolve_report
from config import TestConfig
import test_concurrency as concurrency
import test_event_flow as flows


@pytest.fixture
def pg_config(tmp_path):
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL to run real PostgreSQL integration tests.")
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("TEST_POSTGRES_URL must identify a PostgreSQL test database.")
    url = parsed.set(drivername="postgresql+psycopg")
    schema = "test_broncoshub_" + uuid4().hex
    admin = create_engine(url, connect_args={"prepare_threshold": None})
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    class PostgresConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = url
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
            "connect_args": {"prepare_threshold": None,
                             "options": f"-csearch_path={schema} -clock_timeout=5000"},
        }
        DATABASE_SCHEMA = schema
        COVER_UPLOAD_DIR = str(tmp_path / "covers")
        COVER_STORAGE_BACKEND = "local"

    try:
        yield PostgresConfig
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def pg_app(pg_config):
    app = create_app(pg_config)
    with app.app_context():
        db.session.add_all([
            User(name=f"Concurrent {index}", email=f"concurrent-{index}@scu.edu",
                 google_sub=f"concurrent-{index}", role=role)
            for index, role in enumerate(("organizer", "student", "student", "admin"), 1)
        ])
        db.session.commit()
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


@pytest.mark.parametrize("scenario", [
    concurrency.test_different_students_compete_for_one_free_seat_with_stale_cached_counts,
    concurrency.test_simultaneous_duplicate_signup_reuses_one_registration,
    concurrency.test_organizer_cancellation_racing_signup_closes_every_registration,
    concurrency.test_capacity_reduction_racing_signup_never_goes_below_confirmed_count,
    concurrency.test_cancel_and_moderation_remove_cannot_resurrect_a_removed_event,
], ids=lambda scenario: scenario.__name__.removeprefix("test_"))
def test_postgres_registration_and_moderation_races(pg_app, scenario):
    scenario(pg_app)


@pytest.mark.parametrize("paid", [False, True])
def test_postgres_removal_racing_signup_or_purchase(pg_app, paid):
    concurrency.test_removal_racing_signup_or_checkout_leaves_no_active_registration(pg_app, paid)


@pytest.mark.parametrize("scenario", [
    concurrency.test_two_paid_checkouts_compete_for_last_seat_before_any_second_charge,
    concurrency.test_two_submissions_of_same_checkout_charge_once,
    concurrency.test_edit_loaded_before_moderation_refreshes_state_before_saving,
    concurrency.test_failed_purchase_releases_lock_without_partial_payment_and_can_retry,
], ids=lambda scenario: scenario.__name__.removeprefix("test_"))
def test_postgres_competing_edits_and_purchases(pg_app, scenario, monkeypatch):
    scenario(pg_app, monkeypatch)


@pytest.mark.parametrize("scenario", [
    flows.test_free_event_lifecycle_updates_student_pages_and_organizer_counts,
    flows.test_paid_registration_checkout_issues_one_ticket_and_updates_counts,
    flows.test_reported_event_removal_closes_the_existing_registration_flow,
], ids=lambda scenario: scenario.__name__.removeprefix("test_"))
def test_postgres_http_event_flows(pg_app, scenario):
    with pg_app.app_context():
        db.session.get(User, 2).year_level = "third_year"
        db.session.commit()
    ids = {"organizer": 1, "student": 2, "admin": 4}
    clients = {}
    for role, user_id in ids.items():
        client = pg_app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        clients[role] = client
    # These scenarios read the real rendered forms and their CSRF fields.
    # Google identity and the payment provider remain isolated test boundaries.
    scenario(pg_app, (clients, ids))


def test_postgres_app_restart_preserves_records_times_and_privacy(pg_app, pg_config):
    event_id, report_id = concurrency.seed(pg_app)
    with pg_app.app_context():
        report = db.session.get(Report, report_id)
        report.created_at = datetime(2026, 10, 30, 2, 15)
        db.session.commit()
        db.session.remove()
        db.engine.dispose()

    restarted = create_app(pg_config)
    try:
        with restarted.app_context():
            assert db.session.get(Event, event_id).start_time == datetime(2050, 1, 1, 12)
            report = db.session.get(Report, report_id)
            assert report.created_at == datetime(2026, 10, 30, 2, 15)
            assert report.report_view()["date_label"] == "Oct 29, 2026 · 19:15 PDT"
            tables = db.session.execute(text(
                "SELECT c.relname, c.relrowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND c.relkind = 'r'"
            ), {"schema": pg_config.DATABASE_SCHEMA}).all()
            assert len(tables) == 8
            assert all(enabled for _, enabled in tables)
            assert db.session.scalar(text("SELECT current_schema()")) == pg_config.DATABASE_SCHEMA
        assert restarted.test_client().get("/healthz").json == {"status": "ok"}
        for route in ("/", "/events", f"/events/{event_id}"):
            assert restarted.test_client().get(route).status_code == 200
    finally:
        with restarted.app_context():
            db.session.remove()
            db.engine.dispose()


def test_postgres_rejects_misconfigured_schema_before_creating_tables(pg_config):
    for schema in ("public", "pg_catalog", "information_schema", "unsafe; DROP SCHEMA public"):
        config = type("UnsafeSchemaConfig", (pg_config,), {"DATABASE_SCHEMA": schema})
        with pytest.raises(RuntimeError, match="private DATABASE_SCHEMA"):
            create_app(config)
    config = type("WrongSchemaConfig", (pg_config,), {"DATABASE_SCHEMA": "missing_broncoshub"})
    with pytest.raises(RuntimeError, match="search_path"):
        create_app(config)


def test_postgres_foreign_keys_and_duplicate_registration_constraints(pg_app):
    event_id, _ = concurrency.seed(pg_app)
    with pg_app.app_context():
        db.session.add(Registration(student_id=999999, event_id=event_id, status="confirmed"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        db.session.add(Registration(student_id=2, event_id=event_id, status="confirmed"))
        db.session.commit()
        db.session.add(Registration(student_id=2, event_id=event_id, status="confirmed"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert db.session.scalar(db.select(db.func.count()).select_from(Registration)) == 1


def test_postgres_lock_timeout_returns_retryable_response_and_can_retry(pg_app, pg_config):
    event_id, _ = concurrency.seed(pg_app)
    options = {**pg_config.SQLALCHEMY_ENGINE_OPTIONS,
               "connect_args": {"prepare_threshold": None, "options":
                   f"-csearch_path={pg_config.DATABASE_SCHEMA} -clock_timeout=50"}}
    config = type("FastTimeoutConfig", (pg_config,), {"SQLALCHEMY_ENGINE_OPTIONS": options})
    waiting_app = create_app(config)
    client = concurrency.browser(waiting_app, 3)
    try:
        with pg_app.app_context(), db.engine.begin() as blocker:
            blocker.execute(text("SELECT id FROM events WHERE id = :id FOR UPDATE"), {"id": event_id})
            response = client.post(f"/events/{event_id}/register", data={"csrf_token": "race-token"})
            assert response.status_code == 503
            assert b"Check My Events" in response.data
        response = client.post(f"/events/{event_id}/register", data={"csrf_token": "race-token"})
        assert response.status_code == 302
        with pg_app.app_context():
            assert db.session.get(Event, event_id).active_registration_count() == 1
    finally:
        with waiting_app.app_context():
            db.session.remove()
            db.engine.dispose()


def test_simultaneous_postgres_startup_creates_one_complete_schema(pg_config):
    with ThreadPoolExecutor(max_workers=2) as workers:
        apps = list(workers.map(lambda _: create_app(pg_config), range(2)))
    try:
        for app in apps:
            assert app.test_client().get("/healthz").status_code == 200
            with app.app_context():
                assert db.session.scalar(text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = :schema"
                ), {"schema": pg_config.DATABASE_SCHEMA}) == 8
    finally:
        for app in apps:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()


def test_report_loaded_before_removal_cannot_be_inserted_after_removal(pg_app, monkeypatch):
    event_id, report_id = concurrency.seed(pg_app)
    ready, resume = Signal(), Signal()
    original = reporting_routes.event_write

    def delayed_lock(event):
        ready.set()
        assert resume.wait(timeout=10)
        return original(event)

    monkeypatch.setattr(reporting_routes, "event_write", delayed_lock)
    client = concurrency.browser(pg_app, 3)
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(client.post, f"/events/{event_id}/report", data={
            "csrf_token": "race-token", "reason": "spam", "details": "Please review this event."
        })
        assert ready.wait(timeout=5)
        try:
            with pg_app.app_context():
                assert resolve_report(db.session.get(Report, report_id), db.session.get(User, 4),
                                      "remove", "Removed while a new report was loading.")
        finally:
            resume.set()
        assert pending.result(timeout=10).status_code == 409
    with pg_app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Report).where(
            Report.event_id == event_id, Report.status == "pending"
        )) == 0

