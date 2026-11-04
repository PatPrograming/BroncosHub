"""URL identifiers, exact prices, and date labels match what users submit."""

from datetime import datetime

import pytest

from app.extensions import db
from app.models import Event, User
from app.services.price_service import parse_price_cents


@pytest.mark.parametrize("identifier", ["0", "-1", "9223372036854775808", "9" * 100])
@pytest.mark.parametrize("path", [
    "/events/{id}", "/events/{id}/register", "/events/{id}/report",
    "/media/events/{id}/cover", "/organizer/events/{id}/edit",
    "/organizer/events/{id}/stats", "/admin/reports/{id}",
    "/my-registrations/{id}", "/registrations/{id}/checkout",
])
def test_out_of_range_route_identifiers_are_not_found(client, identifier, path):
    # Invalid numeric URLs must not reach SQLite as an overflowing integer or
    # redirect to sign-in as if they were real protected records.
    assert client.get(path.format(id=identifier)).status_code == 404


def test_largest_database_identifier_is_safe_to_query(client):
    assert client.get("/events/9223372036854775807").status_code == 404
    assert client.get("/media/events/9223372036854775807/cover").status_code == 404


@pytest.mark.parametrize("value,expected", [
    ("0", 0), ("0.01", 1), (".50", 50), ("1.", 100),
    ("12.50", 1250), (" 5.5 ", 550), ("9999", 999900), ("9999.00", 999900),
])
def test_price_parser_keeps_exact_cents(value, expected):
    assert parse_price_cents(value) == expected


@pytest.mark.parametrize("value", [
    "0.001", "1.005", "1.000", "-1", "NaN", "Infinity", "1e2", "9999.01",
    "10000", "", ".", "1,000", None, "9" * 1000,
])
def test_invalid_or_fractional_cent_prices_are_rejected(value):
    with pytest.raises(ValueError):
        parse_price_cents(value)


@pytest.mark.parametrize("price", ["0.001", "1.005"])
def test_event_form_does_not_silently_round_or_make_an_event_free(app, client, price):
    with app.app_context():
        owner = User(name="Price Owner", email="price-owner@scu.edu", google_sub="price-owner", role="organizer")
        db.session.add(owner)
        db.session.commit()
        owner_id = owner.id
    with client.session_transaction() as session:
        session["_user_id"] = str(owner_id)
        session["_fresh"] = True
        session["moderation_csrf"] = {"user": str(owner_id), "token": "price-token"}
    response = client.post("/organizer/events/new", data={
        "csrf_token": "price-token", "title": "Exact price event", "description": "Campus admission.",
        "location": "Benson", "category": "Tech", "start_time": "2050-11-01T17:00",
        "end_time": "2050-11-01T19:00", "price": price, "capacity": "10",
    })
    assert response.status_code == 422
    assert b"no more than two decimal places" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Event)) == 0


@pytest.mark.parametrize("start,end,dates,times", [
    (datetime(2050, 11, 1, 18), datetime(2050, 11, 2, 9),
     "Nov 1, 2050 – Nov 2, 2050", "Nov 1, 6:00 PM – Nov 2, 9:00 AM"),
    (datetime(2050, 12, 31, 18), datetime(2051, 1, 1, 9),
     "Dec 31, 2050 – Jan 1, 2051", "Dec 31, 6:00 PM – Jan 1, 9:00 AM"),
])
def test_overnight_event_labels_show_end_date_and_year(start, end, dates, times):
    event = Event(id=1, organizer=User(name="Schedule Owner"), title="Overnight event",
                  category="Tech", location="Campus", start_time=start, end_time=end,
                  price_cents=0, capacity=10, status="published")
    view = event.event_view()
    assert view["date_label"] == dates
    assert view["time_label"] == times


def test_single_day_event_keeps_compact_time_label():
    event = Event(id=1, organizer=User(name="Schedule Owner"), title="Afternoon event",
                  category="Tech", location="Campus", start_time=datetime(2050, 11, 1, 14),
                  end_time=datetime(2050, 11, 1, 16), price_cents=0, capacity=10, status="published")
    assert event.event_view()["time_label"] == "2:00 PM – 4:00 PM"
