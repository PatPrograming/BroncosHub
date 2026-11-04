"""Provider boundary checks; SDK calls are replaced, never real payments."""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import stripe

from app.extensions import db
from app.models import ConnectedAccount, User
from app.services import stripe_gateway as gateway


@pytest.fixture
def configured_app(app):
    app.config.update(PAYMENT_PROVIDER="stripe", STRIPE_SECRET_KEY="sk_test_fixture_only",
                      STRIPE_WEBHOOK_SECRET="whsec_fixture_only", STRIPE_LIVE_MODE=False,
                      PUBLIC_BASE_URL="https://broncos.example")
    return app


@pytest.fixture
def sdk(monkeypatch):
    client = SimpleNamespace(
        v1=SimpleNamespace(checkout=SimpleNamespace(sessions=SimpleNamespace(
            create=Mock(return_value={"id": "cs_test_example", "url": "https://checkout.stripe.com/c/pay/example#token"}),
            retrieve=Mock(return_value={"id": "cs_test_example", "payment_intent": {"id": "pi_example", "latest_charge": {"amount_refunded": 500}}}),
            expire=Mock(return_value={"id": "cs_test_example", "status": "expired"}))),
            refunds=SimpleNamespace(create=Mock(return_value={"id": "re_example", "status": "pending"}),
                                    list=Mock(),
                                    retrieve=Mock(return_value={"id": "re_example", "status": "succeeded"}))),
        v2=SimpleNamespace(core=SimpleNamespace(
            accounts=SimpleNamespace(create=Mock(return_value={"id": "acct_example", "livemode": False}),
                                     retrieve=Mock(return_value=account_data())),
            account_links=SimpleNamespace(create=Mock(return_value={"url": "https://accounts.stripe.com/r/acct_example#link"})))),
    )
    monkeypatch.setattr(gateway, "_client", lambda: client)
    return client


def account_data(**changes):
    data = dict(id="acct_example", livemode=False, dashboard="full",
                defaults={"responsibilities": {"fees_collector": "stripe", "losses_collector": "stripe"}},
                configuration={"merchant": {"capabilities": {
                    "card_payments": {"status": "active"}, "stripe_balance": {"payouts": {"status": "active"}}}}},
                requirements={"summary": {"minimum_deadline": None}})
    data.update(changes)
    return data


def checkout_args(**changes):
    args = dict(account_id="acct_example", amount_cents=500, event_title="Campus movie night",
                attempt_id=12, registration_id=34, student_email="student@scu.edu",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=31),
                success_url="https://broncos.example/payments/return?attempt=12",
                cancel_url="https://broncos.example/payments/cancel", idempotency_key="checkout:12")
    args.update(changes)
    return args


def add_account():
    user = User(email="organizer@scu.edu", google_sub="stripe-fixture", name="Organizer", role="organizer")
    db.session.add(user)
    db.session.flush()
    account = ConnectedAccount(user_id=user.id, stripe_account_id="acct_example")
    db.session.add(account)
    db.session.commit()
    return user.id


def test_checkout_is_direct_exact_amount_without_platform_fee(configured_app, sdk):
    with configured_app.app_context():
        result = gateway.create_checkout(**checkout_args())
    params = sdk.v1.checkout.sessions.create.call_args.args[0]
    options = sdk.v1.checkout.sessions.create.call_args.kwargs["options"]
    assert options == {"stripe_account": "acct_example", "idempotency_key": "checkout:12"}
    assert params["line_items"][0]["price_data"]["unit_amount"] == 500
    assert params["payment_method_types"] == ["card"]
    assert params["metadata"] == params["payment_intent_data"]["metadata"] == {"attempt_id": "12", "registration_id": "34"}
    assert "application_fee_amount" not in params["payment_intent_data"]
    assert "transfer_data" not in params["payment_intent_data"]
    assert result["id"] == "cs_test_example"


@pytest.mark.parametrize("amount", [1, 49, -1, True, 500.5, 999901])
def test_checkout_rejects_invalid_amount_before_network(configured_app, sdk, amount):
    with configured_app.app_context(), pytest.raises(gateway.PaymentProviderError):
        gateway.create_checkout(**checkout_args(amount_cents=amount))
    sdk.v1.checkout.sessions.create.assert_not_called()


@pytest.mark.parametrize("url", ["https://evil.example/", "//evil.example/", "https://broncos.example@evil.example/", "https://broncos.example/\n", "https://broncos.example\\@evil.example/"])
def test_checkout_rejects_untrusted_callback(configured_app, sdk, url):
    with configured_app.app_context(), pytest.raises(gateway.PaymentProviderError):
        gateway.create_checkout(**checkout_args(success_url=url))
    sdk.v1.checkout.sessions.create.assert_not_called()


@pytest.mark.parametrize("url", ["http://checkout.stripe.com/pay", "https://checkout.stripe.com.evil.example/pay", "https://evil.example/pay", "https://user@checkout.stripe.com/pay", "https://checkout.stripe.com:444/pay"])
def test_checkout_rejects_unexpected_provider_redirect(configured_app, sdk, url):
    sdk.v1.checkout.sessions.create.return_value["url"] = url
    with configured_app.app_context(), pytest.raises(gateway.PaymentProviderError):
        gateway.create_checkout(**checkout_args())


def test_retries_keep_original_expiry_after_first_minute(configured_app, sdk):
    expiry = datetime.now(timezone.utc) + timedelta(minutes=10)
    with configured_app.app_context():
        gateway.create_checkout(**checkout_args(expires_at=expiry))
    assert sdk.v1.checkout.sessions.create.call_args.args[0]["expires_at"] == int(expiry.timestamp())


def test_retrieve_expire_refund_stay_on_original_connected_account(configured_app, sdk):
    with configured_app.app_context():
        result = gateway.retrieve_checkout("acct_example", "cs_test_example")
        gateway.expire_checkout("acct_example", "cs_test_example", "expire:12")
        refund = gateway.refund_payment("acct_example", "pi_example", "refund:12")
        assert gateway.retrieve_refund("acct_example", "re_example")["status"] == "succeeded"
    assert result["payment_intent"]["latest_charge"]["amount_refunded"] == 500
    assert sdk.v1.checkout.sessions.retrieve.call_args.args[1] == {"expand": ["payment_intent.latest_charge.refunds"]}
    for method in (sdk.v1.checkout.sessions.retrieve, sdk.v1.checkout.sessions.expire, sdk.v1.refunds.create, sdk.v1.refunds.retrieve):
        assert method.call_args.kwargs["options"]["stripe_account"] == "acct_example"
    assert sdk.v1.refunds.create.call_args.args[0] == {"payment_intent": "pi_example"}
    assert refund["status"] == "pending"


def test_onboarding_uses_v2_organizer_dashboard_stripe_fees_and_losses(configured_app, sdk):
    with configured_app.app_context():
        gateway.create_connected_account("owner@scu.edu", "Campus club", 9, "account:9", "US")
        gateway.create_onboarding_link("acct_example", "onboarding:9")
    params = sdk.v2.core.accounts.create.call_args.args[0]
    assert params["dashboard"] == "full"
    assert params["defaults"]["responsibilities"] == {"fees_collector": "stripe", "losses_collector": "stripe"}
    assert params["configuration"]["merchant"]["capabilities"]["card_payments"]["requested"]
    assert "type" not in params
    assert params["identity"] == {"country": "US"}
    flow = sdk.v2.core.account_links.create.call_args.args[0]["use_case"]["account_onboarding"]
    assert flow["return_url"] == "https://broncos.example/organizer/payments/return"
    assert flow["refresh_url"] == "https://broncos.example/organizer/payments/expired"


def test_readiness_is_live_verified_and_capability_revocation_disables_it(configured_app, sdk):
    with configured_app.app_context():
        user_id = add_account()
        account = gateway.payment_account_for(user_id)
        assert account.ready and account.checked_at
        sdk.v2.core.accounts.retrieve.return_value["configuration"]["merchant"]["capabilities"]["card_payments"]["status"] = "restricted"
        assert gateway.payment_account_for(user_id) is None
        assert not db.session.scalar(db.select(ConnectedAccount)).charges_enabled


@pytest.mark.parametrize("changes", [
    {"livemode": True}, {"dashboard": "express"}, {"requirements": None},
    {"requirements": {"summary": {"minimum_deadline": {"status": "currently_due"}}}},
    {"defaults": {"responsibilities": {"fees_collector": "application", "losses_collector": "stripe"}}},
])
def test_wrong_mode_configuration_or_incomplete_details_block_checkout(configured_app, sdk, changes):
    sdk.v2.core.accounts.retrieve.return_value = account_data(**changes)
    with configured_app.app_context():
        assert gateway.payment_account_for(add_account()) is None


def test_provider_error_is_safe_retryable_and_disables_cached_ready(configured_app, sdk):
    with configured_app.app_context():
        user_id = add_account()
        assert gateway.payment_account_for(user_id).ready
        sdk.v2.core.accounts.retrieve.side_effect = stripe.APIConnectionError("SECRET provider details")
        with pytest.raises(gateway.PaymentProviderError) as error:
            gateway.payment_account_for(user_id)
        assert error.value.retryable
        assert "SECRET" not in str(error.value)
        assert not db.session.scalar(db.select(ConnectedAccount)).ready


def test_signed_webhook_uses_original_bytes_rejects_tampered_or_expired(configured_app):
    payload = json.dumps({"id": "evt_example", "object": "event", "type": "checkout.session.completed", "data": {"object": {"id": "cs_test_example"}}}).encode()
    def signature(body, timestamp):
        digest = hmac.new(b"whsec_fixture_only", str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()
        return f"t={timestamp},v1={digest}"
    now = int(time.time())
    with configured_app.app_context():
        assert gateway.construct_event(payload, signature(payload, now))["id"] == "evt_example"
        with pytest.raises(gateway.PaymentSignatureError):
            gateway.construct_event(payload + b" ", signature(payload, now))
        with pytest.raises(gateway.PaymentSignatureError):
            gateway.construct_event(payload, signature(payload, now - 301))


def test_client_has_bounded_timeouts_retries_and_version(configured_app, monkeypatch):
    client = Mock()
    http = Mock()
    monkeypatch.setattr(stripe, "StripeClient", client)
    monkeypatch.setattr(stripe, "RequestsClient", http)
    with configured_app.app_context():
        gateway._client()
    assert http.call_args.kwargs["timeout"] == (5, 12)
    assert client.call_args.kwargs["max_network_retries"] == 1
    assert client.call_args.kwargs["stripe_version"] == "2026-10-28.endive"


def test_missing_configuration_makes_no_network_call(app, monkeypatch):
    client = Mock()
    monkeypatch.setattr(stripe, "StripeClient", client)
    with app.app_context(), pytest.raises(gateway.PaymentProviderError):
        gateway._client()
    client.assert_not_called()


def organizer_client(app, user_id=1, role="organizer"):
    with app.app_context():
        db.session.add(User(id=user_id, email=f"user{user_id}@scu.edu", google_sub=f"stripe-user-{user_id}",
                            name=f"Organizer {user_id}", role=role))
        db.session.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["moderation_csrf"] = {"user": str(user_id), "token": "test-csrf"}
    return client


def test_onboarding_routes_allow_students_but_require_login_and_csrf(configured_app, sdk):
    anonymous = configured_app.test_client()
    assert anonymous.get("/organizer/payments").status_code == 302
    student = organizer_client(configured_app, role="student")
    assert student.get("/organizer/payments").status_code == 200
    assert student.post("/organizer/payments/onboard", data={"country": "US"}).status_code == 400
    owner = organizer_client(configured_app, user_id=2)
    assert owner.post("/organizer/payments/onboard").status_code == 400
    assert owner.get("/organizer/payments/onboard").status_code == 405
    sdk.v2.core.accounts.create.assert_not_called()


@pytest.mark.parametrize("role", ["student", "organizer", "admin"])
def test_setup_is_owner_bound_ignores_injected_account_and_does_not_activate(configured_app, sdk, role):
    client = organizer_client(configured_app, role=role)
    with configured_app.app_context():
        db.session.add(User(id=2, email="other@scu.edu", google_sub="stripe-other", name="Other", role="organizer"))
        db.session.add(ConnectedAccount(user_id=2, stripe_account_id="acct_other"))
        db.session.commit()
    response = client.post("/organizer/payments/onboard?account_id=acct_other",
                           data={"csrf_token": "test-csrf", "account_id": "acct_other", "user_id": "2", "country": "US"})
    assert response.status_code == 303
    assert response.location == "https://accounts.stripe.com/r/acct_example#link"
    assert sdk.v2.core.account_links.create.call_args.args[0]["account"] == "acct_example"
    with configured_app.app_context():
        own = db.session.scalar(db.select(ConnectedAccount).where(ConnectedAccount.user_id == 1))
        assert own.stripe_account_id == "acct_example"
        assert not own.ready


def test_return_url_is_not_proof_of_completed_setup(configured_app, sdk):
    client = organizer_client(configured_app)
    with configured_app.app_context():
        db.session.add(ConnectedAccount(user_id=1, stripe_account_id="acct_example"))
        db.session.commit()
    sdk.v2.core.accounts.retrieve.return_value = account_data(configuration={"merchant": {"capabilities": {}}})
    response = client.get("/organizer/payments/return?success=true&charges_enabled=true&account_id=acct_evil",
                          follow_redirects=True)
    assert response.status_code == 200
    assert b"Stripe still needs to finish" in response.data
    assert sdk.v2.core.accounts.retrieve.call_args.args[0] == "acct_example"
    with configured_app.app_context():
        assert not db.session.scalar(db.select(ConnectedAccount)).ready


def test_status_refresh_updates_capabilities_and_expired_link_requires_new_post(configured_app, sdk):
    client = organizer_client(configured_app)
    client.post("/organizer/payments/onboard", data={"csrf_token": "test-csrf", "country": "US"})
    response = client.post("/organizer/payments/refresh", data={"csrf_token": "test-csrf", "country": "US"}, follow_redirects=True)
    assert b"Ready in Stripe test mode" in response.data
    count = sdk.v2.core.account_links.create.call_count
    assert client.get("/organizer/payments/expired").status_code == 302
    assert sdk.v2.core.account_links.create.call_count == count
    sdk.v2.core.accounts.retrieve.return_value["configuration"]["merchant"]["capabilities"]["stripe_balance"]["payouts"]["status"] = "restricted"
    client.post("/organizer/payments/refresh", data={"csrf_token": "test-csrf", "country": "US"})
    with configured_app.app_context():
        assert not db.session.scalar(db.select(ConnectedAccount)).ready


def test_creation_timeout_retry_uses_stable_key_and_snapshot(configured_app, sdk):
    client = organizer_client(configured_app)
    sdk.v2.core.accounts.create.side_effect = stripe.APIConnectionError("private detail")
    assert client.post("/organizer/payments/onboard", data={"csrf_token": "test-csrf", "country": "US"}).status_code == 302
    first = sdk.v2.core.accounts.create.call_args
    with configured_app.app_context():
        db.session.get(User, 1).name = "Renamed organizer"
        db.session.commit()
    sdk.v2.core.accounts.create.side_effect = None
    assert client.post("/organizer/payments/onboard", data={"csrf_token": "test-csrf", "country": "US"}).status_code == 303
    assert sdk.v2.core.accounts.create.call_args == first
    with configured_app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(ConnectedAccount)) == 1
    client.post("/organizer/payments/onboard", data={"csrf_token": "test-csrf", "country": "US"})
    assert sdk.v2.core.accounts.create.call_count == 2


def test_onboarding_and_refresh_release_database_before_network(configured_app, sdk):
    client = organizer_client(configured_app)
    def creation(*args, **kwargs):
        assert not db.session().in_transaction()
        return {"id": "acct_example", "livemode": False}
    def link(*args, **kwargs):
        assert not db.session().in_transaction()
        return {"url": "https://accounts.stripe.com/r/acct_example#link"}
    def retrieve(*args, **kwargs):
        assert not db.session().in_transaction()
        return account_data()
    sdk.v2.core.accounts.create.side_effect = creation
    sdk.v2.core.account_links.create.side_effect = link
    sdk.v2.core.accounts.retrieve.side_effect = retrieve
    assert client.post("/organizer/payments/onboard", data={"csrf_token": "test-csrf", "country": "US"}).status_code == 303
    assert client.post("/organizer/payments/refresh", data={"csrf_token": "test-csrf", "country": "US"}).status_code == 302


def test_setup_page_has_disabled_state_when_provider_not_configured(app, sdk):
    client = organizer_client(app)
    response = client.get("/organizer/payments")
    assert response.status_code == 200
    assert b"Paid tickets aren&#39;t available yet" in response.data or b"Paid tickets aren't available yet" in response.data
    response = client.post("/organizer/payments/onboard", data={"csrf_token": "test-csrf", "country": "US"})
    assert response.status_code == 302
    sdk.v2.core.accounts.create.assert_not_called()


def test_provider_onboarding_url_rejects_foreign_host(configured_app, sdk):
    client = organizer_client(configured_app)
    sdk.v2.core.account_links.create.return_value = {"url": "https://accounts.stripe.com.attacker.example/"}
    response = client.post("/organizer/payments/onboard", data={"csrf_token": "test-csrf", "country": "US"})
    assert response.status_code == 302
    assert response.location == "/organizer/payments"


@pytest.mark.parametrize("country", [None, "", "CA", "us", "US,CA", "<script>"])
def test_initial_onboarding_requires_explicit_supported_country(configured_app, sdk, country):
    client = organizer_client(configured_app)
    fields = {"csrf_token": "test-csrf"}
    if country is not None:
        fields["country"] = country
    response = client.post("/organizer/payments/onboard", data=fields, follow_redirects=True)
    assert response.status_code == 200
    assert b"Choose United States only if" in response.data
    sdk.v2.core.accounts.create.assert_not_called()
    with configured_app.app_context():
        assert not db.session.scalar(db.select(ConnectedAccount))


def test_account_country_choice_is_blank_not_assumed(configured_app, sdk):
    client = organizer_client(configured_app)
    page = client.get("/organizer/payments").data
    assert b'<option value="">Choose your account country</option>' in page
    assert b'<option value="US">United States</option>' in page
    assert b"not citizenship" in page
    assert b"authorized treasurer" in page


def test_country_cannot_be_tampered_with_after_stripe_account_exists(configured_app, sdk):
    client = organizer_client(configured_app)
    client.post("/organizer/payments/onboard", data={"csrf_token": "test-csrf", "country": "US"})
    count = sdk.v2.core.account_links.create.call_count
    response = client.post("/organizer/payments/onboard", data={"csrf_token": "test-csrf", "country": "CA"}, follow_redirects=True)
    assert b"country cannot be changed here" in response.data
    assert sdk.v2.core.account_links.create.call_count == count


def test_production_never_allows_http_even_for_localhost(configured_app):
    configured_app.config.update(APP_ENV="production", PUBLIC_BASE_URL="http://localhost:5000")
    with configured_app.app_context(), pytest.raises(gateway.PaymentProviderError):
        gateway.trusted_url("/organizer/payments/return")


def test_canonical_callback_does_not_follow_request_host(configured_app):
    with configured_app.test_request_context("/", base_url="https://attacker.example"):
        assert gateway.trusted_url("/organizer/payments/return") == "https://broncos.example/organizer/payments/return"


@pytest.mark.parametrize("envelope", [[], 5, "text", None, {}, {"object": "event"}])
def test_malformed_signed_envelope_is_rejected_without_server_error(configured_app, envelope):
    payload = json.dumps(envelope).encode()
    now = int(time.time())
    signature = hmac.new(b"whsec_fixture_only", str(now).encode() + b"." + payload, hashlib.sha256).hexdigest()
    with configured_app.app_context(), pytest.raises(gateway.PaymentSignatureError):
        gateway.construct_event(payload, f"t={now},v1={signature}")


def test_real_sdk_serializes_v2_account_and_direct_checkout_without_network(configured_app, monkeypatch):
    from urllib.parse import parse_qs
    class LocalHTTP(stripe.HTTPClient):
        name = "local-contract-test"
        def __init__(self):
            super().__init__()
            self.calls = []
        def request(self, method, url, headers, post_data=None, **kwargs):
            self.calls.append((method, url, dict(headers), post_data))
            result = (dict(account_data(), object="v2.core.account") if "/v2/core/accounts" in url
                      else {"id": "cs_test_contract", "object": "checkout.session",
                            "url": "https://checkout.stripe.com/c/pay/contract"})
            return json.dumps(result), 200, {}
    transport = LocalHTTP()
    actual_sdk = stripe.StripeClient("sk_test_local_contract_only", stripe_version="2026-10-28.endive",
                                     http_client=transport, max_network_retries=0)
    monkeypatch.setattr(gateway, "_client", lambda: actual_sdk)
    with configured_app.app_context():
        account = gateway.create_connected_account("owner@scu.edu", "Campus club", 9, "owner:9", "US")
        checkout = gateway.create_checkout(**checkout_args())
    assert account["id"] == "acct_example"
    assert checkout["id"] == "cs_test_contract"
    create_account, create_checkout = transport.calls
    assert create_account[1] == "https://api.stripe.com/v2/core/accounts"
    assert json.loads(create_account[3])["identity"] == {"country": "US"}
    headers = {key.lower(): value for key, value in create_checkout[2].items()}
    assert headers["stripe-account"] == "acct_example"
    assert headers["idempotency-key"] == "checkout:12"
    assert headers["stripe-version"] == "2026-10-28.endive"
    form = parse_qs(create_checkout[3])
    assert form["line_items[0][price_data][unit_amount]"] == ["500"]
    assert form["payment_intent_data[metadata][attempt_id]"] == ["12"]
    assert not any("application_fee" in key or "transfer_data" in key for key in form)


def test_expanded_refund_history_collects_remaining_pages_in_same_account(configured_app, sdk):
    first = [{"id": f"re_{index}", "amount": 25, "status": "succeeded"} for index in range(10)]
    remaining = [{"id": f"re_{index}", "amount": 25, "status": "succeeded"} for index in range(10, 20)]
    charge = {"id": "ch_example", "amount": 500, "amount_refunded": 500,
              "refunds": {"data": first, "has_more": True}}
    sdk.v1.checkout.sessions.retrieve.return_value = {"payment_intent": {"latest_charge": charge}}
    sdk.v1.refunds.list.return_value = {"data": remaining, "has_more": False}
    with configured_app.app_context():
        result = gateway.retrieve_checkout("acct_example", "cs_example")
    history = result["payment_intent"]["latest_charge"]["refunds"]
    assert history["has_more"] is False
    assert sum(item["amount"] for item in history["data"]) == 500
    assert sdk.v1.refunds.list.call_args.args[0] == {"charge": "ch_example", "limit": 100, "starting_after": "re_9"}
    assert sdk.v1.refunds.list.call_args.kwargs["options"] == {"stripe_account": "acct_example"}


def test_refund_pagination_is_bounded_and_never_claims_incomplete_history(configured_app, sdk):
    charge = {"id": "ch_example", "refunds": {"data": [{"id": "re_0"}], "has_more": True}}
    sdk.v1.checkout.sessions.retrieve.return_value = {"payment_intent": {"latest_charge": charge}}
    sdk.v1.refunds.list.side_effect = [{"data": [{"id": f"re_{index}"}], "has_more": True} for index in range(1, 21)]
    with configured_app.app_context(), pytest.raises(gateway.PaymentProviderError, match="needs review"):
        gateway.retrieve_checkout("acct_example", "cs_example")
    assert sdk.v1.refunds.list.call_count == 20
    assert charge["refunds"]["has_more"] is True


def test_refund_pagination_rejects_repeated_cursor(configured_app, sdk):
    charge = {"id": "ch_example", "refunds": {"data": [{"id": "re_0"}], "has_more": True}}
    sdk.v1.checkout.sessions.retrieve.return_value = {"payment_intent": {"latest_charge": charge}}
    sdk.v1.refunds.list.return_value = {"data": [{"id": "re_0"}], "has_more": True}
    with configured_app.app_context(), pytest.raises(gateway.PaymentProviderError):
        gateway.retrieve_checkout("acct_example", "cs_example")
    assert sdk.v1.refunds.list.call_count == 1
