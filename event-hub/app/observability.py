"""Optional operational alerts containing fixed failure codes, never user data.

This does not install Sentry's Flask, logging, exception, or tracing hooks.
Callers explicitly report a known category. A separate client per app avoids
sharing configuration or request state with another app in the same process.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from threading import Lock
import time
from urllib.parse import urlsplit
from uuid import uuid4


FAILURES = {
    "http_500": "BroncosHub could not complete a request.",
    "database_failure": "BroncosHub could not access its database.",
    "payment_recovery": "BroncosHub payment recovery needs attention.",
    "notification_worker": "BroncosHub event notification processing needs attention.",
    "notification_failed": "BroncosHub has an event notification that could not be delivered.",
    "refund_pending": "BroncosHub has refunds that need operator attention.",
}
ALERT_INTERVAL_SECONDS = 300
EXTENSION_KEY = "broncoshub_observability"


@dataclass
class AlertState:
    client: object
    last_report: dict = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)


def _now():
    return time.monotonic()


def _validated_dsn(value):
    if not isinstance(value, str):
        raise RuntimeError("SENTRY_DSN must be an HTTPS Sentry ingest address or empty.")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        valid = (
            len(value) <= 512 and parsed.scheme == "https"
            and re.fullmatch(r"(?:[a-z0-9-]+\.)?ingest(?:\.[a-z]{2})?\.sentry\.io", parsed.hostname or "")
            and re.fullmatch(r"[a-fA-F0-9]{16,64}", parsed.username or "")
            and parsed.password is None and parsed.port in (None, 443)
            and re.fullmatch(r"/[0-9]{1,20}", parsed.path)
            and not parsed.query and not parsed.fragment and "\\" not in value
            and not any(char.isspace() or ord(char) < 32 for char in value)
        )
    except ValueError:
        valid = False
    if not valid:
        # Never echo the supplied DSN, token, URL, or other configuration.
        raise RuntimeError("SENTRY_DSN must be an HTTPS Sentry ingest address or empty.")
    return value


def _before_send(event, hint):
    """Rebuild from the allowlist after the SDK applies all other event data."""
    if not isinstance(event, dict) or event.get("type") is not None:
        return None
    tags = event.get("tags")
    category = tags.get("category") if isinstance(tags, dict) else None
    if (not isinstance(category, str) or category not in FAILURES
            or event.get("logger") != "broncoshub.operations"):
        return None
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not re.fullmatch(r"[a-f0-9]{32}", event_id):
        return None
    return {
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": "python",
        "level": "error",
        "logger": "broncoshub.operations",
        "message": FAILURES[category],
        "tags": {"category": category},
        "fingerprint": ["broncoshub", category],
    }


def configure_observability(app):
    """Configure once. An empty DSN disables alerts without loading the SDK."""
    if EXTENSION_KEY in app.extensions:
        return
    dsn = _validated_dsn(app.config.get("SENTRY_DSN", ""))
    if not dsn:
        app.extensions[EXTENSION_KEY] = None
        return
    from sentry_sdk import Client
    client = Client(
        dsn=dsn,
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[],
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        attach_stacktrace=False,
        max_breadcrumbs=0,
        traces_sample_rate=0.0,
        profiles_sample_rate=0.0,
        profile_session_sample_rate=0.0,
        auto_session_tracking=False,
        send_client_reports=False,
        enable_logs=False,
        enable_metrics=False,
        enable_backpressure_handling=False,
        before_send=_before_send,
        transport_queue_size=10,
        shutdown_timeout=0,
        http_proxy="",
        https_proxy="",
        server_name="",
        release="",
        dist="",
        environment="production" if app.config.get("APP_ENV") == "production" else "development",
        spotlight=False,
        debug=False,
    )
    app.extensions[EXTENSION_KEY] = AlertState(client=client)


def report_failure(app, category):
    """Queue at most one alert per category per five minutes per process.

    True means queued, not delivered. SDK transport runs asynchronously;
    reporting never flushes or waits for the provider. Do not pass exceptions
    or user values.
    """
    if not isinstance(category, str) or category not in FAILURES:
        return False
    state = app.extensions.get(EXTENSION_KEY)
    if state is None:
        return False
    now = _now()
    with state.lock:
        last = state.last_report.get(category)
        if last is not None and now - last < ALERT_INTERVAL_SECONDS:
            return False
        state.last_report[category] = now
    try:
        return state.client.capture_event({
            "event_id": uuid4().hex,
            "logger": "broncoshub.operations",
            "tags": {"category": category},
        }, scope=None) is not None
    except Exception:
        # An alerting failure must not replace the original app error or expose
        # provider connection settings in logs. The next interval can retry.
        return False
