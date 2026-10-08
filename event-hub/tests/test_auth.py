# tests/test_auth.py
# Tests for sign-up and sign-in. Backlog #1, #2.
from unittest.mock import patch

from app.extensions import db
from app.models.user import User

ALEX = {
    "sub": "google-alex",
    "email": "Alex@scu.edu",
    "email_verified": True,
    "hd": "scu.edu",
    "name": "Alex Bronco",
}


def user_count():
    return db.session.scalar(db.select(db.func.count()).select_from(User))


@patch("app.routes.auth.fetch_google_claims")
def test_google_signup_then_returning_login(mock_claims, app, client):
    mock_claims.return_value = ALEX

    first = client.get("/auth/google/callback", follow_redirects=False)
    assert first.status_code == 302
    assert first.location.endswith("/profile")

    with app.app_context():
        user = db.session.scalar(db.select(User))
        assert user_count() == 1
        assert user.email == "alex@scu.edu"
        assert user.google_sub == "google-alex"
        assert user.role == "student"
        assert user.name == "Alex Bronco"
        created_id = user.id

    profile = client.get("/profile")
    assert profile.status_code == 200
    assert b"alex@scu.edu" in profile.data

    second = client.get("/auth/google/callback", follow_redirects=False)
    assert second.status_code == 302
    with app.app_context():
        assert user_count() == 1
        again = db.session.scalar(db.select(User))
        assert again.id == created_id


@patch("app.routes.auth.fetch_google_claims")
def test_google_rejects_non_scu_unverified_and_wrong_domain(mock_claims, app, client):
    rejected = [
        {"sub": "gmail-user", "email": "alex@gmail.com", "email_verified": True, "hd": "gmail.com", "name": "Alex"},
        {"sub": "unverified", "email": "alex@scu.edu", "email_verified": False, "hd": "scu.edu", "name": "Alex"},
        {"sub": "wrong-hd", "email": "alex@scu.edu", "email_verified": True, "hd": "gmail.com", "name": "Alex"},
    ]
    for claims in rejected:
        mock_claims.return_value = claims
        response = client.get("/auth/google/callback", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.location
        with app.app_context():
            assert user_count() == 0
        assert client.get("/profile", follow_redirects=False).status_code == 302


@patch("app.routes.auth.fetch_google_claims")
def test_logout_ends_the_session(mock_claims, client):
    mock_claims.return_value = ALEX
    client.get("/auth/google/callback")
    assert client.get("/profile").status_code == 200

    logged_out = client.get("/logout", follow_redirects=False)
    assert logged_out.status_code == 302
    profile = client.get("/profile", follow_redirects=False)
    assert profile.status_code == 302
    assert "/login" in profile.location
