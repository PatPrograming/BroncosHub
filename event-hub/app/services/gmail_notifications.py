"""Send-only Gmail API transport; ambiguous sends are never retried.

Google's send API accepts RFC 2822 MIME encoded as base64url. Unlike Resend,
it has no idempotency-key contract; Message-ID is only a tracing reference.
"""
import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, parsedate_to_datetime
import json
import re

from flask import current_app
import requests

from app.services.notification_service import DeliveryError, valid_email

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
MAX_RESPONSE_BYTES = 16384
MAX_RETRY_SECONDS = 21600
SAFE_RETRY_CODES = frozenset({
    "gmail_token_unavailable", "gmail_auth_rejected", "gmail_rate_limited", "gmail_quota_exceeded",
})


def _secret(value, limit):
    return (isinstance(value, str) and 0 < len(value) <= limit
            and all(33 <= ord(character) <= 126 for character in value))


def configure_gmail(app):
    sender = app.config.get("NOTIFICATION_FROM_EMAIL")
    if (not isinstance(sender, str)
            or not re.fullmatch(r"[A-Za-z0-9.]{1,64}@gmail\.com", sender)):
        raise RuntimeError("Gmail notifications require the dedicated @gmail.com sender address.")
    client_id = app.config.get("GMAIL_CLIENT_ID")
    if (not isinstance(client_id, str) or len(client_id) > 256
            or not re.fullmatch(r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", client_id)):
        raise RuntimeError("Set GMAIL_CLIENT_ID before enabling Gmail notifications.")
    for key, limit in (("GMAIL_CLIENT_SECRET", 2048), ("GMAIL_REFRESH_TOKEN", 4096)):
        if not _secret(app.config.get(key), limit):
            raise RuntimeError(f"Set {key} before enabling Gmail notifications.")


def _read_json(response):
    raw = bytearray()
    for chunk in response.iter_content(4096):
        raw.extend(chunk)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Response too large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Expected object")
    return result


def _retry_after(response, default):
    value = response.headers.get("Retry-After", "")
    seconds = default
    if isinstance(value, str) and len(value) <= 128:
        try:
            if value.isascii() and value.isdigit():
                seconds = int(value)
            else:
                when = parsedate_to_datetime(value)
                if when.tzinfo is not None:
                    seconds = int((when - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            pass
    return max(60, min(MAX_RETRY_SECONDS, seconds))


def _access_token(client):
    try:
        with client.post(TOKEN_URL, data={
            "client_id": current_app.config["GMAIL_CLIENT_ID"],
            "client_secret": current_app.config["GMAIL_CLIENT_SECRET"],
            "refresh_token": current_app.config["GMAIL_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        }, timeout=(5, 15), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                if response.status_code == 429 or response.status_code >= 500:
                    raise DeliveryError("gmail_token_unavailable", True, _retry_after(response, 300))
                raise DeliveryError("gmail_auth_required", False)
            result = _read_json(response)
            token = result.get("access_token")
            if (not _secret(token, 4096) or result.get("token_type", "").lower() != "bearer"):
                raise DeliveryError("gmail_auth_required", False)
            # OAuth may omit unchanged scopes on refresh. If returned, require
            # exactly send-only permission; never use an inbox-reading token.
            scope = result.get("scope")
            if scope is not None and (not isinstance(scope, str) or set(scope.split()) != {GMAIL_SEND_SCOPE}):
                raise DeliveryError("gmail_scope_invalid", False)
            return token
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        # No send happened, so retrying a token exchange cannot duplicate mail.
        raise DeliveryError("gmail_token_unavailable", True, 300) from None


def _mime(payload, message_id):
    if (not isinstance(payload, dict) or not isinstance(payload.get("to"), list)
            or len(payload["to"]) != 1 or not valid_email(payload["to"][0])
            or not valid_email(payload.get("reply_to"))
            or payload.get("from") != current_app.config["NOTIFICATION_FROM_EMAIL"]
            or not valid_email(payload.get("from"))
            or not isinstance(message_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", message_id)):
        raise DeliveryError("invalid_recipient", False)
    subject, body = payload.get("subject"), payload.get("text")
    if (not isinstance(subject, str) or not 0 < len(subject) <= 256
            or any(ord(character) < 32 or ord(character) == 127 for character in subject)
            or not isinstance(body, str) or len(body.encode("utf-8")) > 65536):
        raise DeliveryError("invalid_message", False)
    message = EmailMessage(policy=SMTP)
    message["From"], message["To"] = payload["from"], payload["to"][0]
    message["Reply-To"], message["Subject"] = payload["reply_to"], subject
    message["Message-ID"] = f"<broncoshub.{message_id}@gmail.com>"
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _rejection(response):
    status = response.status_code
    if status == 401:
        return DeliveryError("gmail_auth_rejected", True, 300)
    if status == 429:
        return DeliveryError("gmail_rate_limited", True, _retry_after(response, 3600))
    if status == 403:
        try:
            result = _read_json(response)
            errors = result.get("error", {}).get("errors", [])
            reasons = {item.get("reason") for item in errors if isinstance(item, dict)
                       and isinstance(item.get("reason"), str)}
        except (ValueError, TypeError, AttributeError, requests.RequestException):
            reasons = set()
        if "dailyLimitExceeded" in reasons:
            return DeliveryError("gmail_quota_exceeded", True, _retry_after(response, MAX_RETRY_SECONDS))
        if reasons & {"rateLimitExceeded", "userRateLimitExceeded"}:
            return DeliveryError("gmail_rate_limited", True, _retry_after(response, 300))
        return DeliveryError("gmail_permission_denied", False)
    if 400 <= status < 500:
        return DeliveryError("gmail_message_rejected", False)
    # A server error, redirect, or unexpected success is not proof of rejection.
    return DeliveryError("delivery_unknown", False)


def send_gmail(payload, message_id):
    raw = _mime(payload, message_id)
    with requests.Session() as client:
        client.trust_env = False
        access_token = _access_token(client)
        try:
            with client.post(SEND_URL, json={"raw": raw}, headers={
                "Authorization": "Bearer " + access_token,
            }, timeout=(5, 15), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise _rejection(response)
                result = _read_json(response)
                identifier = result.get("id")
                if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
                    raise DeliveryError("delivery_unknown", False)
                return identifier
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            # Gmail has no idempotent send. Even a read timeout may follow a
            # successful send; automatic retries could duplicate the message.
            raise DeliveryError("delivery_unknown", False) from None
