"""Registration and checkout use Pacific event times and current seat counts."""

from datetime import datetime, timedelta
from html.parser import HTMLParser

import pytest

from app.extensions import db
from app.models import Event, Payment, Registration, Ticket, User
from app.routes import events, registrations
from app.services import payment_service, registration_service


NOW = datetime(2030, 11, 1, 17, 0)  # Naive Pacific time, like stored event times.
CARD = "4242424242424242"


class FormTokens(HTMLParser):
    def __init__(self, response):
        super().__init__()
        self.tokens = []
        self.feed(response.get_data(as_text=True))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name") == "csrf_token":
            self.tokens.append(attrs["value"])


def token_from(client, path):
    response = client.get(path)
    assert response.status_code == 200
    tokens = FormTokens(response).tokens
    assert tokens
    return tokens[0]


@pytest.fixture
def accounts(app, monkeypatch):
    for module in (events, registrations, payment_service, registration_service):
        monkeypatch.setattr(module, "campus_now", lambda: NOW)
    with app.app_context():
        owner = User(name="Boundary Organizer", email="boundary-owner@scu.edu",
                     google_sub="boundary-owner", role="organizer")
        students = [
            User(name=f"Boundary Student {number}", email=f"boundary-{number}@scu.edu",
                 google_sub=f"boundary-{number}")
            for number in range(2)
        ]
        db.session.add_all([owner, *students])
        db.session.commit()
        owner_id, student_ids = owner.id, [student.id for student in students]
    clients = []
    for user_id in student_ids:
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        clients.append(client)
    return owner_id, student_ids, clients


def make_event(owner_id, **changes):
    values = {
        "organizer_id": owner_id, "title": "Boundary event", "description": "A campus event.",
        "location": "Benson", "category": "Social", "status": "published",
        "start_time": NOW + timedelta(minutes=30), "end_time": NOW + timedelta(hours=2),
        "price_cents": 0, "capacity": 1,
    }
    values.update(changes)
    event = Event(**values)
    db.session.add(event)
    db.session.commit()
    return event.id


@pytest.mark.parametrize("seconds_from_end", [-1, 0])
def test_ended_event_hides_registration_and_refuses_direct_post(app, accounts, seconds_from_end):
    owner_id, _, clients = accounts
    student = clients[0]
    with app.app_context():
        active_id = make_event(owner_id)
        ended_id = make_event(owner_id, title="Finished event", start_time=NOW - timedelta(hours=1),
                              end_time=NOW + timedelta(seconds=seconds_from_end))
    # A valid token from an open form simulates a stale tab or direct POST.
    token = token_from(student, f"/events/{active_id}/register")
    for path in (f"/events/{ended_id}", f"/events/{ended_id}/register"):
        response = student.get(path)
        assert response.status_code == 200
        assert b"This event has ended. Registration is closed." in response.data
        assert f'action="/events/{ended_id}/register"'.encode() not in response.data
        assert f'href="/events/{ended_id}/register"'.encode() not in response.data
    refused = student.post(f"/events/{ended_id}/register", data={"csrf_token": token},
                           follow_redirects=True)
    assert refused.status_code == 200
    assert b"This event has ended. Registration is closed." in refused.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Registration)) == 0


def test_event_ending_after_checkout_opens_cannot_charge_or_issue_a_ticket(app, accounts, monkeypatch):
    owner_id, _, clients = accounts
    student = clients[0]
    with app.app_context():
        event_id = make_event(owner_id, price_cents=1500)
    token = token_from(student, f"/events/{event_id}/register")
    registered = student.post(f"/events/{event_id}/register", data={"csrf_token": token})
    checkout_url = registered.headers["Location"]
    token = token_from(student, checkout_url)
    with app.app_context():
        db.session.get(Event, event_id).end_time = NOW
        db.session.commit()
        registration_id = db.session.scalar(db.select(Registration.id))

    def unexpected_charge(*args, **kwargs):
        pytest.fail("An ended event must be rejected before any payment attempt")

    monkeypatch.setattr(payment_service.MockPaymentService, "charge", unexpected_charge)
    for path in (checkout_url, f"/my-registrations/{registration_id}"):
        page = student.get(path)
        assert page.status_code == 200
        assert b"This event has ended. Checkout is closed." in page.data
        assert b'name="card_number"' not in page.data
        assert f'href="{checkout_url}"'.encode() not in page.data
    refused = student.post(checkout_url, data={"csrf_token": token, "card_number": CARD})
    assert refused.status_code == 409
    assert b"This event has ended. Checkout is closed." in refused.data
    assert b'name="card_number"' not in refused.data
    with app.app_context():
        assert db.session.get(Registration, registration_id).status == "pending"
        assert db.session.scalar(db.select(db.func.count()).select_from(Payment)) == 0
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 0


@pytest.mark.parametrize("submitted_token", [None, "not-the-session-token"])
def test_checkout_rejects_missing_or_bad_csrf_without_payment_changes(app, accounts, submitted_token):
    owner_id, _, clients = accounts
    student = clients[0]
    with app.app_context():
        event_id = make_event(owner_id, price_cents=1500)
    token = token_from(student, f"/events/{event_id}/register")
    registered = student.post(f"/events/{event_id}/register", data={"csrf_token": token})
    checkout_url = registered.headers["Location"]
    token_from(student, checkout_url)
    form = {"card_number": CARD}
    if submitted_token is not None:
        form["csrf_token"] = submitted_token
    refused = student.post(checkout_url, data=form)
    assert refused.status_code == 400
    with app.app_context():
        registration = db.session.scalar(db.select(Registration))
        assert registration.status == "pending"
        assert registration.payment is None and registration.ticket is None
        assert db.session.scalar(db.select(db.func.count()).select_from(Payment)) == 0
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 0


def test_two_pending_checkouts_cannot_sequentially_purchase_the_last_seat(app, accounts, monkeypatch):
    owner_id, _, clients = accounts
    with app.app_context():
        event_id = make_event(owner_id, price_cents=1500)
    checkouts = []
    for student in clients:
        token = token_from(student, f"/events/{event_id}/register")
        registered = student.post(f"/events/{event_id}/register", data={"csrf_token": token})
        checkout_url = registered.headers["Location"]
        checkouts.append((checkout_url, token_from(student, checkout_url)))
    with app.app_context():
        assert [r.status for r in db.session.scalars(db.select(Registration))] == ["pending", "pending"]

    calls = []
    original_charge = payment_service.MockPaymentService.charge

    def recorded_charge(service, amount_cents, card_number):
        calls.append(amount_cents)
        return original_charge(service, amount_cents, card_number)

    monkeypatch.setattr(payment_service.MockPaymentService, "charge", recorded_charge)
    first_url, first_token = checkouts[0]
    first = clients[0].post(first_url, data={"csrf_token": first_token, "card_number": CARD},
                            follow_redirects=True)
    assert first.status_code == 200
    assert b"Registration confirmed." in first.data
    second_url, second_token = checkouts[1]
    closed_page = clients[1].get(second_url)
    assert b"fully booked" in closed_page.data
    assert b'name="card_number"' not in closed_page.data
    second = clients[1].post(second_url, data={"csrf_token": second_token, "card_number": CARD})
    assert second.status_code == 409
    assert b"fully booked" in second.data and b"No payment was taken." in second.data
    assert b'name="card_number"' not in second.data
    assert calls == [1500]
    with app.app_context():
        event = db.session.get(Event, event_id)
        assert event.active_registration_count() == 1
        records = db.session.scalars(db.select(Registration).order_by(Registration.id)).all()
        assert [row.status for row in records] == ["confirmed", "pending"]
        assert records[1].payment is None and records[1].ticket is None
        ticket_code = records[0].ticket.code
        assert db.session.scalar(db.select(db.func.count()).select_from(Payment)) == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 1
        # A completed purchase is idempotent even after the event fills and ends.
        event.end_time = NOW
        db.session.commit()
        assert payment_service.complete_purchase(records[0], CARD).id == records[0].id
    repeated = clients[0].post(first_url, data={"csrf_token": first_token, "card_number": CARD},
                               follow_redirects=True)
    assert repeated.status_code == 200
    assert ticket_code.encode() in repeated.data
    assert calls == [1500]
