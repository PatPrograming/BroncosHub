"""Local-only Gmail transport and durable no-duplicate retry policy checks."""
import base64
from copy import deepcopy
from datetime import timedelta
from email import policy
from email.parser import BytesParser
import json

import pytest
import requests

from app.extensions import db
from app.models.notification import Notification, utcnow
from app.services import gmail_notifications as gmail
from app.services import notification_service as notices
import test_notifications as cases


@pytest.fixture(autouse=True)
def no_external_requests(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *args, **kwargs:
                        pytest.fail("Gmail tests must not contact external services"))


def configure(app):
    app.config.update(
        NOTIFICATION_EMAIL_PROVIDER="gmail", NOTIFICATION_FROM_EMAIL="broncoshub.test@gmail.com",
        GMAIL_CLIENT_ID="123-local.apps.googleusercontent.com", GMAIL_CLIENT_SECRET="local-client-secret",
        GMAIL_REFRESH_TOKEN="local-refresh-token", SUPPORT_EMAIL="ecan@scu.edu",
        PUBLIC_BASE_URL="https://broncoshub.example.com", TRUSTED_HOSTS=["broncoshub.example.com"],
    )


@pytest.fixture
def gmail_case(app):
    configure(app)
    return cases.seed(app)


def envelope():
    return dict(cases.envelope(), **{"from": "broncoshub.test@gmail.com", "subject": "Campus update — SCU"})


class Response:
    def __init__(self, status=200, body=None, headers=None, read_error=None):
        self.status_code = status
        self.body = json.dumps(body if body is not None else {"id": "gmail-123"}).encode() if not isinstance(body, bytes) else body
        self.headers = headers or {}
        self.read_error = read_error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        if self.read_error:
            raise self.read_error
        for offset in range(0, len(self.body), size):
            yield self.body[offset:offset + size]


def token(**updates):
    body = {"access_token": "local-access-token", "token_type": "Bearer", "scope": gmail.GMAIL_SEND_SCOPE}
    return Response(body={**body, **updates})


def transport(monkeypatch, responses, before_request=None):
    pending, calls = list(responses), []
    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            assert self.trust_env is False
            assert kwargs["timeout"] == (5, 15) and kwargs["allow_redirects"] is False and kwargs["stream"] is True
            assert url in {gmail.TOKEN_URL, gmail.SEND_URL}
            if before_request:
                before_request(url, kwargs)
            calls.append((url, deepcopy(kwargs)))
            assert pending, "Unexpected extra HTTP attempt"
            response = pending.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
    monkeypatch.setattr(gmail.requests, "Session", Session)
    return calls


def due(identifier):
    message = db.session.get(Notification, identifier)
    message.available_at = utcnow() - timedelta(seconds=1)
    db.session.commit()


def test_send_uses_only_official_https_send_api_and_safe_mime(app, monkeypatch):
    configure(app)
    notices.configure_notifications(app)
    calls = transport(monkeypatch, [token(), Response()])
    with app.app_context():
        assert notices.send_email(envelope(), "notice-123") == "gmail-123"
    auth, send = calls
    assert auth[0] == gmail.TOKEN_URL
    assert auth[1]["data"] == {"client_id": app.config["GMAIL_CLIENT_ID"],
                                "client_secret": "local-client-secret", "refresh_token": "local-refresh-token",
                                "grant_type": "refresh_token"}
    assert send[0] == gmail.SEND_URL and "?" not in send[0]
    assert send[1]["headers"] == {"Authorization": "Bearer local-access-token"}
    assert set(send[1]["json"]) == {"raw"}
    parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(send[1]["json"]["raw"]))
    assert str(parsed["From"]) == "broncoshub.test@gmail.com"
    assert str(parsed["To"]) == "recipient@scu.edu"
    assert str(parsed["Reply-To"]) == "ecan@scu.edu"
    assert str(parsed["Subject"]) == "Campus update — SCU"
    assert parsed["Message-ID"] == "<broncoshub.notice-123@gmail.com>"
    assert parsed["Bcc"] is None and parsed["Cc"] is None
    assert parsed.get_content().strip() == "Details"


@pytest.mark.parametrize("key,value", [
    ("NOTIFICATION_FROM_EMAIL", "events@example.com"), ("NOTIFICATION_FROM_EMAIL", "name+alias@gmail.com"),
    ("NOTIFICATION_FROM_EMAIL", "sender@gmail.com\r\nBcc:other@example.com"),
    ("GMAIL_CLIENT_ID", "https://evil.example/token"), ("GMAIL_CLIENT_ID", ""),
    ("GMAIL_CLIENT_SECRET", ""), ("GMAIL_CLIENT_SECRET", "secret\n"),
    ("GMAIL_REFRESH_TOKEN", ""), ("GMAIL_REFRESH_TOKEN", "token\r\n"),
    ("GMAIL_REFRESH_TOKEN", "x" * 4097), ("SUPPORT_EMAIL", "invalid"),
    ("PUBLIC_BASE_URL", "http://broncoshub.example.com"),
])
def test_invalid_gmail_configuration_fails_before_delivery(app, key, value):
    configure(app)
    app.config[key] = value
    with pytest.raises(RuntimeError):
        notices.configure_notifications(app)


@pytest.mark.parametrize("key,value", [
    ("from", "other@gmail.com"), ("from", "sender@gmail.com\nBcc:other@example.com"),
    ("to", ["recipient@scu.edu\r\nBcc:bad@example.com"]), ("to", ["one@scu.edu", "two@scu.edu"]),
    ("reply_to", "ecan@scu.edu\nBcc:bad@example.com"),
    ("subject", "Subject\r\nBcc:bad@example.com"), ("subject", "Bad\0subject"),
    ("text", "x" * 65537),
])
def test_mime_header_injection_and_bad_envelopes_never_call_provider(app, monkeypatch, key, value):
    configure(app)
    calls = transport(monkeypatch, [])
    payload = {**envelope(), key: value}
    with app.app_context(), pytest.raises(notices.DeliveryError) as failure:
        notices.send_email(payload, "notice-123")
    assert failure.value.retryable is False and calls == []


def test_message_id_injection_is_rejected(app, monkeypatch):
    configure(app)
    calls = transport(monkeypatch, [])
    with app.app_context(), pytest.raises(notices.DeliveryError):
        gmail.send_gmail(envelope(), "id\r\nBcc:bad@example.com")
    assert calls == []


@pytest.mark.parametrize("scope", ["https://mail.google.com/", gmail.GMAIL_SEND_SCOPE + " https://www.googleapis.com/auth/gmail.readonly", "", []])
def test_broader_or_missing_send_scope_is_rejected_without_sending(app, monkeypatch, scope):
    configure(app)
    calls = transport(monkeypatch, [token(scope=scope)])
    with app.app_context(), pytest.raises(notices.DeliveryError) as failure:
        notices.send_email(envelope(), "notice-123")
    assert failure.value.code == "gmail_scope_invalid" and len(calls) == 1


@pytest.mark.parametrize("response,code,retryable", [
    (Response(400, {"error": "invalid_grant", "error_description": "private token details"}), "gmail_auth_required", False),
    (Response(302), "gmail_auth_required", False),
    (Response(429), "gmail_token_unavailable", True),
    (Response(503), "gmail_token_unavailable", True),
    (requests.ReadTimeout("private token details"), "gmail_token_unavailable", True),
    (Response(body=b"x" * 16385), "gmail_token_unavailable", True),
])
def test_token_failures_never_send_or_leak_secrets(app, monkeypatch, response, code, retryable, caplog):
    configure(app)
    calls = transport(monkeypatch, [response])
    with app.app_context(), pytest.raises(notices.DeliveryError) as failure:
        notices.send_email(envelope(), "notice-123")
    assert failure.value.code == code and failure.value.retryable is retryable and len(calls) == 1
    for private in ("private token details", "local-refresh-token", "local-client-secret"):
        assert private not in str(failure.value) and private not in caplog.text


@pytest.mark.parametrize("status,reason,code,retryable,delay", [
    (401, "authError", "gmail_auth_rejected", True, 300),
    (429, "userRateLimitExceeded", "gmail_rate_limited", True, 3600),
    (403, "dailyLimitExceeded", "gmail_quota_exceeded", True, 21600),
    (403, "rateLimitExceeded", "gmail_rate_limited", True, 300),
    (403, "userRateLimitExceeded", "gmail_rate_limited", True, 300),
    (403, "domainPolicy", "gmail_permission_denied", False, None),
    (400, "badRequest", "gmail_message_rejected", False, None),
    (503, "backendError", "delivery_unknown", False, None),
    (302, "redirect", "delivery_unknown", False, None),
])
def test_send_error_policy_is_sanitized(app, monkeypatch, status, reason, code, retryable, delay):
    configure(app)
    transport(monkeypatch, [token(), Response(status, {"error": {
        "message": "private response text", "errors": [{"reason": reason}],
    }})])
    with app.app_context(), pytest.raises(notices.DeliveryError) as failure:
        notices.send_email(envelope(), "notice-123")
    assert (failure.value.code, failure.value.retryable, failure.value.retry_after) == (code, retryable, delay)
    assert "private" not in str(failure.value)


@pytest.mark.parametrize("header,delay", [("5", 60), ("120", 120), ("9999999", 21600), ("bad", 3600)])
def test_retry_after_is_honored_with_bounded_cooldown(app, monkeypatch, header, delay):
    configure(app)
    transport(monkeypatch, [token(), Response(429, headers={"Retry-After": header})])
    with app.app_context(), pytest.raises(notices.DeliveryError) as failure:
        notices.send_email(envelope(), "notice-123")
    assert failure.value.retry_after == delay


def test_queue_freezes_provider_before_network_then_clears_private_envelope(app, gmail_case, monkeypatch):
    identifier = cases.queue(app, gmail_case)
    def before(url, arguments):
        assert not db.session().in_transaction()
        with app.app_context():
            message = db.session.get(Notification, identifier)
            assert message.attempts == 1 and message.failure_code == "gmail_send_started"
            assert message.delivery_payload["_provider"] == "gmail"
    calls = transport(monkeypatch, [token(), Response()], before)
    with app.app_context():
        assert notices.deliver_pending()["sent"] == 1
        message = db.session.get(Notification, identifier)
        assert message.status == "sent" and message.provider_id == "gmail-123"
        assert message.delivery_payload is None and message.details == {} and message.failure_code is None
        assert notices.deliver_pending()["sent"] == 0
    assert len(calls) == 2


@pytest.mark.parametrize("response", [requests.ReadTimeout("private mail body"), Response(503),
    Response(body=b"not json"), Response(body={"id": ""}), Response(body=b"x" * 16385),
    Response(read_error=requests.ReadTimeout("while reading accepted response"))])
def test_ambiguous_send_is_terminal_and_never_retried(app, gmail_case, monkeypatch, response):
    identifier = cases.queue(app, gmail_case)
    calls = transport(monkeypatch, [token(), response])
    with app.app_context():
        assert notices.deliver_pending()["failed"] == 1
        message = db.session.get(Notification, identifier)
        assert message.status == "failed" and message.failure_code == "delivery_unknown"
        assert message.delivery_payload is None and message.details == {}
        assert notices.deliver_pending()["sent"] == 0
    assert len(calls) == 2


def test_explicit_rejection_retries_safely_with_same_trace_id_after_cooldown(app, gmail_case, monkeypatch):
    identifier = cases.queue(app, gmail_case)
    calls = transport(monkeypatch, [token(), Response(429, headers={"Retry-After": "120"}), token(), Response()])
    with app.app_context():
        before = utcnow()
        assert notices.deliver_pending()["failed"] == 0
        message = db.session.get(Notification, identifier)
        assert message.status == "pending" and message.failure_code == "gmail_rate_limited"
        assert message.available_at >= before + timedelta(seconds=120)
        assert message.delivery_payload["_provider"] == "gmail"
        assert notices.deliver_pending()["sent"] == 0
        due(identifier)
        assert notices.deliver_pending()["sent"] == 1
    sends = [BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(request["json"]["raw"]))
             for url, request in calls if url == gmail.SEND_URL]
    assert len(sends) == 2 and sends[0]["Message-ID"] == sends[1]["Message-ID"]


@pytest.mark.parametrize("new_provider", ["gmail", "resend", "disabled"])
def test_crashed_started_send_is_not_retried_after_lease_expiry(app, gmail_case, monkeypatch, new_provider):
    identifier = cases.queue(app, gmail_case)
    def crash(*args):
        raise RuntimeError("Worker stopped after send might have completed")
    monkeypatch.setattr(notices, "send_email", crash)
    with app.app_context():
        with pytest.raises(RuntimeError):
            notices.deliver_pending()
        db.session.remove()
        message = db.session.get(Notification, identifier)
        assert message.status == "sending" and message.attempts == 1
        assert notices.deliver_pending()["failed"] == 0  # Live lease is not stolen.
        message.lease_until = utcnow() - timedelta(seconds=1)
        db.session.commit()
        app.config["NOTIFICATION_EMAIL_PROVIDER"] = new_provider
        monkeypatch.setattr(notices, "send_email", lambda *args: pytest.fail("Ambiguous send retried"))
        notices.deliver_pending()
        message = db.session.get(Notification, identifier)
        assert message.status == "failed" and message.failure_code == "delivery_unknown"
        assert message.delivery_payload is None and message.lease_token is None


def test_crashed_unattempted_claim_can_be_recovered(app, gmail_case, monkeypatch):
    identifier = cases.queue(app, gmail_case)
    transport(monkeypatch, [token(), Response()])
    with app.app_context():
        message = db.session.get(Notification, identifier)
        message.status, message.lease_token = "sending", "old-worker"
        message.lease_until = utcnow() - timedelta(seconds=1)
        db.session.commit()
        assert notices.deliver_pending()["sent"] == 1


@pytest.mark.parametrize("provider,marker", [("gmail", "resend"), ("resend", "gmail"), ("gmail", "other"), ("gmail", None)])
def test_frozen_provider_cannot_be_changed_or_unknown(app, gmail_case, monkeypatch, provider, marker):
    identifier = cases.queue(app, gmail_case)
    app.config["NOTIFICATION_EMAIL_PROVIDER"] = provider
    monkeypatch.setattr(notices, "send_email", lambda *args: pytest.fail("Provider fallback attempted"))
    with app.app_context():
        message = db.session.get(Notification, identifier)
        message.delivery_payload = {**notices._payload(message), "_provider": marker}
        message.attempts, message.failure_code = 1, "gmail_rate_limited"
        db.session.commit()
        assert notices.deliver_pending()["failed"] == 1
        assert db.session.get(Notification, identifier).failure_code == "provider_changed"


@pytest.mark.parametrize("replacement", ["completed", "reclaimed", "expired"])
def test_gmail_stale_worker_cannot_prepare_or_send(app, gmail_case, monkeypatch, replacement):
    cases.test_stale_worker_cannot_prepare_or_send_after_losing_lease(app, gmail_case, monkeypatch, replacement)


def test_quota_retries_are_bounded_and_failure_code_remains_useful(app, gmail_case, monkeypatch):
    identifier = cases.queue(app, gmail_case)
    transport(monkeypatch, [token(), Response(403, {"error": {"errors": [{"reason": "dailyLimitExceeded"}]}})])
    with app.app_context():
        message = db.session.get(Notification, identifier)
        message.delivery_payload = {**notices._payload(message), "_provider": "gmail"}
        message.attempts, message.failure_code = notices.MAX_ATTEMPTS - 1, "gmail_quota_exceeded"
        db.session.commit()
        assert notices.deliver_pending()["failed"] == 1
        message = db.session.get(Notification, identifier)
        assert message.failure_code == "gmail_quota_exceeded" and message.status == "failed"
        assert message.delivery_payload is None
