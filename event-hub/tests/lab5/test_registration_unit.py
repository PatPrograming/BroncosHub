"""Five direct Registration view tests, without routes or database writes."""

from app.models import Event, Payment, Registration, Ticket


def make_registration(monkeypatch, *, price_cents=1500, status="confirmed"):
    # Event rendering has its own tests; isolate the Registration decision logic.
    monkeypatch.setattr(Event, "event_view", lambda self: {"id": self.id})
    return Registration(
        id=7,
        status=status,
        event=Event(id=3, price_cents=price_cents, status="published"),
    )


def add_paid_ticket(registration):
    registration.payment = Payment(status="succeeded", amount_cents=1500)
    registration.ticket = Ticket(code="BH-UNITTEST", is_valid=True)


def test_confirmed_paid_registration_exposes_valid_ticket(monkeypatch):
    """Normal: R1 - A confirmed signup and successful payment expose the ticket."""
    registration = make_registration(monkeypatch)
    add_paid_ticket(registration)

    view = registration.registration_view()

    assert registration.is_confirmed is True
    assert view["payment_status"] == "succeeded"
    assert view["ticket_valid"] is True
    assert view["ticket_code"] == "BH-UNITTEST"
    assert view["event"] == {"id": 3}


def test_free_registration_does_not_require_payment(monkeypatch):
    """Edge: R2 - A zero-price event requires no payment record."""
    registration = make_registration(monkeypatch, price_cents=0)

    view = registration.registration_view()

    assert view["payment_status"] == "not_required"
    assert view["ticket_valid"] is False
    assert view["ticket_code"] is None


def test_paid_registration_without_payment_is_pending(monkeypatch):
    """Edge: R3 - An absent payment leaves a paid signup pending, with no ticket."""
    registration = make_registration(monkeypatch, status="pending")

    view = registration.registration_view()

    assert registration.is_confirmed is False
    assert view["payment_status"] == "pending"
    assert view["ticket_valid"] is False
    assert view["ticket_code"] is None


def test_declined_payment_is_shown_as_failed(monkeypatch):
    """Invalid: R4 - A declined charge cannot expose an otherwise valid ticket."""
    # Keep other ticket conditions valid so only the declined payment blocks it.
    registration = make_registration(monkeypatch, status="confirmed")
    registration.payment = Payment(status="declined", amount_cents=1500)
    registration.ticket = Ticket(code="BH-UNITTEST", is_valid=True)

    view = registration.registration_view()

    assert view["payment_status"] == "failed"
    assert view["ticket_valid"] is False
    assert view["ticket_code"] is None


def test_cancelled_event_hides_an_otherwise_valid_paid_ticket(monkeypatch):
    """Invalid: R5 - A cancelled event makes an otherwise valid paid ticket unusable."""
    registration = make_registration(monkeypatch)
    add_paid_ticket(registration)
    registration.event.status = "cancelled"

    view = registration.registration_view()

    assert view["payment_status"] == "succeeded"
    assert view["ticket_valid"] is False
    assert view["ticket_code"] is None
