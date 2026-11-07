"""Shared limits on costly actions; ordinary browsing stays unaffected.

Counters use atomic database upserts, not a worker-local dictionary. A limit is
per account across event IDs and browser sessions. Login starts use an HMAC of
the trusted client address, grouped into a bounded set of slots. Very rarely,
two unrelated addresses can share a slot; that is preferable to letting an
attacker create unlimited database rows using changing addresses.
"""

import hashlib
import hmac
import ipaddress
import time

from flask import current_app, request
from flask_login import current_user
from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from werkzeug.exceptions import TooManyRequests

from app.extensions import db
from app.models.rate_limit import RateLimitBucket


# Each pair is (attempts, seconds). Fixed windows can allow two adjacent bursts;
# longer windows also bound sustained activity. These are small-campus defaults.
DEFAULT_LIMITS = {
    "google_login": ((60, 60), (1000, 3600)),
    "event_write": ((20, 60), (120, 3600)),
    "event_create": ((20, 86400),),
    "registration": ((20, 60), (120, 3600)),
    "checkout": ((10, 60), (60, 3600)),
    "report": ((10, 600), (30, 86400)),
    "profile": ((10, 600),),
    "payment_setup": ((5, 300), (30, 3600)),
    "moderation": ((30, 60), (300, 3600)),
}

POST_POLICIES = {
    "organizer.create_event": ("event_write", "event_create"),
    "organizer.edit_event": ("event_write",),
    "organizer.cancel_event": ("event_write",),
    "registrations.register": ("registration",),
    "registrations.cancel": ("registration",),
    "registrations.checkout": ("checkout",),
    "reporting.report_event": ("report",),
    "auth.profile": ("profile",),
    "payments.onboard": ("payment_setup",),
    "payments.refresh": ("payment_setup",),
    "admin.dismiss_report": ("moderation",),
    "admin.remove_report": ("moderation",),
}

# Two login windows therefore need at most 32,768 live address buckets. Account
# buckets are limited to the fixed policies above times actual signed-in users.
ANONYMOUS_SLOTS = 16384
CLEANUP_BATCH = 256


def _now():
    return int(time.time())


def _limits(app):
    configured = app.config.get("RATE_LIMITS", {})
    if not isinstance(configured, dict) or set(configured) - set(DEFAULT_LIMITS):
        raise RuntimeError("RATE_LIMITS must contain only known action names.")
    limits = dict(DEFAULT_LIMITS)
    limits.update(configured)
    for rules in limits.values():
        if not isinstance(rules, (list, tuple)) or not 1 <= len(rules) <= 3:
            raise RuntimeError("Each rate limit needs one to three (attempts, seconds) pairs.")
        periods = set()
        for rule in rules:
            if (not isinstance(rule, (list, tuple)) or len(rule) != 2
                    or any(type(value) is not int for value in rule)
                    or not 1 <= rule[0] <= 100000 or not 1 <= rule[1] <= 86400
                    or rule[1] in periods):
                raise RuntimeError("Rate limits need positive attempts and unique windows up to one day.")
            periods.add(rule[1])
    return limits


def _digest(value):
    secret = current_app.config["SECRET_KEY"]
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _address_subject():
    # Do not read X-Forwarded-For here. ProxyFix is enabled only for the configured
    # trusted proxy, and otherwise request.remote_addr ignores spoofed headers.
    try:
        address = ipaddress.ip_address(request.remote_addr or "")
        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped:
                address = address.ipv4_mapped
            else:
                address = ipaddress.ip_network(f"{address}/64", strict=False).network_address
        normalized = str(address)
    except ValueError:
        normalized = "unknown"
    slot = int(_digest("address:" + normalized), 16) % ANONYMOUS_SLOTS
    return "address:" + str(slot)


def _request_limits():
    if request.endpoint == "auth.google_login" and request.method == "GET":
        return _address_subject(), ("google_login",)
    # Webhooks, the OAuth callback, health checks, log out and ordinary page
    # reads are deliberately absent. Stripe's return route does call its API.
    policies = POST_POLICIES.get(request.endpoint, ()) if request.method == "POST" else ()
    if request.method == "GET" and request.endpoint == "payments.onboarding_return":
        policies = ("payment_setup",)
    if not policies or not current_user.is_authenticated:
        return None, ()
    return "account:" + _digest("account:" + current_user.get_id()), policies


def _consume(subject, policies, limits, now):
    """Commit only counters, using a separate transaction from route writes."""
    table = RateLimitBucket.__table__
    dialect = db.engine.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise RuntimeError("Rate limits require SQLite or PostgreSQL.")
    insert = postgres_insert if dialect == "postgresql" else sqlite_insert
    retry_after = 0
    with db.engine.begin() as connection:
        # Expired rows cannot accumulate indefinitely, even without a worker.
        expired = select(table.c.key).where(table.c.reset_at <= now).order_by(
            table.c.reset_at, table.c.key,
        ).limit(CLEANUP_BATCH)
        connection.execute(delete(table).where(table.c.key.in_(expired)))
        # Stable ordering avoids opposing row-lock orders for shared policies.
        for name in sorted(policies):
            for maximum, period in sorted(limits[name], key=lambda rule: rule[1]):
                reset_at = (now // period + 1) * period
                statement = insert(table).values(
                    key=f"{name}:{period}:{subject}", count=1, reset_at=reset_at,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[table.c.key],
                    set_={
                        "count": case(
                            (table.c.reset_at <= now, 1),
                            (table.c.count < maximum + 1, table.c.count + 1),
                            else_=table.c.count,
                        ),
                        "reset_at": case((table.c.reset_at <= now, reset_at),
                                         else_=table.c.reset_at),
                    },
                ).returning(table.c.count, table.c.reset_at)
                count, deadline = connection.execute(statement).one()
                if count > maximum:
                    retry_after = max(retry_after, deadline - now)
    return retry_after


def register_rate_limits(app):
    """Register before database initialization so the counter table is created."""
    if "broncoshub_rate_limits" in app.extensions:
        return
    limits = _limits(app)
    app.extensions["broncoshub_rate_limits"] = limits

    @app.before_request
    def limit_action_requests():
        if not app.config.get("RATE_LIMIT_ENABLED", True):
            return
        subject, policies = _request_limits()
        if not policies:
            return
        retry_after = _consume(subject, policies, limits, _now())
        if retry_after:
            # The shared error handler preserves headers from HTTPException.
            raise TooManyRequests(retry_after=max(1, retry_after))
