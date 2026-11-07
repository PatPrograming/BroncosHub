"""Support links are useful without exposing organizer emails publicly."""

from datetime import datetime

import pytest

from app.extensions import db
from app.models import Event, Registration, User


@pytest.fixture
def support_case(app):
    # Also permits focused testing before the blueprint is wired into create_app.
    if "support" not in app.blueprints:
        from app.routes.support import support_bp
        app.register_blueprint(support_bp)
    with app.app_context():
        host = User(email="event-host@scu.edu", google_sub="support-host", name="Event Host")
        student = User(email="support-student@scu.edu", google_sub="support-student", name="Student")
        event = Event(
            organizer=host, title="Support test event", description="Event details",
            location="Campus", category="Tech", start_time=datetime(2050, 1, 1, 12),
            end_time=datetime(2050, 1, 1, 14), price_cents=1200, status="published",
        )
        db.session.add_all([host, student, event])
        db.session.flush()
        registration = Registration(student=student, event=event, status="confirmed")
        db.session.add(registration)
        db.session.commit()
        return {"host": host.id, "student": student.id, "event": event.id, "registration": registration.id}


def signed_in(app, user_id):
    browser = app.test_client()
    with browser.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return browser


def test_support_and_privacy_are_public_and_use_selected_support_address(client, support_case):
    support = client.get("/support")
    privacy = client.get("/privacy")
    terms = client.get("/terms")
    assert support.status_code == privacy.status_code == terms.status_code == 200
    assert b'mailto:ecan@scu.edu' in support.data
    assert b'mailto:ecan@scu.edu' in privacy.data
    assert b"queued for refunds" in support.data
    assert b"reviewed manually" in privacy.data
    assert b"openid" in privacy.data
    assert b"Google API Services User Data Policy" in privacy.data
    assert b"Terms of service" in terms.data
    assert b'aria-label="Help and privacy"' in client.get("/").data


def test_home_names_broncoshub_links_privacy_and_allows_browsing_without_login(client, support_case):
    home = client.get("/")
    assert home.status_code == 200
    assert b"BroncosHub" in home.data
    assert b"Campus events for Santa Clara University students" in home.data
    assert b"without signing in" in home.data
    assert b'href="/privacy"' in home.data
    assert b'href="/terms"' in home.data
    assert b'name="google-site-verification"' not in home.data


def test_google_site_verification_meta_when_configured(app, client, support_case):
    app.config["GOOGLE_SITE_VERIFICATION"] = "test-verification-token"
    home = client.get("/")
    assert b'name="google-site-verification"' in home.data
    assert b"test-verification-token" in home.data


def test_public_event_offers_contact_without_exposing_organizer_email(client, support_case):
    response = client.get(f"/events/{support_case['event']}")
    assert response.status_code == 200
    assert f"/events/{support_case['event']}/contact".encode() in response.data
    assert b"refund eligibility before buying" in response.data
    assert b"event-host@scu.edu" not in response.data
    contact = client.get(f"/events/{support_case['event']}/contact")
    assert contact.status_code == 302 and "/login" in contact.location
    assert b"event-host@scu.edu" not in contact.data


@pytest.mark.parametrize("status", ["published", "cancelled"])
def test_verified_scu_user_can_contact_current_event_organizer(app, support_case, status):
    with app.app_context():
        event = db.session.get(Event, support_case["event"])
        event.status = status
        # Contact comes from the current account rather than a stale event DTO.
        event.organizer.email = "updated-host@scu.edu"
        db.session.commit()
    browser = signed_in(app, support_case["student"])
    response = browser.get(f"/events/{support_case['event']}/contact")
    assert response.status_code == 200
    assert b'mailto:updated-host@scu.edu' in response.data
    assert b"event-host@scu.edu" not in response.data
    assert b"support-student@scu.edu" not in response.data


@pytest.mark.parametrize("email,google_sub", [
    ("outsider@gmail.com", "linked"), ("unlinked@scu.edu", ""),
])
def test_unlinked_and_outside_accounts_cannot_see_contacts(app, support_case, email, google_sub):
    with app.app_context():
        student = db.session.get(User, support_case["student"])
        student.email, student.google_sub = email, google_sub
        db.session.commit()
    response = signed_in(app, support_case["student"]).get(f"/events/{support_case['event']}/contact")
    assert response.status_code == 403
    assert b"event-host@scu.edu" not in response.data


@pytest.mark.parametrize("status", ["removed", "draft", "unexpected"])
def test_hidden_events_do_not_expose_contact_even_to_registered_attendee(app, support_case, status):
    with app.app_context():
        db.session.get(Event, support_case["event"]).status = status
        db.session.commit()
    response = signed_in(app, support_case["student"]).get(f"/events/{support_case['event']}/contact")
    assert response.status_code == 404
    assert b"event-host@scu.edu" not in response.data
    assert b"Support test event" not in response.data


def test_missing_event_contact_is_not_found(app, support_case):
    response = signed_in(app, support_case["student"]).get("/events/999999/contact")
    assert response.status_code == 404


def test_removed_registration_has_support_path_without_reexposing_organizer(app, support_case):
    with app.app_context():
        db.session.get(Event, support_case["event"]).status = "removed"
        db.session.get(Registration, support_case["registration"]).status = "cancelled"
        db.session.commit()
    browser = signed_in(app, support_case["student"])
    receipt = browser.get(f"/my-registrations/{support_case['registration']}")
    assert receipt.status_code == 200
    assert b"Contact support" in receipt.data
    assert f"/events/{support_case['event']}/contact".encode() not in receipt.data
    assert b"Event Host" not in receipt.data
    assert b"event-host@scu.edu" not in receipt.data


@pytest.mark.parametrize("email", ["host@scu.edu?cc=other@scu.edu", "host\r\nBcc:other@scu.edu"])
def test_invalid_stored_organizer_address_falls_back_to_support(app, support_case, email):
    with app.app_context():
        db.session.get(User, support_case["host"]).email = email
        db.session.commit()
    response = signed_in(app, support_case["student"]).get(f"/events/{support_case['event']}/contact")
    assert response.status_code == 200
    assert b"contact information is unavailable" in response.data
    assert b'mailto:ecan@scu.edu' in response.data
    assert b"other@scu.edu" not in response.data


def test_mailto_reserved_characters_are_encoded(app, support_case):
    with app.app_context():
        db.session.get(User, support_case["host"]).email = "host+event%tag@scu.edu"
        db.session.commit()
    response = signed_in(app, support_case["student"]).get(f"/events/{support_case['event']}/contact")
    assert b'mailto:host%2Bevent%25tag@scu.edu' in response.data


def test_support_address_can_be_configured_but_invalid_headers_are_rejected(app, client, support_case):
    app.config["SUPPORT_EMAIL"] = "help+events@scu.edu"
    assert b'mailto:help%2Bevents@scu.edu' in client.get("/support").data
    app.config["SUPPORT_EMAIL"] = "help@scu.edu?bcc=someone@example.com"
    response = client.get("/support")
    assert b'mailto:ecan@scu.edu' in response.data
    assert b"someone@example.com" not in response.data
