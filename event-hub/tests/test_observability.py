"""Optional alerts use real SDK serialization with a local-only transport."""

from concurrent.futures import ThreadPoolExecutor
import json

from flask import Flask
import pytest

sentry_sdk = pytest.importorskip("sentry_sdk", reason="Optional alerts require requirements-production.txt")
from sentry_sdk import Client
from sentry_sdk.transport import Transport

from app import observability


TEST_DSN = "https://0123456789abcdef0123456789abcdef@o123.ingest.us.sentry.io/456"


class RecordingTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)


@pytest.fixture
def configured_alerts(monkeypatch):
    captured = []
    scopes = (sentry_sdk.get_global_scope(), sentry_sdk.get_isolation_scope(), sentry_sdk.get_current_scope())
    original_clients = tuple(scope.client for scope in scopes)
    def isolated_client(**options):
        client = Client(**options, transport=RecordingTransport)
        captured.append(client)
        return client
    monkeypatch.setattr(sentry_sdk, "Client", isolated_client)
    app = Flask(__name__)
    app.config.update(SENTRY_DSN=TEST_DSN, APP_ENV="production")
    observability.configure_observability(app)
    assert tuple(scope.client for scope in scopes) == original_clients
    yield app, captured[0]
    captured[0].close(timeout=0)


def events(client):
    return [item.get_event() for envelope in client.transport.envelopes
            for item in envelope.items if item.headers["type"] == "event"]


def test_disabled_alerts_do_not_construct_client_or_send(monkeypatch):
    monkeypatch.setattr(sentry_sdk, "Client", lambda **kw: pytest.fail("Disabled alerts must not initialize Sentry"))
    app = Flask(__name__)
    observability.configure_observability(app)
    assert app.extensions[observability.EXTENSION_KEY] is None
    assert observability.report_failure(app, "http_500") is False


def test_sdk_has_no_auto_collection_and_a_bounded_queue(configured_alerts):
    app, client = configured_alerts
    assert client.integrations == {}
    for key in ["default_integrations", "auto_enabling_integrations", "send_default_pii",
                "include_local_variables", "attach_stacktrace", "auto_session_tracking",
                "send_client_reports", "enable_logs", "enable_metrics", "enable_backpressure_handling"]:
        assert client.options[key] is False
    assert client.options["max_request_body_size"] == "never"
    assert client.options["traces_sample_rate"] == 0.0
    assert client.options["profiles_sample_rate"] == 0.0
    assert client.options["transport_queue_size"] == 10
    assert client.options["server_name"] == ""
    assert client.options["http_proxy"] == client.options["https_proxy"] == ""
    assert client.transport.envelopes == []


def test_alert_contains_only_fixed_fields_even_inside_private_request(configured_alerts):
    app, client = configured_alerts
    with app.test_request_context("/private?secret=token", headers={"Authorization": "private-password"}):
        assert observability.report_failure(app, "database_failure") is True
    event = events(client)[0]
    assert set(event) == {"event_id", "timestamp", "platform", "level", "logger", "message", "tags", "fingerprint"}
    assert event["message"] == observability.FAILURES["database_failure"]
    assert event["tags"] == {"category": "database_failure"}
    serialized = json.dumps(event)
    assert "private" not in serialized and "token" not in serialized
    assert "request" not in event and "user" not in event and "server_name" not in event


def test_before_send_discards_every_untrusted_context_field(configured_alerts):
    app, client = configured_alerts
    client.capture_event({
        "event_id": "a" * 32, "logger": "broncoshub.operations", "tags": {"category": "http_500", "email": "private@scu.edu"},
        "message": "secret password", "request": {"url": "https://private/path", "headers": {"Cookie": "secret"}},
        "user": {"email": "private@scu.edu"}, "extra": {"key": "secret"}, "contexts": {"private": "secret"},
        "exception": {"values": [{"type": "PrivateError", "value": "secret", "stacktrace": {"frames": [{"vars": {"password": "secret"}}]}}]},
        "breadcrumbs": {"values": [{"message": "secret"}]}, "server_name": "private-machine",
        "release": "private-release", "environment": "private-environment",
    })
    event = events(client)[0]
    assert "secret" not in json.dumps(event) and "private" not in json.dumps(event)
    assert event["message"] == observability.FAILURES["http_500"]


@pytest.mark.parametrize("value", [None, {}, "secret exception text", "student@scu.edu"])
def test_unknown_alert_categories_never_reach_transport(configured_alerts, value):
    app, client = configured_alerts
    assert observability.report_failure(app, value) is False
    assert client.transport.envelopes == []


def test_unmarked_sdk_events_are_dropped(configured_alerts):
    _, client = configured_alerts
    client.capture_event({"message": "secret"})
    client.capture_event({"logger": "broncoshub.operations", "tags": {"category": "secret"}})
    assert client.transport.envelopes == []


def test_deduplication_is_per_category_and_expires(configured_alerts, monkeypatch):
    app, client = configured_alerts
    monkeypatch.setattr(observability, "_now", lambda: 100.0)
    assert observability.report_failure(app, "http_500") is True
    assert observability.report_failure(app, "http_500") is False
    assert observability.report_failure(app, "database_failure") is True
    monkeypatch.setattr(observability, "_now", lambda: 400.0)
    assert observability.report_failure(app, "http_500") is True
    assert len(events(client)) == 3


def test_parallel_failures_queue_only_one_alert(configured_alerts, monkeypatch):
    app, client = configured_alerts
    monkeypatch.setattr(observability, "_now", lambda: 100.0)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: observability.report_failure(app, "notification_worker"), range(30)))
    assert results.count(True) == 1
    assert len(events(client)) == 1


def test_reporting_failure_never_raises_or_leaks_secrets(configured_alerts, monkeypatch, caplog):
    app, client = configured_alerts
    def failed(*args, **kwargs):
        raise RuntimeError("private provider credential")
    monkeypatch.setattr(client, "capture_event", failed)
    assert observability.report_failure(app, "payment_recovery") is False
    assert "private" not in caplog.text


def test_independent_apps_do_not_share_limits_or_clients(configured_alerts):
    first, first_client = configured_alerts
    second = Flask("second")
    second.config["SENTRY_DSN"] = TEST_DSN
    observability.configure_observability(second)
    second_client = second.extensions[observability.EXTENSION_KEY].client
    try:
        assert second_client is not first_client
        assert observability.report_failure(first, "http_500") is True
        assert observability.report_failure(second, "http_500") is True
        assert len(events(first_client)) == len(events(second_client)) == 1
    finally:
        second_client.close(timeout=0)


@pytest.mark.parametrize("dsn", [None, 123, "http://key@o123.ingest.sentry.io/1",
    "https://0123456789abcdef@localhost/1", "https://0123456789abcdef@evil.example/1",
    "https://0123456789abcdef:secret@o123.ingest.sentry.io/1", TEST_DSN + "?key=secret",
    TEST_DSN + "#secret", TEST_DSN.replace("/456", "/wrong"), TEST_DSN.replace(".io/", ".io:80/"),
    TEST_DSN + "\n"])
def test_invalid_dsn_fails_without_echoing_configuration(dsn, monkeypatch):
    monkeypatch.setattr(sentry_sdk, "Client", lambda **kw: pytest.fail("Invalid DSN reached SDK"))
    app = Flask(__name__)
    app.config["SENTRY_DSN"] = dsn
    with pytest.raises(RuntimeError, match="HTTPS Sentry ingest") as failure:
        observability.configure_observability(app)
    assert "secret" not in str(failure.value) and "012345" not in str(failure.value)
