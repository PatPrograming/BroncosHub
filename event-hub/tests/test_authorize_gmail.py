"""Local sender setup boundaries. No Google authorization or email is sent."""
import json
import logging
from types import SimpleNamespace

import pytest
import requests

from tools import authorize_gmail as setup


@pytest.fixture
def client_config():
    return {"installed": {
        "client_id": "12345678-testclient.apps.googleusercontent.com",
        "client_secret": "fictional-client-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }}


@pytest.fixture
def client_file(tmp_path, client_config):
    path = tmp_path / "google-client.json"
    path.write_text(json.dumps(client_config))
    return path


@pytest.mark.parametrize("field,value", [
    ("auth_uri", "https://accounts.google.com.evil.example/o/oauth2/auth"),
    ("auth_uri", "http://accounts.google.com/o/oauth2/auth"),
    ("auth_uri", "https://accounts.google.com/o/oauth2/auth?redirect=evil"),
    ("token_uri", "https://attacker.example/token"),
    ("token_uri", "http://oauth2.googleapis.com/token"),
    ("client_id", "client\r\nInjected: yes"),
    ("client_secret", "secret with spaces"),
])
def test_rejects_untrusted_client_settings(client_file, client_config, field, value):
    client_config["installed"][field] = value
    client_file.write_text(json.dumps(client_config))
    with pytest.raises(setup.SetupError, match="unchanged Google Desktop") as error:
        setup.load_client_config(client_file)
    assert value not in str(error.value)


def test_desktop_only_and_bounded_input(client_file, client_config):
    client_file.write_text(json.dumps({"web": client_config["installed"]}))
    with pytest.raises(setup.SetupError):
        setup.load_client_config(client_file)
    client_config["web"] = client_config["installed"]
    client_file.write_text(json.dumps(client_config))
    with pytest.raises(setup.SetupError):
        setup.load_client_config(client_file)
    client_file.write_text(" " * 16385)
    with pytest.raises(setup.SetupError):
        setup.load_client_config(client_file)


def test_sanitizes_downloaded_client_config(client_file):
    client = setup.load_client_config(client_file)["installed"]
    assert set(client) == {"client_id", "client_secret", "auth_uri", "token_uri"}


@pytest.mark.parametrize("address", [
    "outside@example.org", "good@gmail.com\r\nBcc: other@example.org",
    "Name <good@gmail.com>", "first..last@gmail.com", "name+tag@gmail.com",
])
def test_requires_plain_gmail_sender(address):
    with pytest.raises(setup.SetupError):
        setup.gmail_address(address)


def test_output_private_atomic_and_no_overwrite(tmp_path):
    output = tmp_path / "instance" / "deployment" / "gmail-sender.json"
    values = {"GMAIL_REFRESH_TOKEN": "fictional-refresh-token"}
    with setup.output_destination(output, app_root=tmp_path) as (directory, filename):
        assert not output.exists()
        setup.save_credentials(directory, filename, values)
    assert json.loads(output.read_text()) == values
    assert output.stat().st_mode & 0o777 == 0o600
    assert sorted(path.name for path in output.parent.iterdir()) == [output.name]
    with pytest.raises(setup.SetupError, match="already exists"):
        with setup.output_destination(output, app_root=tmp_path):
            pytest.fail("Must not authorize or overwrite an existing file")
    assert json.loads(output.read_text()) == values


def test_rejects_output_outside_ignored_folder(tmp_path):
    with pytest.raises(setup.SetupError, match="instance/deployment"):
        with setup.output_destination(tmp_path / "credentials.json", app_root=tmp_path):
            pytest.fail("Unsafe location accepted")
    assert not (tmp_path / "credentials.json").exists()


@pytest.mark.parametrize("hop", ["instance", "deployment", "file"])
def test_rejects_output_symlinks(tmp_path, hop):
    root = tmp_path / "app"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    instance = root / "instance"
    output = instance / "deployment" / "gmail-sender.json"
    if hop == "instance":
        instance.symlink_to(outside, target_is_directory=True)
    elif hop == "deployment":
        instance.mkdir()
        (instance / "deployment").symlink_to(outside, target_is_directory=True)
    else:
        output.parent.mkdir(parents=True)
        output.symlink_to(outside / "not-yet-created.json")
    with pytest.raises(setup.SetupError):
        with setup.output_destination(output, app_root=root):
            pytest.fail("Symlink accepted")
    assert list(outside.iterdir()) == []


def test_racing_output_creation_is_not_replaced(tmp_path):
    output = tmp_path / "instance" / "deployment" / "gmail-sender.json"
    with setup.output_destination(output, app_root=tmp_path) as (directory, filename):
        output.write_text("existing value")
        with pytest.raises(setup.SetupError, match="no existing file was replaced"):
            setup.save_credentials(directory, filename, {"GMAIL_REFRESH_TOKEN": "new secret"})
    assert output.read_text() == "existing value"
    assert sorted(path.name for path in output.parent.iterdir()) == [output.name]


class FakeResponse:
    def __init__(self, body=b'{"access_token":"fake"}', status=200):
        self.body, self.status_code, self.closed = body, status, False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def iter_content(self, chunk_size):
        for index in range(0, len(self.body), chunk_size):
            yield self.body[index:index + chunk_size]


def test_token_transport_fixed_endpoint_timeout_tls_and_no_redirects():
    calls = []
    response = FakeResponse()
    def request(**kwargs):
        calls.append(kwargs)
        return response
    session = SimpleNamespace(request=request, trust_env=True)
    setup.secure_token_transport(session)
    result = session.request("POST", setup.TOKEN_URI, timeout=None, verify=False,
                             allow_redirects=True, data={"code": "fake-code"})
    assert result is response and response.closed
    assert result._content == response.body
    assert session.trust_env is False
    assert calls[0]["timeout"] == (5, 20)
    assert calls[0]["verify"] is True
    assert calls[0]["allow_redirects"] is False
    assert calls[0]["proxies"] == {}
    for url, method in [("https://evil.example/token", "POST"), (setup.TOKEN_URI, "GET")]:
        with pytest.raises(setup.SetupError):
            session.request(method, url)
    assert len(calls) == 1


@pytest.mark.parametrize("status,body", [(302, b"secret redirect"), (401, b"secret error"),
                                         (200, b"s" * 65537)])
def test_token_response_failure_is_bounded_and_sanitized(status, body):
    response = FakeResponse(body, status)
    session = SimpleNamespace(request=lambda **_kwargs: response, trust_env=True)
    setup.secure_token_transport(session)
    with pytest.raises(setup.SetupError) as error:
        session.request("POST", setup.TOKEN_URI)
    assert "secret" not in str(error.value)
    assert response.closed


class FakeFlow:
    def __init__(self, credentials=None, failure=None):
        self.credentials = credentials or SimpleNamespace(
            refresh_token="fictional-refresh-token",
            scopes=[setup.SEND_SCOPE], granted_scopes=[setup.SEND_SCOPE],
        )
        self.failure = failure
        self.closed = False
        self.oauth2session = SimpleNamespace(request=self.no_network, close=self.close)

    def close(self):
        self.closed = True

    @staticmethod
    def no_network(*_args, **_kwargs):
        pytest.fail("No real token exchange should occur")

    def run_local_server(self, **kwargs):
        self.server_options = kwargs
        logging.getLogger("requests_oauthlib").critical("SECRET_TOKEN_MUST_NOT_LOG")
        if self.failure:
            raise self.failure
        return self.credentials


def test_authorization_requests_only_send_with_pkce_and_loopback(client_config, capsys, caplog):
    flow = FakeFlow()
    factory_options = {}
    def factory(config, **kwargs):
        factory_options.update(kwargs)
        return flow
    previous_logging = logging.root.manager.disable
    values = setup.authorize(client_config, "BroncosHub@gmail.com", flow_factory=factory)
    assert factory_options == {"scopes": [setup.SEND_SCOPE], "autogenerate_code_verifier": True}
    assert flow.server_options["host"] == flow.server_options["bind_addr"] == "127.0.0.1"
    assert flow.server_options["port"] == 0
    assert flow.server_options["open_browser"] is False
    assert flow.server_options["timeout_seconds"] == 600
    assert flow.server_options["access_type"] == "offline"
    assert flow.server_options["prompt"] == "consent select_account"
    assert flow.server_options["include_granted_scopes"] == "false"
    assert flow.server_options["login_hint"] == "broncoshub@gmail.com"
    assert flow.closed and flow.oauth2session.trust_env is False
    assert logging.root.manager.disable == previous_logging
    assert values["NOTIFICATION_FROM_EMAIL"] == "broncoshub@gmail.com"
    assert values["GMAIL_REFRESH_TOKEN"] == "fictional-refresh-token"
    output = capsys.readouterr()
    assert "fictional-refresh-token" not in output.out + output.err + caplog.text
    assert "SECRET_TOKEN_MUST_NOT_LOG" not in caplog.text


@pytest.mark.parametrize("attribute,value", [
    ("refresh_token", None), ("refresh_token", "bad\nsecret"),
    ("granted_scopes", [setup.SEND_SCOPE, "https://mail.google.com/"]),
    ("scopes", ["https://mail.google.com/"]),
])
def test_no_missing_refresh_token_or_excess_scopes(client_config, attribute, value):
    flow = FakeFlow()
    setattr(flow.credentials, attribute, value)
    with pytest.raises(setup.SetupError):
        setup.authorize(client_config, "broncoshub@gmail.com", flow_factory=lambda *_args, **_kwargs: flow)
    assert flow.closed


@pytest.mark.parametrize("failure", [TimeoutError("SECRET_CALLBACK_CODE"),
                                     requests.RequestException("SECRET_ACCESS_TOKEN")])
def test_authorization_timeout_and_errors_hide_provider_data(client_config, failure, capsys):
    flow = FakeFlow(failure=failure)
    previous_logging = logging.root.manager.disable
    with pytest.raises(setup.SetupError, match="did not finish") as error:
        setup.authorize(client_config, "broncoshub@gmail.com", flow_factory=lambda *_args, **_kwargs: flow)
    output = capsys.readouterr()
    assert "SECRET_" not in str(error.value) + output.out + output.err
    assert flow.closed
    assert logging.root.manager.disable == previous_logging


def test_cli_bad_download_is_redacted_and_does_not_authorize(tmp_path, monkeypatch, capsys):
    path = tmp_path / "bad.json"
    path.write_text('{"secret": "DO_NOT_PRINT"')
    monkeypatch.setattr(setup, "authorize", lambda *_args: pytest.fail("No authorization expected"))
    assert setup.main(["--client-secrets", str(path), "--sender", "broncoshub@gmail.com"]) == 1
    output = capsys.readouterr()
    assert "DO_NOT_PRINT" not in output.out + output.err
    assert "Desktop OAuth" in output.err
