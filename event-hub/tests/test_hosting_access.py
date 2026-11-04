"""SCU students can host their own events without gaining admin privileges."""

from datetime import datetime
from unittest.mock import Mock

import pytest

from app.extensions import db
from app.models import ConnectedAccount, Event, Report, User
from app.services import stripe_gateway


def event_form(**changes):
    fields = {
        "csrf_token": "host-token", "title": "Study together",
        "description": "An afternoon study group.", "category": "Social",
        "location": "Learning Commons", "start_time": "2050-11-01T15:00",
        "end_time": "2050-11-01T17:00", "price": "0", "capacity": "12",
    }
    fields.update(changes)
    return fields


def add_user(name="Alex", **changes):
    fields = dict(name=name, email=f"{name.lower()}@scu.edu",
                  google_sub=f"hosting-{name}", role="student")
    fields.update(changes)
    row = User(**fields)
    db.session.add(row)
    db.session.flush()
    return row


def add_event(owner):
    row = Event(organizer=owner, title="Owner's study group", description="Study together.",
                category="Social", location="Library", price_cents=0, capacity=12,
                start_time=datetime(2050, 11, 1, 15), end_time=datetime(2050, 11, 1, 17),
                status="published")
    db.session.add(row)
    db.session.flush()
    return row


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["moderation_csrf"] = {"user": str(user_id), "token": "host-token"}


def test_new_google_student_can_host_the_full_free_event_flow(app, client, monkeypatch):
    monkeypatch.setattr("app.routes.auth.fetch_google_claims", lambda: {
        "sub": "new-host-google", "email": "new-host@scu.edu", "hd": "scu.edu",
        "email_verified": True, "name": "New Host", "role": "admin",
    })
    assert client.get("/auth/google/callback").status_code == 302
    with client.session_transaction() as session:
        token, user_id = session["moderation_csrf"]["token"], int(session["_user_id"])
    assert client.get("/organizer/").status_code == 200
    assert client.get("/organizer/events/new").status_code == 200

    response = client.post("/organizer/events/new", data=event_form(
        csrf_token=token, organizer_id="999", role="admin",
    ))
    assert response.status_code == 302
    with app.app_context():
        event = db.session.scalar(db.select(Event))
        assert event.organizer_id == user_id and event.status == "published"
        event_id = event.id
        assert db.session.get(User, user_id).role == "student"

    assert client.get(f"/organizer/events/{event_id}/stats").status_code == 200
    assert client.get(f"/organizer/events/{event_id}/edit").status_code == 200
    assert client.post(f"/organizer/events/{event_id}/edit", data=event_form(
        csrf_token=token, title="Updated study group", organizer_id="999", role="admin",
    )).status_code == 302
    with app.app_context():
        event = db.session.get(Event, event_id)
        assert event.title == "Updated study group" and event.organizer_id == user_id

    assert client.post(f"/organizer/events/{event_id}/cancel", data={"csrf_token": token}).status_code == 302
    with app.app_context():
        assert db.session.get(Event, event_id).status == "cancelled"
        assert db.session.get(User, user_id).role == "student"
    assert client.get("/admin/reports?role=admin").status_code == 403


@pytest.mark.parametrize("method,path", [
    ("get", "/organizer/"), ("get", "/organizer/events/new"),
    ("post", "/organizer/events/new"), ("get", "/organizer/payments"),
    ("post", "/organizer/payments/onboard"),
])
def test_guests_must_sign_in_before_hosting(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 302 and "/login" in response.location


@pytest.mark.parametrize("changes", [
    {"email": "alex@gmail.com"}, {"email": "alex@scu.edu.attacker.example"},
    {"email": "alex@attacker.example@scu.edu"}, {"google_sub": ""},
    {"google_sub": "   "}, {"role": "unrecognized"},
])
def test_non_scu_unlinked_or_unknown_accounts_cannot_host(app, client, changes):
    with app.app_context():
        user = add_user(**changes)
        db.session.commit()
        user_id = user.id
    sign_in(client, user_id)
    assert client.get("/organizer/").status_code == 403
    assert client.post("/organizer/events/new", data=event_form()).status_code == 403
    assert client.get("/organizer/payments").status_code == 403
    assert client.post("/organizer/payments/onboard", data={
        "csrf_token": "host-token", "country": "US",
    }).status_code == 403
    with app.app_context():
        assert db.session.scalar(db.select(Event)) is None
        assert db.session.scalar(db.select(ConnectedAccount)) is None


@pytest.mark.parametrize("role", ["student", "organizer", "admin"])
def test_all_hosts_including_admins_must_own_events_to_manage_them(app, client, role):
    with app.app_context():
        owner = add_user("Owner")
        actor = add_user("Other", role=role)
        event = add_event(owner)
        db.session.commit()
        actor_id, owner_id, event_id = actor.id, owner.id, event.id
    sign_in(client, actor_id)

    for page in ("edit", "stats"):
        assert client.get(f"/organizer/events/{event_id}/{page}?role=admin").status_code == 404
    assert client.post(f"/organizer/events/{event_id}/edit", data=event_form(
        title="Stolen event", organizer_id=str(actor_id), role="admin",
    )).status_code == 404
    assert client.post(f"/organizer/events/{event_id}/cancel", data={
        "csrf_token": "host-token", "organizer_id": actor_id,
    }).status_code == 404
    with app.app_context():
        event = db.session.get(Event, event_id)
        assert (event.organizer_id, event.title, event.status) == (owner_id, "Owner's study group", "published")


def test_student_host_cannot_review_or_remove_reported_events(app, client):
    with app.app_context():
        owner, reporter = add_user("Owner"), add_user("Reporter")
        event = add_event(owner)
        report = Report(event=event, reporter=reporter, reason="spam", details="Repeated listing.")
        db.session.add(report)
        db.session.commit()
        owner_id, event_id, report_id = owner.id, event.id, report.id
    sign_in(client, owner_id)
    assert client.get("/organizer/").status_code == 200
    for path in ("/admin/reports", f"/admin/reports/{report_id}"):
        assert client.get(path + "?role=admin").status_code == 403
    for action in ("dismiss", "remove"):
        assert client.post(f"/admin/reports/{report_id}/{action}", data={
            "csrf_token": "host-token", "role": "admin", "review_note": "Forged admin action.",
        }).status_code == 403
    with app.app_context():
        assert db.session.get(Report, report_id).status == "pending"
        assert db.session.get(Event, event_id).status == "published"


@pytest.mark.parametrize("role", ["student", "organizer", "admin"])
def test_host_navigation_is_available_without_exposing_admin_links(app, client, role):
    with app.app_context():
        user = add_user(role=role)
        db.session.commit()
        user_id = user.id
    sign_in(client, user_id)
    for path in ("/", "/profile"):
        page = client.get(path)
        assert page.status_code == 200
        assert b'href="/organizer/"' in page.data
        assert (b'href="/admin/reports"' in page.data) is (role == "admin")


def stripe_account_data():
    return dict(id="acct_student", livemode=False, dashboard="full",
                defaults={"responsibilities": {"fees_collector": "stripe", "losses_collector": "stripe"}},
                configuration={"merchant": {"capabilities": {
                    "card_payments": {"status": "active"},
                    "stripe_balance": {"payouts": {"status": "active"}},
                }}}, requirements={"summary": {"minimum_deadline": None}})


@pytest.mark.parametrize("state", ["missing", "incomplete", "wrong_mode", "unavailable", "ready"])
def test_student_paid_create_and_edit_require_verified_stripe_readiness(app, client, monkeypatch, state):
    app.config.update(PAYMENT_PROVIDER="stripe", STRIPE_SECRET_KEY="sk_test_fixture_only",
                      STRIPE_WEBHOOK_SECRET="whsec_fixture_only", STRIPE_LIVE_MODE=False,
                      PUBLIC_BASE_URL="https://broncos.example")
    with app.app_context():
        owner = add_user()
        free = add_event(owner)
        if state != "missing":
            # Even a stale ready flag must be refreshed from the provider.
            db.session.add(ConnectedAccount(user=owner, stripe_account_id="acct_student",
                                           configuration_verified=True, charges_enabled=True,
                                           payouts_enabled=True, details_submitted=True))
        db.session.commit()
        owner_id, free_id = owner.id, free.id
    data = stripe_account_data()
    if state == "incomplete":
        data["configuration"]["merchant"]["capabilities"]["card_payments"]["status"] = "pending"
    if state == "wrong_mode":
        data["livemode"] = True
    provider = Mock(return_value=data)
    if state == "unavailable":
        provider.side_effect = stripe_gateway.PaymentProviderError()
    monkeypatch.setattr(stripe_gateway, "retrieve_connected_account", provider)
    sign_in(client, owner_id)

    expected = 302 if state == "ready" else 422
    for path in ("/organizer/events/new", f"/organizer/events/{free_id}/edit"):
        response = client.post(path, data=event_form(price="5", title="Paid study group"))
        assert response.status_code == expected
    with app.app_context():
        events = db.session.scalars(db.select(Event).order_by(Event.id)).all()
        assert len(events) == (2 if state == "ready" else 1)
        assert events[0].price_cents == (500 if state == "ready" else 0)
        assert events[0].title == ("Paid study group" if state == "ready" else "Owner's study group")
        assert all(event.organizer_id == owner_id for event in events)
        assert db.session.get(User, owner_id).role == "student"
    if state == "missing":
        provider.assert_not_called()
    else:
        assert provider.call_count == 2
        provider.assert_called_with("acct_student")
