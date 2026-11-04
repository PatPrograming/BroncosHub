"""Hosted payments: reserve locally, call Stripe outside locks, settle once.

The provider's signed events or authenticated reconciliation are authoritative.
Browser redirects never issue admission. Failed refunds remain durable work.
"""

import secrets
from datetime import timedelta
from urllib.parse import urlparse

from flask import current_app

from app.extensions import db
from app.models import Payment, Registration, Ticket
from app.models.checkout_attempt import CheckoutAttempt, utcnow
from app.services.event_service import campus_now
from app.services.event_write_service import event_write, occupied_seats
from app.services.payment_service import CheckoutNotAllowed, PAYMENTS_UNAVAILABLE


class BillingVerificationError(ValueError):
    """A provider message does not match the checkout we created."""


class CheckoutTemporarilyUnavailable(Exception):
    """The reservation is saved; retry with the same idempotency key."""


def _gateway():
    from app.services import stripe_gateway
    return stripe_gateway


def _attempt(attempt_id):
    return db.session.get(CheckoutAttempt, attempt_id, populate_existing=True)


def _event_open(event, registration):
    return bool(event and event.status == "published" and event.end_time > campus_now()
                and registration and registration.status != "cancelled")


def _provider_enabled():
    if current_app.config.get("PAYMENT_PROVIDER") != "stripe":
        raise CheckoutNotAllowed(PAYMENTS_UNAVAILABLE)


def _fully_refunded(charge, amount_cents):
    refunds = (charge.get("refunds") or {}).get("data", [])
    return (charge.get("amount_refunded") == amount_cents and isinstance(refunds, list)
            and sum(item.get("amount", 0) for item in refunds
                    if isinstance(item, dict) and item.get("status") == "succeeded") == amount_cents)


def _mark_refunded(attempt):
    attempt.status = "refunded"
    attempt.last_error = None
    payment = attempt.registration.payment
    if payment and payment.provider_reference == attempt.payment_intent_id:
        payment.status = "refunded"
        attempt.registration.status = "cancelled"
        if attempt.registration.ticket:
            attempt.registration.ticket.is_valid = False


def _error(attempt_id, event_id, message):
    with event_write(event_id):
        attempt = _attempt(attempt_id)
        if attempt:
            # Store operational context, never payment details or provider secrets.
            attempt.last_error = message[:500]
            attempt.retry_count += 1
            db.session.commit()


def start_checkout(registration, success_url, cancel_url):
    """Return a hosted URL; duplicate submits reuse the same reserved attempt."""
    _provider_enabled()
    registration_id, event_id = registration.id, registration.event_id
    organizer_id = registration.event.organizer_id
    db.session.rollback()
    try:
        account = _gateway().payment_account_for(organizer_id)
    except _gateway().PaymentProviderError as error:
        raise CheckoutTemporarilyUnavailable("We couldn't verify the organizer's payment account. Please try again.") from error
    if account is None:
        raise CheckoutNotAllowed("The organizer has not enabled online payments yet.")
    verified_account_id = account.stripe_account_id
    with event_write(event_id) as event:
        registration = db.session.get(Registration, registration_id, populate_existing=True)
        if not _event_open(event, registration) or event.is_free:
            raise CheckoutNotAllowed("This registration is not available for payment.")
        if registration.payment and registration.payment.status == "succeeded":
            return success_url
        if registration.payment and registration.payment.status in ("refund_pending", "refunded"):
            raise CheckoutNotAllowed("This purchase already has a payment record. View its receipt.")
        from app.models.connected_account import ConnectedAccount
        account = db.session.scalar(db.select(ConnectedAccount).where(
            ConnectedAccount.user_id == event.organizer_id,
            ConnectedAccount.stripe_account_id == verified_account_id,
        ))
        if account is None or not account.ready:
            raise CheckoutNotAllowed("The organizer has not enabled online payments yet.")
        active = db.session.scalar(db.select(CheckoutAttempt).where(
            CheckoutAttempt.registration_id == registration_id,
            CheckoutAttempt.status.in_(("creating", "open")),
            CheckoutAttempt.expires_at > utcnow(),
        ).order_by(CheckoutAttempt.id.desc()))
        if active is not None:
            attempt_id = active.id
            if active.checkout_url and active.status == "open":
                return active.checkout_url
        else:
            if event.capacity is not None and occupied_seats(event.id) >= event.capacity:
                raise CheckoutNotAllowed("This event is fully booked or its remaining seats are held at checkout.")
            for old in db.session.scalars(db.select(CheckoutAttempt).where(
                CheckoutAttempt.registration_id == registration_id,
                CheckoutAttempt.status.in_(("creating", "open")),
            )):
                old.status = "expired"
                old.expire_requested = True
            attempt = CheckoutAttempt(
                registration_id=registration_id, event_id=event_id,
                account_id=account.stripe_account_id, amount_cents=event.price_cents,
                event_title=event.title, customer_email=registration.student.email,
                currency="usd", livemode=bool(current_app.config.get("STRIPE_LIVE_MODE", False)),
                expires_at=utcnow() + timedelta(minutes=31), status="creating",
                success_url=success_url, cancel_url=cancel_url,
            )
            db.session.add(attempt)
            db.session.flush()
            attempt_id = attempt.id
        db.session.commit()
    return _create_session(attempt_id)


def _create_session(attempt_id):
    attempt = _attempt(attempt_id)
    if attempt is None:
        raise CheckoutNotAllowed("This checkout is no longer available.")
    event_id = attempt.event_id
    with event_write(event_id) as event:
        attempt = _attempt(attempt_id)
        registration = attempt.registration
        if attempt.status not in ("creating", "open") or attempt.expires_at <= utcnow() or not _event_open(event, registration):
            raise CheckoutNotAllowed("This checkout has expired or closed. Start checkout again.")
        if attempt.checkout_url:
            return attempt.checkout_url
        arguments = dict(
            account_id=attempt.account_id, amount_cents=attempt.amount_cents,
            event_title=attempt.event_title, attempt_id=attempt.id,
            registration_id=attempt.registration_id, student_email=attempt.customer_email,
            expires_at=attempt.expires_at, success_url=attempt.success_url,
            cancel_url=attempt.cancel_url, idempotency_key=f"broncos-checkout-{attempt.id}",
        )
    try:
        result = _gateway().create_checkout(**arguments)
    except _gateway().PaymentProviderError as error:
        _error(attempt_id, event_id, "Hosted checkout could not be created. Retry pending.")
        if not error.retryable:
            with event_write(event_id):
                attempt = _attempt(attempt_id)
                if attempt.status == "creating":
                    attempt.status = "failed"
                db.session.commit()
        raise CheckoutTemporarilyUnavailable("We couldn't reach checkout. Your seat is temporarily held; try again.") from error
    checkout_url = result.get("url", "")
    parsed = urlparse(checkout_url)
    if parsed.scheme != "https" or parsed.hostname != "checkout.stripe.com":
        _error(attempt_id, event_id, "Provider returned an unexpected checkout URL.")
        raise CheckoutTemporarilyUnavailable("Checkout could not be opened. Please try again.")
    with event_write(event_id) as event:
        attempt = _attempt(attempt_id)
        if attempt.session_id and attempt.session_id != result["id"]:
            raise BillingVerificationError("Checkout session changed for an existing attempt")
        attempt.session_id = result["id"]
        attempt.checkout_url = checkout_url
        attempt.last_error = None
        if attempt.status == "creating" and attempt.expires_at > utcnow() and _event_open(event, attempt.registration):
            attempt.status = "open"
        elif attempt.status != "completed":
            attempt.expire_requested = True
        usable = attempt.status == "open"
        completed_url = attempt.success_url if attempt.status == "completed" else None
        db.session.commit()
    if completed_url:
        return completed_url
    if not usable:
        reconcile_attempt(attempt_id)
        raise CheckoutNotAllowed("This checkout closed before it could be opened. No ticket was issued.")
    return checkout_url


def _match_session(attempt, session, account_id):
    if not isinstance(session, dict) or not isinstance(session.get("metadata"), dict):
        raise BillingVerificationError("Missing checkout metadata")
    metadata = session["metadata"]
    if account_id != attempt.account_id or session.get("livemode") is not attempt.livemode:
        raise BillingVerificationError("Checkout account or mode does not match")
    if (str(metadata.get("attempt_id")) != str(attempt.id)
            or str(metadata.get("registration_id")) != str(attempt.registration_id)):
        raise BillingVerificationError("Checkout metadata does not match")
    if session.get("amount_total") != attempt.amount_cents or session.get("currency") != attempt.currency:
        raise BillingVerificationError("Checkout amount or currency does not match")
    if not session.get("id") or (attempt.session_id and session["id"] != attempt.session_id):
        raise BillingVerificationError("Checkout session does not match")
    if session.get("mode") != "payment":
        raise BillingVerificationError("Checkout must be a one-time payment")


def process_session(session, account_id, *, verified_snapshot=False):
    """Apply a signature-verified or API-retrieved session, idempotently."""
    if not isinstance(session, dict) or not isinstance(session.get("metadata"), dict):
        raise BillingVerificationError("Missing checkout metadata")
    metadata = session["metadata"]
    try:
        attempt_id = int(metadata.get("attempt_id", ""))
        if not 1 <= attempt_id <= 9223372036854775807:
            raise ValueError
    except (TypeError, ValueError):
        raise BillingVerificationError("Missing checkout attempt") from None
    attempt = _attempt(attempt_id)
    if attempt is None:
        raise BillingVerificationError("Unknown checkout attempt")
    event_id = attempt.event_id
    _match_session(attempt, session, account_id)
    if session.get("payment_status") == "paid" and not verified_snapshot:
        session_id = session["id"]
        db.session.rollback()
        # Webhooks can arrive out of order. Read the current charge before
        # admitting anyone, including a refund that arrived before completion.
        session = _gateway().retrieve_checkout(account_id, session_id)
    refund = False
    with event_write(event_id) as event:
        attempt = _attempt(attempt_id)
        _match_session(attempt, session, account_id)
        registration = attempt.registration
        attempt.session_id = session["id"]
        if session.get("payment_status") != "paid":
            if session.get("status") == "expired" and attempt.status in ("creating", "open", "expired", "cancelled"):
                attempt.status = "cancelled" if registration.status == "cancelled" else "expired"
                attempt.expire_requested = False
                db.session.commit()
            return
        intent = session.get("payment_intent")
        payment_intent = intent.get("id") if isinstance(intent, dict) else None
        if not isinstance(payment_intent, str) or not payment_intent.startswith("pi_"):
            raise BillingVerificationError("Missing payment reference")
        charge = intent.get("latest_charge")
        if (not isinstance(charge, dict) or charge.get("amount") != attempt.amount_cents
                or charge.get("currency") != attempt.currency or charge.get("paid") is not True):
            raise BillingVerificationError("Missing matching charge confirmation")
        refund_amount = charge.get("amount_refunded", 0)
        if type(refund_amount) is not int or not 0 <= refund_amount <= attempt.amount_cents:
            raise BillingVerificationError("Invalid refunded amount")
        already_refunded = _fully_refunded(charge, attempt.amount_cents)
        if attempt.payment_intent_id and attempt.payment_intent_id != payment_intent:
            raise BillingVerificationError("Payment reference changed")
        attempt.payment_intent_id = payment_intent
        if attempt.status in ("completed", "refunded", "refund_pending"):
            return
        payment = registration.payment
        # An older attempt can finish after a newer one has already paid. Its
        # separate attempt remains refundable without replacing the receipt.
        another_payment = payment is not None and payment.provider_reference != payment_intent and payment.status in (
            "succeeded", "refund_pending", "refunded",
        )
        valid = (not another_payment and refund_amount == 0 and attempt.status in ("creating", "open")
                 and attempt.expires_at > utcnow() and _event_open(event, registration)
                 and (event.capacity is None or occupied_seats(event_id, registration.id) < event.capacity))
        payment_status = "succeeded" if valid else ("refunded" if already_refunded else "refund_pending")
        if not another_payment:
            if payment is None:
                payment = Payment(registration_id=registration.id, amount_cents=attempt.amount_cents,
                                  status=payment_status, provider_reference=payment_intent)
                db.session.add(payment)
            else:
                payment.amount_cents = attempt.amount_cents
                payment.provider_reference = payment_intent
                payment.status = payment_status
        if valid:
            registration.status = "confirmed"
            if registration.ticket is None:
                db.session.add(Ticket(registration=registration, code=f"BH-{secrets.token_hex(4).upper()}", is_valid=True))
            else:
                registration.ticket.is_valid = True
            attempt.status = "completed"
        else:
            attempt.status = "refunded" if already_refunded else "refund_pending"
            refund = not already_refunded
            if not another_payment:
                registration.status = "cancelled"
                if registration.ticket:
                    registration.ticket.is_valid = False
        attempt.expire_requested = False
        attempt.last_error = None
        db.session.commit()
    if refund:
        refund_attempt(attempt_id)


def queue_event_cancellations(event_id):
    """Called inside the event's mutation transaction; no network or commits."""
    for attempt in db.session.scalars(db.select(CheckoutAttempt).where(CheckoutAttempt.event_id == event_id)):
        if attempt.status == "completed":
            attempt.status = "refund_pending"
            payment = attempt.registration.payment
            if payment and payment.provider_reference == attempt.payment_intent_id:
                payment.status = "refund_pending"
            if attempt.registration.ticket:
                attempt.registration.ticket.is_valid = False
        elif attempt.status in ("creating", "open", "expired"):
            attempt.status = "cancelled"
            attempt.expire_requested = True


def refund_attempt(attempt_id):
    attempt = _attempt(attempt_id)
    if attempt is None:
        return False
    event_id = attempt.event_id
    with event_write(event_id):
        attempt = _attempt(attempt_id)
        if attempt.status != "refund_pending" or not attempt.payment_intent_id:
            return False
        account_id, payment_intent, session_id = attempt.account_id, attempt.payment_intent_id, attempt.session_id
        refund_id = attempt.refund_id
    try:
        snapshot = _gateway().retrieve_checkout(account_id, session_id)
        with event_write(event_id):
            attempt = _attempt(attempt_id)
            _match_session(attempt, snapshot, account_id)
            intent = snapshot.get("payment_intent") or {}
            charge = intent.get("latest_charge") or {} if isinstance(intent, dict) else {}
            if not isinstance(intent, dict) or intent.get("id") != payment_intent:
                raise BillingVerificationError("Refund payment does not match")
            if _fully_refunded(charge, attempt.amount_cents):
                _mark_refunded(attempt)
                db.session.commit()
                return True
            if charge.get("amount_refunded") == attempt.amount_cents and not refund_id:
                # A full external refund may still be pending. Never request
                # another refund for money already assigned to that refund.
                items = (charge.get("refunds") or {}).get("data", [])
                failed = any(item.get("status") in ("failed", "canceled") for item in items)
                attempt.last_error = ("Refund failed; operator review required." if failed else
                                      "The provider is still processing an existing full refund.")
                db.session.commit()
                return False
            previously_refunded = charge.get("amount_refunded", 0)
        result = _gateway().retrieve_refund(account_id, refund_id) if refund_id else None
        # A successful external partial refund is not the full refund requested
        # by cancellation. Complete the remaining balance with a stable key for
        # that balance, rather than polling the partial refund forever.
        if result is None or (result.get("status") == "succeeded" and previously_refunded < attempt.amount_cents):
            result = _gateway().refund_payment(account_id, payment_intent,
                                               f"broncos-refund-{attempt_id}-{previously_refunded}")
        snapshot = _gateway().retrieve_checkout(account_id, session_id)
    except _gateway().PaymentProviderError:
        _error(attempt_id, event_id, "Full refund could not be confirmed. Retry pending.")
        return False
    with event_write(event_id):
        attempt = _attempt(attempt_id)
        _match_session(attempt, snapshot, account_id)
        if result.get("payment_intent") != payment_intent or not str(result.get("id", "")).startswith("re_"):
            raise BillingVerificationError("Refund reference does not match")
        attempt.refund_id = result["id"]
        if result.get("status") != "succeeded":
            attempt.last_error = ("Refund failed; operator review required."
                                  if result.get("status") in ("failed", "canceled") else
                                  "Full refund is still processing. Retry pending.")
            db.session.commit()
            return False
        intent = snapshot.get("payment_intent") or {}
        charge = (intent.get("latest_charge") or {}) if isinstance(intent, dict) else {}
        if (result.get("payment_intent") != payment_intent or not isinstance(intent, dict)
                or intent.get("id") != payment_intent or not _fully_refunded(charge, attempt.amount_cents)
                or charge.get("amount") != attempt.amount_cents or charge.get("currency") != attempt.currency):
            attempt.last_error = "Full refund has not been verified. Retry pending."
            db.session.commit()
            return False
        _mark_refunded(attempt)
        db.session.commit()
    return True


def process_refund(charge, account_id):
    """Honor full refunds initiated in Stripe as well as application refunds."""
    if not isinstance(charge, dict):
        raise BillingVerificationError("Missing refund information")
    payment_intent = charge.get("payment_intent")
    if not isinstance(payment_intent, str) or not payment_intent.startswith("pi_"):
        raise BillingVerificationError("Missing refund payment reference")
    attempt = db.session.scalar(db.select(CheckoutAttempt).where(CheckoutAttempt.payment_intent_id == payment_intent))
    if attempt is None:
        return  # May precede checkout completion; reconciliation will observe it.
    event_id, attempt_id = attempt.event_id, attempt.id
    if account_id != attempt.account_id:
        raise BillingVerificationError("Refund account does not match")
    session_id = attempt.session_id
    db.session.rollback()
    snapshot = _gateway().retrieve_checkout(account_id, session_id)
    with event_write(event_id):
        attempt = _attempt(attempt_id)
        _match_session(attempt, snapshot, account_id)
        if (account_id != attempt.account_id or charge.get("livemode") is not attempt.livemode
                or charge.get("amount") != attempt.amount_cents or charge.get("currency") != attempt.currency):
            raise BillingVerificationError("Refund does not match the purchase")
        if attempt.status == "refunded":
            return  # An older partial-refund notification cannot undo finality.
        intent = snapshot.get("payment_intent") or {}
        latest_charge = intent.get("latest_charge") or {} if isinstance(intent, dict) else {}
        if not isinstance(intent, dict) or intent.get("id") != payment_intent:
            raise BillingVerificationError("Refund payment does not match")
        full = _fully_refunded(latest_charge, attempt.amount_cents)
        attempt.status = "refunded" if full else "refund_pending"
        payment = attempt.registration.payment
        if payment and payment.provider_reference == payment_intent:
            payment.status = attempt.status
            attempt.registration.status = "cancelled"
            if attempt.registration.ticket:
                attempt.registration.ticket.is_valid = False
        db.session.commit()


def process_refund_notification(refund, account_id, livemode):
    """Recheck pending/failed refunds, including failures after an earlier success."""
    if not isinstance(refund, dict) or not str(refund.get("id", "")).startswith("re_"):
        raise BillingVerificationError("Missing refund reference")
    payment_intent = refund.get("payment_intent")
    if not isinstance(payment_intent, str) or not payment_intent.startswith("pi_"):
        raise BillingVerificationError("Missing refund payment reference")
    attempt = db.session.scalar(db.select(CheckoutAttempt).where(CheckoutAttempt.payment_intent_id == payment_intent))
    if attempt is None:
        return  # Completion will read the current charge before issuing admission.
    if account_id != attempt.account_id or livemode is not attempt.livemode:
        raise BillingVerificationError("Refund account or mode does not match")
    attempt_id, event_id, session_id = attempt.id, attempt.event_id, attempt.session_id
    db.session.rollback()
    actual = _gateway().retrieve_refund(account_id, refund["id"])
    snapshot = _gateway().retrieve_checkout(account_id, session_id)
    with event_write(event_id):
        attempt = _attempt(attempt_id)
        _match_session(attempt, snapshot, account_id)
        if (actual.get("id") != refund["id"] or actual.get("payment_intent") != payment_intent
                or actual.get("currency") != attempt.currency
                or type(actual.get("amount")) is not int or not 0 < actual["amount"] <= attempt.amount_cents):
            raise BillingVerificationError("Refund information does not match")
        intent = snapshot.get("payment_intent") or {}
        charge = (intent.get("latest_charge") or {}) if isinstance(intent, dict) else {}
        if _fully_refunded(charge, attempt.amount_cents):
            _mark_refunded(attempt)
        else:
            attempt.status = "refund_pending"
            attempt.refund_id = (None if actual.get("status") == "succeeded"
                                 and charge.get("amount_refunded", 0) < attempt.amount_cents else actual["id"])
            attempt.last_error = ("Refund failed; operator review required." if actual.get("status") in ("failed", "canceled")
                                  else "Full refund is still processing.")
            payment = attempt.registration.payment
            if payment and payment.provider_reference == payment_intent:
                payment.status = "refund_pending"
                attempt.registration.status = "cancelled"
                if attempt.registration.ticket:
                    attempt.registration.ticket.is_valid = False
        db.session.commit()


def reconcile_attempt(attempt_id):
    """Retry after network failures; safe to invoke repeatedly or concurrently."""
    attempt = _attempt(attempt_id)
    if attempt is None:
        return
    event_id, status = attempt.event_id, attempt.status
    if status == "refund_pending":
        refund_attempt(attempt_id)
        return
    if status in ("completed", "refunded"):
        return
    if not attempt.session_id:
        if status == "creating" and attempt.expires_at > utcnow():
            try:
                _create_session(attempt_id)
            except (CheckoutNotAllowed, CheckoutTemporarilyUnavailable):
                pass
        elif attempt.expires_at <= utcnow():
            with event_write(event_id):
                attempt = _attempt(attempt_id)
                attempt.status = "expired" if attempt.status == "creating" else attempt.status
                attempt.expire_requested = False
                db.session.commit()
        return
    account_id, session_id = attempt.account_id, attempt.session_id
    should_expire = attempt.expire_requested or attempt.expires_at <= utcnow()
    db.session.rollback()  # Never hold a read transaction across a network call.
    try:
        session = _gateway().retrieve_checkout(account_id, session_id)
        process_session(session, account_id, verified_snapshot=True)
        if session.get("payment_status") == "paid" or session.get("status") == "expired":
            return
        if should_expire:
            expired = _gateway().expire_checkout(account_id, session_id, f"broncos-expire-{attempt_id}")
            process_session(expired, account_id)
    except _gateway().PaymentProviderError:
        _error(attempt_id, event_id, "Checkout reconciliation could not reach the provider. Retry pending.")


def reconcile_pending(limit=100, event_id=None):
    query = db.select(CheckoutAttempt.id).where(db.or_(
        CheckoutAttempt.status.in_(("creating", "open", "refund_pending")),
        CheckoutAttempt.expire_requested.is_(True),
    ))
    if event_id is not None:
        query = query.where(CheckoutAttempt.event_id == event_id)
    ids = list(db.session.scalars(query.order_by(CheckoutAttempt.updated_at, CheckoutAttempt.id).limit(limit)))
    db.session.rollback()
    for attempt_id in ids:
        try:
            reconcile_attempt(attempt_id)
        except BillingVerificationError:
            attempt = _attempt(attempt_id)
            _error(attempt_id, attempt.event_id, "Provider information did not match; operator review required.")
        finally:
            # Rotate unresolved entries so an early failing batch cannot starve
            # newer cancellations/refunds when the worker uses a bounded limit.
            attempt = _attempt(attempt_id)
            if attempt:
                with event_write(attempt.event_id):
                    attempt = _attempt(attempt_id)
                    attempt.updated_at = utcnow()
                    db.session.commit()
    return len(ids)
