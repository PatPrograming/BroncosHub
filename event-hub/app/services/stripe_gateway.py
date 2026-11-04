"""Stripe's network boundary. Call before/after, never inside, event write locks.

Accounts v2 gives organizers their own full Dashboard. Checkout and refunds are
direct charges in that account, with no BroncosHub application fee.
"""

from datetime import datetime, timezone
import re
import time
from urllib.parse import urlsplit

from flask import current_app
from sqlalchemy.exc import IntegrityError
import stripe

from app.extensions import db
from app.models.connected_account import ConnectedAccount


class PaymentProviderError(Exception):
    def __init__(self, message="Payments are temporarily unavailable. Please try again.", *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class PaymentSignatureError(PaymentProviderError):
    pass


def configured():
    return (current_app.config.get("PAYMENT_PROVIDER") == "stripe"
            and bool(current_app.config.get("STRIPE_SECRET_KEY"))
            and bool(current_app.config.get("STRIPE_WEBHOOK_SECRET"))
            and bool(current_app.config.get("PUBLIC_BASE_URL")))


def _client():
    if not configured():
        raise PaymentProviderError("Paid events aren't available until Stripe is configured.")
    key = current_app.config["STRIPE_SECRET_KEY"]
    expected_prefix = "sk_live_" if current_app.config.get("STRIPE_LIVE_MODE", False) else "sk_test_"
    if not key.startswith(expected_prefix):
        raise PaymentProviderError("Stripe's payment mode needs to be checked by the site administrator.")
    return stripe.StripeClient(
        key, stripe_version="2026-10-28.endive", max_network_retries=1,
        http_client=stripe.RequestsClient(timeout=(5, 12)),
    )


def _call(method, *args, **kwargs):
    try:
        result = method(*args, **kwargs)
        return result.to_dict() if hasattr(result, "to_dict") else dict(result)
    except stripe.StripeError as error:
        # Provider messages can include request details; never expose them in UI.
        retryable = isinstance(error, (stripe.APIConnectionError, stripe.RateLimitError)) or (
            error.http_status is not None and error.http_status >= 500)
        raise PaymentProviderError(retryable=retryable) from None


def _identifier(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"[A-Za-z0-9_]{1,180}", value):
        raise PaymentProviderError("The payment reference is invalid.")
    return value


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_:.\-]{1,200}", value):
        raise PaymentProviderError("The payment request reference is invalid.")
    return value


def _parse_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) <= 32 for c in value) or "\\" in value:
        raise PaymentProviderError("The payment redirect is invalid.")
    try:
        parsed = urlsplit(value)
        if parsed.username or parsed.password or not parsed.hostname:
            raise ValueError()
        parsed.port
        return parsed
    except ValueError:
        raise PaymentProviderError("The payment redirect is invalid.") from None


def trusted_url(path):
    """Build callbacks from deployment config, never Host headers or form fields."""
    base = current_app.config.get("PUBLIC_BASE_URL", "").rstrip("/")
    parsed = _parse_url(base)
    local = (current_app.config.get("APP_ENV") != "production" and parsed.hostname in ("localhost", "127.0.0.1", "::1"))
    if ((parsed.scheme != "https" and not (local and parsed.scheme == "http"))
            or parsed.path or parsed.query or parsed.fragment
            or not isinstance(path, str) or not path.startswith("/") or path.startswith("//")):
        raise PaymentProviderError("The site's payment return address needs to be configured.")
    result = base + path
    candidate = _parse_url(result)
    if (candidate.scheme, candidate.netloc) != (parsed.scheme, parsed.netloc):
        raise PaymentProviderError("The payment redirect is invalid.")
    return result


def _callback_url(value):
    parsed = _parse_url(value)
    origin = _parse_url(trusted_url("/"))
    if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc) or parsed.fragment:
        raise PaymentProviderError("The payment return address is invalid.")
    return value


def _stripe_url(value, *, onboarding=False):
    parsed = _parse_url(value)
    hosts = {"connect.stripe.com", "accounts.stripe.com"} if onboarding else {"checkout.stripe.com"}
    if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.port not in (None, 443):
        raise PaymentProviderError("Stripe returned an unexpected redirect address.")
    return value


def create_checkout(account_id, amount_cents, event_title, attempt_id, registration_id,
                    student_email, expires_at, success_url, cancel_url, idempotency_key):
    account_id = _identifier(account_id, "acct_")
    if type(amount_cents) is not int or not 50 <= amount_cents <= 999900:
        raise PaymentProviderError("Paid tickets must cost between $0.50 and $9,999.00.")
    if isinstance(expires_at, datetime):
        expires_at = int(expires_at.replace(tzinfo=expires_at.tzinfo or timezone.utc).timestamp())
    # The reservation service supplies 31 minutes for new attempts. Preserve the
    # original expiry on retries so a lost response can recover via idempotency.
    if type(expires_at) is not int or not 0 < expires_at - time.time() <= 86400:
        raise PaymentProviderError("This checkout reservation expired. Please start again.")
    metadata = {"attempt_id": str(attempt_id), "registration_id": str(registration_id)}
    result = _call(_client().v1.checkout.sessions.create, {
        "mode": "payment", "payment_method_types": ["card"],
        "line_items": [{"price_data": {"currency": "usd", "unit_amount": amount_cents,
                        "product_data": {"name": event_title[:100]}}, "quantity": 1}],
        "client_reference_id": str(attempt_id), "customer_email": student_email,
        "metadata": metadata, "payment_intent_data": {"metadata": metadata},
        "expires_at": expires_at, "success_url": _callback_url(success_url),
        "cancel_url": _callback_url(cancel_url),
    }, options={"stripe_account": account_id, "idempotency_key": _key(idempotency_key)})
    _identifier(result.get("id"), "cs_")
    _stripe_url(result.get("url"))
    return result


def retrieve_checkout(account_id, session_id):
    account_id = _identifier(account_id, "acct_")
    client = _client()
    result = _call(client.v1.checkout.sessions.retrieve, _identifier(session_id, "cs_"),
                   {"expand": ["payment_intent.latest_charge.refunds"]},
                   options={"stripe_account": account_id})
    intent = result.get("payment_intent")
    charge = intent.get("latest_charge") if isinstance(intent, dict) else None
    refunds = charge.get("refunds") if isinstance(charge, dict) else None
    if isinstance(refunds, dict) and refunds.get("has_more") is True:
        # Expanded lists contain only the first page. Do not mistake ten
        # partial refunds for all refunds, or declare a pending refund complete.
        items = refunds.get("data")
        if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
            raise PaymentProviderError("The refund history could not be verified.")
        items = list(items)
        seen = {item.get("id") for item in items}
        charge_id = _identifier(charge.get("id"), "ch_")
        for _ in range(20):
            after = _identifier(items[-1].get("id"), "re_")
            page = _call(client.v1.refunds.list,
                         {"charge": charge_id, "limit": 100, "starting_after": after},
                         options={"stripe_account": account_id})
            batch = page.get("data")
            if (not isinstance(batch, list) or not batch
                    or not all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in batch)
                    or len({item["id"] for item in batch}) != len(batch)
                    or any(item["id"] in seen for item in batch)):
                raise PaymentProviderError("The refund history could not be verified.")
            items.extend(batch)
            seen.update(item["id"] for item in batch)
            if page.get("has_more") is False:
                refunds.update(data=items, has_more=False)
                break
        else:
            raise PaymentProviderError("This refund history needs review by the site administrator.")
    return result


def expire_checkout(account_id, session_id, idempotency_key):
    return _call(_client().v1.checkout.sessions.expire, _identifier(session_id, "cs_"),
                 options={"stripe_account": _identifier(account_id, "acct_"),
                          "idempotency_key": _key(idempotency_key)})


def refund_payment(account_id, payment_intent_id, idempotency_key):
    return _call(_client().v1.refunds.create,
                 {"payment_intent": _identifier(payment_intent_id, "pi_")},
                 options={"stripe_account": _identifier(account_id, "acct_"),
                          "idempotency_key": _key(idempotency_key)})


def retrieve_refund(account_id, refund_id):
    return _call(_client().v1.refunds.retrieve, _identifier(refund_id, "re_"),
                 options={"stripe_account": _identifier(account_id, "acct_")})


def construct_event(rawpayload, signature):
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET")
    if not secret or not isinstance(signature, str) or len(signature) > 4096:
        raise PaymentSignatureError("Invalid payment notification.")
    try:
        event = stripe.Webhook.construct_event(rawpayload, signature, secret, tolerance=300)
        data = event.to_dict()
        if (data.get("object") != "event" or not isinstance(data.get("id"), str)
                or not isinstance(data.get("type"), str)):
            raise ValueError("Invalid event envelope")
        return data
    except (ValueError, TypeError, AttributeError, KeyError, stripe.SignatureVerificationError):
        raise PaymentSignatureError("Invalid payment notification.") from None


def create_connected_account(email, display_name, owner_id, idempotency_key, country):
    if country != "US":
        raise PaymentProviderError("Choose United States for a US-based payment account. Other account countries aren't supported yet.")
    return _call(_client().v2.core.accounts.create, {
        "contact_email": email, "display_name": display_name[:100], "dashboard": "full",
        "identity": {"country": country},
        "configuration": {"merchant": {"capabilities": {"card_payments": {"requested": True}}}},
        "defaults": {"responsibilities": {"fees_collector": "stripe", "losses_collector": "stripe"}},
        "metadata": {"broncoshub_user_id": str(owner_id)},
        "include": ["configuration.merchant", "defaults", "requirements"],
    }, options={"idempotency_key": _key(idempotency_key)})


def retrieve_connected_account(account_id):
    return _call(_client().v2.core.accounts.retrieve, _identifier(account_id, "acct_"),
                 {"include": ["configuration.merchant", "defaults", "requirements"]})


def create_onboarding_link(account_id, idempotency_key):
    result = _call(_client().v2.core.account_links.create, {
        "account": _identifier(account_id, "acct_"),
        "use_case": {"type": "account_onboarding", "account_onboarding": {
            "return_url": trusted_url("/organizer/payments/return"),
            "refresh_url": trusted_url("/organizer/payments/expired"),
            "collection_options": {"fields": "eventually_due"},
        }},
    }, options={"idempotency_key": _key(idempotency_key)})
    _stripe_url(result.get("url"), onboarding=True)
    return result


def _apply_account_status(account, data):
    if data.get("id") != account.stripe_account_id:
        raise PaymentProviderError("Stripe returned an unexpected account.")
    capabilities = ((data.get("configuration") or {}).get("merchant") or {}).get("capabilities") or {}
    responsibilities = (data.get("defaults") or {}).get("responsibilities") or {}
    requirements = data.get("requirements")
    deadline = ((requirements or {}).get("summary") or {}).get("minimum_deadline") or {}
    account.charges_enabled = (capabilities.get("card_payments") or {}).get("status") == "active"
    account.payouts_enabled = ((capabilities.get("stripe_balance") or {}).get("payouts") or {}).get("status") == "active"
    account.details_submitted = (isinstance(requirements, dict)
                                 and deadline.get("status") in (None, "eventually_due")
                                 and account.charges_enabled and account.payouts_enabled)
    account.livemode = data.get("livemode") is True
    account.configuration_verified = (
        data.get("dashboard") == "full"
        and responsibilities.get("fees_collector") == "stripe"
        and responsibilities.get("losses_collector") == "stripe"
        and type(data.get("livemode")) is bool
        and account.livemode == bool(current_app.config.get("STRIPE_LIVE_MODE", False)))
    account.checked_at = datetime.now(timezone.utc).replace(tzinfo=None)


def refresh_connected_account(user_id):
    """Recheck capabilities and mode. No write transaction spans the API call."""
    account = db.session.scalar(db.select(ConnectedAccount).where(ConnectedAccount.user_id == user_id))
    if account is None or not account.stripe_account_id:
        return None
    account_id = account.stripe_account_id
    db.session.rollback()
    try:
        data = retrieve_connected_account(account_id)
    except PaymentProviderError:
        account = db.session.scalar(db.select(ConnectedAccount).where(ConnectedAccount.user_id == user_id))
        account.configuration_verified = False
        db.session.commit()
        raise
    account = db.session.scalar(db.select(ConnectedAccount).where(ConnectedAccount.user_id == user_id))
    try:
        _apply_account_status(account, data)
    except PaymentProviderError:
        account.configuration_verified = False
        db.session.commit()
        raise
    db.session.commit()
    return account


def payment_account_for(user_id):
    if not configured():
        return None
    account = refresh_connected_account(user_id)
    return account if account and account.ready else None


def begin_onboarding(user, country=None):
    """Persist a stable creation key so timeouts never make duplicate accounts."""
    if not configured():
        raise PaymentProviderError("Paid events aren't available until Stripe is configured.")
    user_id, email, name = user.id, user.email, user.name
    account = db.session.scalar(db.select(ConnectedAccount).where(ConnectedAccount.user_id == user_id))
    if (not account or not account.stripe_account_id) and country != "US":
        raise PaymentProviderError("Choose United States only if your payment account is based there. Other account countries aren't supported yet.")
    if country is not None and country != "US":
        raise PaymentProviderError("The payment account country cannot be changed here.")
    if account is None:
        account = ConnectedAccount(user_id=user_id, creation_email=email, creation_name=name[:100])
        db.session.add(account)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            account = db.session.scalar(db.select(ConnectedAccount).where(ConnectedAccount.user_id == user_id))
    if not account.creation_email or not account.creation_name:
        account.creation_email, account.creation_name = email, name[:100]
        db.session.commit()
    account_id, creation_key = account.stripe_account_id, account.onboarding_key
    email, name = account.creation_email, account.creation_name
    db.session.rollback()
    if not account_id:
        data = create_connected_account(email, name, user_id, "onboard:" + creation_key, "US")
        account_id = _identifier(data.get("id"), "acct_")
        if data.get("livemode") is not bool(current_app.config.get("STRIPE_LIVE_MODE", False)):
            raise PaymentProviderError("Stripe's account mode does not match the site.")
        account = db.session.scalar(db.select(ConnectedAccount).where(ConnectedAccount.user_id == user_id))
        account.stripe_account_id = account_id
        # Even a completion URL cannot mark an account active: refresh from API.
        db.session.commit()
    db.session.rollback()
    import secrets
    return create_onboarding_link(account_id, "onboard-link:" + secrets.token_urlsafe(24))
