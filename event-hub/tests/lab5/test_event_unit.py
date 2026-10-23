"""Five direct Event unit tests; all model instances stay in memory."""

from datetime import datetime

from app.models import Event, Registration, User


def make_event(**overrides):
    values = {
        "id": 1,
        "organizer": User(name="Campus Club"),
        "title": "Campus concert",
        "description": "A fictional event for testing.",
        "location": "Test hall",
        "category": "Arts & Culture",
        "start_time": datetime(2050, 10, 22, 18),
        "end_time": datetime(2050, 10, 22, 20),
        "price_cents": 1500,
        "capacity": 10,
        "status": "published",
    }
    values.update(overrides)
    return Event(**values)


def test_paid_event_displays_price_in_dollars():
    """Normal: E1 - A 1500-cent event is paid and displays $15.00."""
    event = make_event()

    view = event.event_view()

    assert event.is_free is False
    assert view["price_label"] == "$15.00"
    assert view["organizer"] == "Campus Club"


def test_only_confirmed_registrations_count_toward_capacity():
    """Normal: E2 - Pending, failed and cancelled signups do not use seats."""
    event = make_event(capacity=10)
    event.registrations = [
        Registration(status="confirmed"),
        Registration(status="confirmed"),
        Registration(status="pending"),
        Registration(status="failed"),
        Registration(status="cancelled"),
    ]

    assert event.active_registration_count() == 2
    assert event.spots_left() == 8


def test_unlimited_event_has_no_numeric_seat_limit():
    """Edge: E3 - None capacity means unlimited, not zero available seats."""
    event = make_event(capacity=None)

    assert event.spots_left() is None
    assert "spots_left" not in event.event_view()


def test_exactly_full_event_has_zero_spots_left():
    """Edge: E4 - One confirmed signup fills an event with capacity one."""
    event = make_event(capacity=1)
    event.registrations = [Registration(status="confirmed")]

    assert event.spots_left() == 0


def test_over_capacity_data_never_displays_negative_spots():
    """Invalid: E5 - Over-capacity data displays zero rather than negative seats."""
    event = make_event(capacity=1)
    event.registrations = [
        Registration(status="confirmed"),
        Registration(status="confirmed"),
    ]

    assert event.active_registration_count() == 2
    assert event.spots_left() == 0
