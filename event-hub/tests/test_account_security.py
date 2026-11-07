"""Account changes, sign-out and local role assignment enforce their boundaries."""

from types import SimpleNamespace

import pytest
from authlib.common.errors import AuthlibBaseError
from requests.exceptions import RequestException

from app.extensions import db
from app.models import User


CLAIMS = {
    "sub": "security-alex", "email": "alex@scu.edu", "email_verified": True,
    "hd": "scu.edu", "name": "Alex Bronco",
}


@pytest.fixture
def google_claims(monkeypatch):
    claims = dict(CLAIMS)
    monkeypatch.setattr("app.routes.auth.fetch_google_claims", lambda: claims)
    return claims


def sign_in(client):
    assert client.get("/auth/google/callback").status_code == 302
    assert client.get("/profile").status_code == 200
    with client.session_transaction() as session:
        return session["moderation_csrf"]["token"]


def saved_user(app):
    with app.app_context():
        row = db.session.scalar(db.select(User))
        return (row.email, row.name, row.role, row.year_level)


def test_logout_requires_post_and_a_valid_token(client, google_claims):
    token = sign_in(client)
    assert client.get("/logout").status_code == 405
    for fields in ({}, {"csrf_token": "wrong"}, {"csrf_token": [token, token]}):
        assert client.post("/logout", data=fields).status_code == 400
        assert client.get("/profile").status_code == 200


def test_login_and_logout_replace_session_data_and_old_form_tokens(client, google_claims):
    with client.session_transaction() as session:
        session["old_account_cache"] = "must not cross sign-in"
        session["auth_next"] = "/my-registrations"
        session["moderation_csrf"] = {"user": None, "token": "old-anonymous-token"}
    response = client.get("/auth/google/callback")
    assert response.location.endswith("/my-registrations")
    with client.session_transaction() as session:
        token = session["moderation_csrf"]["token"]
        assert token != "old-anonymous-token"
        assert "old_account_cache" not in session and "auth_next" not in session
        assert session["moderation_csrf"]["user"] == session["_user_id"]
        session["private_cache"] = "remove on logout"

    assert client.post("/logout", data={"csrf_token": token}).status_code == 302
    assert client.get("/profile").status_code == 302
    with client.session_transaction() as session:
        assert "_user_id" not in session and "private_cache" not in session
        assert session["moderation_csrf"]["user"] is None
        assert session["moderation_csrf"]["token"] != token
    fresh_token = sign_in(client)
    assert fresh_token != token
    assert client.post("/profile", data={
        "csrf_token": token, "name": "Replayed edit", "year_level": "third_year",
    }).status_code == 400


@pytest.mark.parametrize("destination", [
    "https://attacker.example", "//attacker.example", "/%2fattacker.example",
    "/\\attacker.example", "/%5cattacker.example", "/good\nLocation: bad",
    "/good%0d%0aLocation:bad", "javascript:alert(1)", "/" + "x" * 2048,
])
def test_login_does_not_redirect_to_unsafe_destinations(client, google_claims, destination):
    with client.session_transaction() as session:
        session["auth_next"] = destination
    response = client.get("/auth/google/callback")
    assert response.status_code == 302
    assert response.location == "/profile"


@pytest.mark.parametrize("destination", ["/my-registrations", "/events?q=campus%20music#results"])
def test_login_keeps_safe_local_destinations(client, google_claims, destination):
    with client.session_transaction() as session:
        session["auth_next"] = destination
    response = client.get("/auth/google/callback")
    assert response.location == destination


@pytest.mark.parametrize("field,value", [
    ("email", None), ("email", ["alex@scu.edu"]), ("hd", True),
    ("email_verified", "false"), ("sub", 123), ("sub", ""),
])
def test_malformed_identity_claims_do_not_create_accounts(app, client, google_claims, field, value):
    google_claims[field] = value
    response = client.get("/auth/google/callback")
    assert response.status_code == 302
    assert response.location == "/login"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(User)) == 0


def test_callback_without_validated_userinfo_fails_closed(app, client, monkeypatch):
    monkeypatch.setattr("app.routes.auth.oauth", SimpleNamespace(google=SimpleNamespace(
        authorize_access_token=lambda: {"id_token": "not-validated-claims"},
    )))
    response = client.get("/auth/google/callback")
    assert response.status_code == 302
    assert response.location == "/login"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(User)) == 0


@pytest.mark.parametrize("failure", [AuthlibBaseError("invalid_token"), RequestException("provider unavailable")])
def test_sign_in_failures_return_to_login_without_creating_an_account(app, client, monkeypatch, failure):
    def failed_provider(*args, **kwargs):
        raise failure

    monkeypatch.setattr("app.routes.auth.oauth", SimpleNamespace(google=SimpleNamespace(
        authorize_redirect=failed_provider, authorize_access_token=failed_provider,
    )))
    for path in ("/auth/google?next=/my-registrations", "/auth/google/callback"):
        response = client.get(path)
        assert response.status_code == 302
        assert response.location == "/login"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(User)) == 0
    with client.session_transaction() as session:
        assert "auth_next" not in session


def test_google_authorize_uses_public_base_url_when_configured(app, client, monkeypatch):
    captured = {}

    def capture_redirect(redirect_uri, hd=None):
        captured["redirect_uri"] = redirect_uri
        captured["hd"] = hd
        return "redirected", 302

    monkeypatch.setattr("app.routes.auth.oauth", SimpleNamespace(google=SimpleNamespace(
        authorize_redirect=capture_redirect,
    )))
    app.config["PUBLIC_BASE_URL"] = "https://broncoshub.example"
    response = client.get("/auth/google?next=/my-registrations")
    assert response.status_code == 302
    assert captured["redirect_uri"] == "https://broncoshub.example/auth/google/callback"
    assert captured["hd"] == "scu.edu"
    with client.session_transaction() as session:
        assert session["auth_next"] == "/my-registrations"


def test_google_authorize_falls_back_to_request_host_without_public_base(app, client, monkeypatch):
    captured = {}

    def capture_redirect(redirect_uri, hd=None):
        captured["redirect_uri"] = redirect_uri
        return "redirected", 302

    monkeypatch.setattr("app.routes.auth.oauth", SimpleNamespace(google=SimpleNamespace(
        authorize_redirect=capture_redirect,
    )))
    app.config["PUBLIC_BASE_URL"] = ""
    response = client.get("/auth/google", base_url="http://127.0.0.1:5080")
    assert response.status_code == 302
    assert captured["redirect_uri"] == "http://127.0.0.1:5080/auth/google/callback"


@pytest.mark.parametrize("stored_token", [None, 123, [], {"unexpected": "object"}])
def test_malformed_session_csrf_data_fails_cleanly(app, client, google_claims, stored_token):
    token = sign_in(client)
    original = saved_user(app)
    with client.session_transaction() as session:
        session["moderation_csrf"]["token"] = stored_token
        session.modified = True
    assert client.post("/profile", data={
        "csrf_token": token, "name": "Wrong change", "year_level": "third_year",
    }).status_code == 400
    assert saved_user(app) == original
    # Reloading the form replaces broken session data with a usable token.
    assert client.get("/profile").status_code == 200
    with client.session_transaction() as session:
        assert isinstance(session["moderation_csrf"]["token"], str)


@pytest.mark.parametrize("name,year", [
    ("", "third_year"), ("x" * 81, "third_year"),
    ("Alex\nBronco", "third_year"), ("Alex", "admin"),
])
def test_invalid_profile_fields_do_not_change_account(app, client, google_claims, name, year):
    token = sign_in(client)
    original = saved_user(app)
    response = client.post("/profile", data={"csrf_token": token, "name": name, "year_level": year})
    assert response.status_code == 422
    assert saved_user(app) == original


def test_profile_escapes_display_name_and_ignores_identity_or_role_fields(app, client, google_claims):
    google_claims["role"] = "admin"
    token = sign_in(client)
    name = "<script>alert(1)</script>"
    response = client.post("/profile", data={
        "csrf_token": token, "name": name, "year_level": "unknown",
        "role": "admin", "email": "other@scu.edu", "google_sub": "someone-else",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert name.encode() not in response.data
    assert b"&lt;script&gt;" in response.data
    assert saved_user(app) == ("alex@scu.edu", name, "student", None)
    assert client.get("/admin/reports").status_code == 403
    assert client.get("/organizer/").status_code == 200


def test_role_command_requires_confirmation_and_can_grant_and_revoke(app, client, google_claims):
    sign_in(client)
    runner = app.test_cli_runner()
    aborted = runner.invoke(args=["user-role", "alex@scu.edu", "--role", "admin"], input="n\n")
    assert aborted.exit_code != 0
    assert saved_user(app)[2] == "student"
    granted = runner.invoke(args=["user-role", " ALEX@SCU.EDU ", "--role", "admin"], input="y\n")
    assert granted.exit_code == 0, granted.output
    assert "student -> admin" in granted.output
    assert saved_user(app)[2] == "admin"
    assert client.get("/admin/reports").status_code == 200
    revoked = runner.invoke(args=["user-role", "alex@scu.edu", "--role", "student", "--yes"])
    assert revoked.exit_code == 0, revoked.output
    assert saved_user(app)[2] == "student"
    assert client.get("/admin/reports").status_code == 403


@pytest.mark.parametrize("arguments", [
    ["alex@scu.edu"],
    ["alex@scu.edu", "--role", "superadmin", "--yes"],
    ["alex@gmail.com", "--role", "organizer", "--yes"],
    ["missing@scu.edu", "--role", "admin", "--yes"],
])
def test_role_command_rejects_implicit_invalid_or_nonexistent_targets(app, client, google_claims, arguments):
    sign_in(client)
    result = app.test_cli_runner().invoke(args=["user-role", *arguments])
    assert result.exit_code != 0
    assert saved_user(app)[2] == "student"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(User)) == 1


def test_role_command_does_not_create_a_google_identity(app):
    with app.app_context():
        db.session.add(User(name="Unlinked", email="unlinked@scu.edu", google_sub="", role="student"))
        db.session.commit()
    result = app.test_cli_runner().invoke(args=["user-role", "unlinked@scu.edu", "--role", "admin", "--yes"])
    assert result.exit_code != 0
    assert "must sign in first" in result.output
    assert saved_user(app)[2] == "student"


def test_role_command_does_not_overwrite_a_change_during_confirmation(app, client, google_claims, monkeypatch):
    sign_in(client)

    def another_operator_changed_role(*args, **kwargs):
        account = db.session.scalar(db.select(User))
        account.role = "admin"
        db.session.commit()
        return True

    monkeypatch.setattr("app.commands.click.confirm", another_operator_changed_role)
    result = app.test_cli_runner().invoke(args=["user-role", "alex@scu.edu", "--role", "organizer"])
    assert result.exit_code != 0
    assert "account changed while waiting" in result.output
    assert saved_user(app)[2] == "admin"
