"""Transactional event notices and bounded, provider-aware email delivery.

No network is used while saving an event. A worker claims committed messages
and releases the database transaction before contacting the email provider.
"""
from datetime import timedelta
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4

from flask import current_app
import requests
from sqlalchemy import and_, or_, select, update

from app.extensions import db
from app.models import Event, Registration, User
from app.models.notification import Notification, utcnow
from app.services.event_service import campus_now

EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}\Z")
MAX_ATTEMPTS = 8
MAX_AGE = timedelta(hours=23)


def valid_email(value):
    return isinstance(value, str) and len(value) <= 254 and EMAIL.fullmatch(value) is not None


def configure_notifications(app):
    provider = app.config.get("NOTIFICATION_EMAIL_PROVIDER", "disabled")
    if provider not in {"disabled", "resend", "gmail"}:
        raise RuntimeError("NOTIFICATION_EMAIL_PROVIDER must be disabled, resend, or gmail.")
    if provider == "disabled":
        return
    if provider == "resend" and not str(app.config.get("RESEND_API_KEY", "")).startswith("re_"):
        raise RuntimeError("Set RESEND_API_KEY before enabling notification email.")
    if provider == "gmail":
        from app.services.gmail_notifications import configure_gmail
        configure_gmail(app)
    if not valid_email(app.config.get("NOTIFICATION_FROM_EMAIL")):
        raise RuntimeError("Set a verified NOTIFICATION_FROM_EMAIL address.")
    if not valid_email(app.config.get("SUPPORT_EMAIL")):
        raise RuntimeError("Set a valid SUPPORT_EMAIL reply address.")
    base = app.config.get("PUBLIC_BASE_URL", "")
    try:
        parsed = urlsplit(base)
        valid_origin = (
            isinstance(base, str) and parsed.scheme == "https"
            and parsed.hostname in (app.config.get("TRUSTED_HOSTS") or [])
            and not parsed.username and not parsed.password and parsed.port in (None, 443)
            and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
            and "\\" not in base and not any(ord(c) <= 32 for c in base)
        )
    except (ValueError, TypeError, AttributeError):
        valid_origin = False
    if not valid_origin:
        raise RuntimeError("Notification email needs the trusted public HTTPS origin.")


def event_notice_details(event):
    return {"start": event.start_time.isoformat(), "end": event.end_time.isoformat(),
            "location": event.location}


def queue_event_notices(event, kind):
    """Call under event_write, BEFORE cancelling registrations; caller commits."""
    if kind not in {"changed", "cancelled", "removed"}:
        raise ValueError("Unknown event notification")
    recipients = Registration.status.in_(("confirmed", "pending"))
    if kind == "removed":
        # A cancellation can already have closed registrations while its email
        # is still pending/ambiguous. Replace that notice with a safe removal
        # message. Do not recontact people who cancelled their own registration
        # earlier and never had an outstanding organizer-cancellation notice.
        cancellation_pending = select(Notification.id).where(
            Notification.registration_id == Registration.id,
            Notification.kind == "cancelled",
            Notification.status.in_(("pending", "sending")),
        ).exists()
        recipients = or_(recipients, and_(Registration.status == "cancelled", cancellation_pending))
    registrations = db.session.scalars(select(Registration.id).where(
        Registration.event_id == event.id, recipients,
    ))
    details = event_notice_details(event) if kind == "changed" else {}
    for registration_id in registrations:
        db.session.add(Notification(id=uuid4().hex, registration_id=registration_id,
                                    kind=kind, details=details))


def queue_registration_notice(registration, kind):
    """Save a registration-state notice inside the caller's event transaction.

    Each real state transition gets a new key. Supersede old unsent state or
    schedule notices so cancel/re-register cycles cannot revive stale mail.
    Event-wide cancellation/removal notices retain their independent meaning.
    """
    expected = {"registered": "confirmed", "unregistered": "cancelled"}
    if kind not in expected or registration.status != expected[kind]:
        raise ValueError("Invalid registration notification transition")
    db.session.flush()
    now = utcnow()
    db.session.execute(update(Notification).where(
        Notification.registration_id == registration.id,
        Notification.kind.in_(("registered", "unregistered", "changed")),
        Notification.status.in_(("pending", "sending")),
    ).values(status="skipped", failure_code="obsolete", completed_at=now,
             delivery_payload=None, details={}, lease_token=None, lease_until=None))
    db.session.add(Notification(
        id=uuid4().hex, registration_id=registration.id, kind=kind,
        details=event_notice_details(registration.event),
    ))


class DeliveryError(Exception):
    def __init__(self, code, retryable=True, retry_after=None):
        self.code, self.retryable = code, retryable
        self.retry_after = retry_after
        super().__init__(code)


def _response_json(response):
    raw = bytearray()
    for chunk in response.iter_content(4096):
        raw.extend(chunk)
        if len(raw) > 16384:
            raise DeliveryError("invalid_provider_response")
    return json.loads(raw)


def send_email(payload, message_id):
    """HTTPS works on Render Free, which blocks standard SMTP ports."""
    provider = current_app.config.get("NOTIFICATION_EMAIL_PROVIDER", "disabled")
    if provider not in {"resend", "gmail"}:
        raise DeliveryError("provider_disabled", False)
    marker = payload.get("_provider") if isinstance(payload, dict) else None
    if isinstance(payload, dict) and "_provider" in payload and marker != provider:
        raise DeliveryError("provider_changed", False)
    if provider == "gmail":
        from app.services.gmail_notifications import send_gmail
        envelope = {key: value for key, value in payload.items() if key != "_provider"} if isinstance(payload, dict) else payload
        return send_gmail(envelope, message_id)
    if (not isinstance(payload, dict) or not isinstance(payload.get("to"), list)
            or len(payload["to"]) != 1 or not valid_email(payload["to"][0])
            or not valid_email(payload.get("from")) or not valid_email(payload.get("reply_to"))):
        raise DeliveryError("invalid_recipient", False)
    try:
        with requests.Session() as client:
            client.trust_env = False
            with client.post("https://api.resend.com/emails", json=payload, headers={
                "Authorization": "Bearer " + current_app.config["RESEND_API_KEY"],
                "Idempotency-Key": "broncoshub-event-" + message_id,
            }, timeout=(5, 15), allow_redirects=False, stream=True) as response:
                if response.status_code not in {200, 201}:
                    code = response.status_code
                    if code == 409:
                        try:
                            error = _response_json(response)
                        except ValueError:
                            error = {}
                        name = error.get("name") if isinstance(error, dict) else None
                        retryable = isinstance(name, str) and name in {
                            "concurrent_idempotent_requests", "resource_locked",
                        }
                        raise DeliveryError("provider_busy" if retryable else "provider_rejected", retryable)
                    raise DeliveryError("provider_rejected", code == 429 or code >= 500)
                result = _response_json(response)
                identifier = result.get("id") if isinstance(result, dict) else None
                if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
                    raise DeliveryError("invalid_provider_response")
                return identifier
    except (requests.RequestException, ValueError):
        # Never persist/log provider responses, request payloads or credentials.
        raise DeliveryError("provider_unavailable") from None


def _payload(message):
    registration = db.session.get(Registration, message.registration_id)
    if registration is None:
        return None
    event = db.session.get(Event, registration.event_id)
    student = db.session.get(User, registration.student_id)
    if (event is None or student is None or not student.can_organize
            or not valid_email(student.email)):
        return None
    if message.kind == "changed" and (event.status != "published"
            or registration.status not in {"confirmed", "pending"}
            or event_notice_details(event) != message.details):
        return None  # Never send a stale time/location after another edit or removal.
    if message.kind == "cancelled" and event.status == "removed":
        return None  # Removal queued a replacement, including for unattempted cancellations.
    if message.kind in {"registered", "unregistered"}:
        expected = "confirmed" if message.kind == "registered" else "cancelled"
        if (registration.status != expected or event.status != "published"
                or event.end_time <= campus_now()):
            return None
        if message.kind == "registered" and not event.is_free:
            if (registration.payment is None or registration.payment.status != "succeeded"
                    or registration.ticket is None or not registration.ticket.is_valid):
                return None
        # A frozen confirmation with old schedule details must not be retried
        # after a change. The event-change notice supplies the current details.
        if message.delivery_payload and message.details != event_notice_details(event):
            return None
    if message.delivery_payload:
        # A previously attempted cancellation may contain a now-removed title.
        # Never resend it. The separate removal notice carries only safe text.
        if message.delivery_payload.get("to") != [student.email]:
            return None
        return message.delivery_payload
    link = current_app.config["PUBLIC_BASE_URL"].rstrip("/") + f"/my-registrations/{registration.id}"
    if message.kind == "removed" or event.status == "removed":
        subject = "An event you registered for was removed"
        body = "An administrator removed an event you registered for. Your registration is no longer active."
    elif message.kind == "cancelled":
        subject = "An event you registered for was cancelled"
        body = f"The organizer cancelled {event.title}. Your registration is no longer active."
    else:
        view = event.event_view()
        registered = message.kind == "registered"
        if message.kind == "unregistered":
            subject = "Your registration was cancelled"
            intro = f"You cancelled your registration for {event.title}. Your place has been released."
        elif registered:
            subject = "Your registration is confirmed"
            intro = f"Your registration for {event.title} is confirmed."
        else:
            subject = "Updated event time or location"
            intro = f"The organizer updated {event.title}."
        body = (f"{intro}\n\n"
                f"When: {view['date_label']}, {view['time_label']} (Pacific time)\n"
                f"Where: {event.location}")
        if registered and not event.is_free:
            body += f"\nTicket reference: {registration.ticket.code}"
    action = ("View in My events:" if message.kind in {"registered", "unregistered"}
              else "Check your registration and any refund status:")
    body += f"\n\n{action}\n{link}\n\nQuestions? {current_app.config['SUPPORT_EMAIL']}"
    return {"from": current_app.config["NOTIFICATION_FROM_EMAIL"], "to": [student.email],
            "reply_to": current_app.config["SUPPORT_EMAIL"], "subject": subject, "text": body}


def _cleanup(now):
    # Keep the disabled queue bounded too: unconfigured email must not collect
    # months of old notices to send later. Do not steal an active worker's lease.
    # An interrupted Gmail send cannot safely be repeated after its lease.
    # Preserve that uncertainty even if the provider is subsequently changed
    # or delivery is disabled, and clear the frozen private envelope.
    unknown = db.session.execute(update(Notification).where(
        Notification.status == "sending", Notification.attempts > 0,
        Notification.failure_code == "gmail_send_started",
        or_(Notification.lease_until.is_(None), Notification.lease_until <= now),
    ).values(status="failed", failure_code="delivery_unknown", completed_at=now,
             delivery_payload=None, details={}, lease_token=None, lease_until=None))
    expired = db.session.execute(update(Notification).where(
        Notification.status.in_(("pending", "sending")),
        Notification.created_at <= now - MAX_AGE,
        or_(Notification.lease_until.is_(None), Notification.lease_until <= now),
    ).values(status="failed", failure_code="expired", completed_at=now,
             delivery_payload=None, details={}, lease_token=None, lease_until=None))
    db.session.query(Notification).filter(
        Notification.completed_at < now - timedelta(days=30)
    ).delete(synchronize_session=False)
    db.session.commit()
    return expired.rowcount + unknown.rowcount


def deliver_pending(limit=20):
    expired = _cleanup(utcnow())
    provider = current_app.config.get("NOTIFICATION_EMAIL_PROVIDER", "disabled")
    if provider == "disabled":
        return {"sent": 0, "failed": 0, "skipped": 0, "disabled": True}
    result = {"sent": 0, "failed": expired, "skipped": 0, "disabled": False}
    for _ in range(max(0, min(int(limit), 100))):
        now, token = utcnow(), uuid4().hex
        eligible = and_(Notification.status.in_(("pending", "sending")),
                        Notification.available_at <= now,
                        or_(Notification.lease_until.is_(None), Notification.lease_until <= now))
        candidate = db.session.scalar(select(Notification.id).where(eligible)
                                      .order_by(Notification.available_at, Notification.id).limit(1))
        db.session.rollback()
        if candidate is None:
            break
        claimed = db.session.execute(update(Notification).where(Notification.id == candidate, eligible)
            .values(status="sending", lease_token=token, lease_until=now + timedelta(minutes=2)))
        db.session.commit()
        if claimed.rowcount != 1:
            continue
        message = db.session.get(Notification, candidate)
        if (message is None or message.status != "sending" or message.lease_token != token
                or message.lease_until is None or message.lease_until <= utcnow()):
            db.session.rollback()
            continue
        frozen_provider = (message.delivery_payload.get("_provider", "resend")
                           if message.delivery_payload else None)
        gmail_retry_safe = True
        if provider == "gmail" and message.attempts > 0:
            from app.services.gmail_notifications import SAFE_RETRY_CODES
            gmail_retry_safe = message.failure_code in SAFE_RETRY_CODES
        if message.delivery_payload and frozen_provider != provider:
            outcome, code, payload = "failed", "provider_changed", None
        elif not gmail_retry_safe:
            outcome, code, payload = "failed", "delivery_unknown", None
        elif now - message.created_at >= MAX_AGE or message.attempts >= MAX_ATTEMPTS:
            outcome, code, payload = "failed", "expired", None
        else:
            payload = _payload(message)
            outcome, code = "skipped", "obsolete"
        first_attempt = message.first_attempt_at or now
        attempt = message.attempts + (1 if payload else 0)
        details = message.details
        # A worker can pause after its claim and lose the lease. Freeze the
        # envelope only while still owning it; never restore private payloads
        # onto a row another worker has already completed and cleared.
        if payload:
            prepared_at = utcnow()
            if provider == "gmail":
                payload = {**payload, "_provider": "gmail"}
            if message.kind in {"registered", "unregistered"} and not message.delivery_payload:
                # First delivery uses the current schedule. Save that snapshot
                # alongside the immutable envelope for later retry checks.
                registration = db.session.get(Registration, message.registration_id)
                details = event_notice_details(registration.event)
            values = dict(first_attempt_at=first_attempt, attempts=attempt, delivery_payload=payload,
                          details=details, lease_until=prepared_at + timedelta(minutes=2))
            if provider == "gmail":
                values["failure_code"] = "gmail_send_started"
            prepared = db.session.execute(update(Notification).where(
                Notification.id == candidate, Notification.status == "sending",
                Notification.lease_token == token, Notification.lease_until > prepared_at,
            ).values(**values))
            if prepared.rowcount != 1:
                db.session.rollback()
                continue
        # No open transaction/DB lock while contacting the provider.
        db.session.commit()
        provider_id = None
        retry_after = min(3600, 60 * 2 ** min(attempt, 6))
        if payload:
            try:
                provider_id = send_email(payload, candidate)
                outcome, code = "sent", None
            except DeliveryError as error:
                retryable, code = error.retryable, error.code
                if provider == "gmail":
                    from app.services.gmail_notifications import SAFE_RETRY_CODES
                    if retryable and code not in SAFE_RETRY_CODES:
                        retryable, code = False, "delivery_unknown"
                outcome = "pending" if retryable and attempt < MAX_ATTEMPTS else "failed"
                if error.retry_after is not None:
                    retry_after = max(60, min(21600, int(error.retry_after)))
        completed = utcnow()
        completion = db.session.execute(update(Notification).where(
            Notification.id == candidate, Notification.status == "sending",
            Notification.lease_token == token,
        ).values(status=outcome, failure_code=code, provider_id=provider_id,
                 lease_token=None, lease_until=None,
                 available_at=completed + timedelta(seconds=retry_after),
                 completed_at=completed if outcome != "pending" else None,
                 delivery_payload=payload if outcome == "pending" else None,
                 details=details if outcome == "pending" else {}))
        db.session.commit()
        if completion.rowcount and outcome in result:
            result[outcome] += 1
    return result
