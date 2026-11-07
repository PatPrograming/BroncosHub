"""Authorize one Gmail sender without granting inbox access or sending mail.

Run from event-hub after installing tools/requirements-gmail-setup.txt. Use a
separate Google Desktop OAuth client, not the website's Google sign-in client.
The operator opens the printed consent URL and selects the intended account.
"""
import argparse
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import re
import sys
from uuid import uuid4


APP_ROOT = Path(__file__).resolve().parents[1]
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URIS = {
    "https://accounts.google.com/o/oauth2/auth",
    "https://accounts.google.com/o/oauth2/v2/auth",
}
DEFAULT_OUTPUT = APP_ROOT / "instance" / "deployment" / "gmail-sender.json"


class SetupError(Exception):
    """A fixed, safe message suitable for the operator; never provider details."""


def gmail_address(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9.]{0,62}[A-Za-z0-9])?@gmail\.com", value
    ) or ".." in value:
        raise SetupError("Use the dedicated account's ordinary @gmail.com address.")
    return value.lower()


def _secret(value, maximum=4096):
    return isinstance(value, str) and 8 <= len(value) <= maximum and all(
        33 <= ord(char) <= 126 for char in value
    )


def load_client_config(path):
    try:
        with Path(path).open("rb") as source:
            raw = source.read(16385)
        if len(raw) > 16384:
            raise ValueError
        config = json.loads(raw)
        client = config.get("installed") if isinstance(config, dict) else None
        if not isinstance(client, dict) or "web" in config:
            raise ValueError
        if client.get("auth_uri") not in AUTH_URIS or client.get("token_uri") != TOKEN_URI:
            raise ValueError
        identifier = client.get("client_id")
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{8,200}\.apps\.googleusercontent\.com", identifier
        ) or not _secret(client.get("client_secret"), 1024):
            raise ValueError
    except (OSError, ValueError, TypeError):
        raise SetupError("Use an unchanged Google Desktop OAuth client JSON file.") from None
    # Do not allow downloaded configuration to add redirects, scopes or other
    # endpoints. The library supplies our fresh loopback redirect at runtime.
    return {"installed": {name: client[name] for name in (
        "client_id", "client_secret", "auth_uri", "token_uri",
    )}}


@contextmanager
def output_destination(path, app_root=APP_ROOT):
    """Keep credentials in the ignored directory and reject every symlink hop."""
    path = Path(os.path.abspath(path))
    root = Path(app_root).absolute()
    if path.parent != root / "instance" / "deployment" or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.json", path.name
    ):
        raise SetupError("Save the result directly under event-hub/instance/deployment as a JSON file.")
    handles = []
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent = os.open(root, directory_flags)
        handles.append(parent)
        for name in ("instance", "deployment"):
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
            parent = os.open(name, directory_flags, dir_fd=parent)
            handles.append(parent)
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise SetupError("The output already exists. Choose a new JSON filename; nothing was replaced.")
        yield parent, path.name
    except OSError:
        raise SetupError("The private output folder is unavailable or contains a symlink.") from None
    finally:
        for handle in reversed(handles):
            os.close(handle)


def save_credentials(directory, filename, values):
    """Publish a complete mode-0600 file without replacing an existing name."""
    temporary = ".gmail-" + uuid4().hex + ".tmp"
    created = False
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        created = True
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            os.fchmod(target.fileno(), 0o600)
            json.dump(values, target, indent=2)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        # Unlike replace(), link() fails if the destination appeared meanwhile.
        os.link(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        os.fsync(directory)
    except OSError:
        raise SetupError("Could not save credentials safely; no existing file was replaced.") from None
    finally:
        if created:
            os.unlink(temporary, dir_fd=directory)


def secure_token_transport(session):
    """Bound Google's token exchange and prevent proxy or redirect disclosure."""
    session.trust_env = False
    request = session.request

    def bounded_request(method, url, **kwargs):
        if method.upper() != "POST" or url != TOKEN_URI:
            raise SetupError("Unexpected authorization endpoint; setup stopped.")
        kwargs.update(timeout=(5, 20), allow_redirects=False, verify=True,
                      proxies={}, stream=True)
        with request(method=method, url=url, **kwargs) as response:
            if response.status_code != 200:
                raise SetupError("Google did not complete authorization. Try again after checking the account setup.")
            raw = bytearray()
            for chunk in response.iter_content(4096):
                raw.extend(chunk)
                if len(raw) > 65536:
                    raise SetupError("Google returned an unexpected authorization response.")
            response._content = bytes(raw)
            response._content_consumed = True
            return response

    session.request = bounded_request


def authorize(config, sender, flow_factory=None):
    """Obtain only send permission. No API email send or inbox read occurs."""
    sender = gmail_address(sender)
    if flow_factory is None:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            raise SetupError("Install tools/requirements-gmail-setup.txt in the local environment first.") from None
        flow_factory = InstalledAppFlow.from_client_config
    # OAuth libraries may otherwise log callback codes and token responses at
    # DEBUG/INFO. This is a short standalone setup process, not the Flask server.
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    flow = None
    try:
        flow = flow_factory(config, scopes=[SEND_SCOPE], autogenerate_code_verifier=True)
        secure_token_transport(flow.oauth2session)
        print("Select exactly " + sender + " on Google's consent screen.")
        print("This grants sending only. It does not read your inbox or send a test email.")
        credentials = flow.run_local_server(
            host="127.0.0.1", bind_addr="127.0.0.1", port=0,
            open_browser=False, timeout_seconds=600,
            authorization_prompt_message="Open this temporary Google consent URL:\n{url}",
            success_message="Authorization received. Return to the setup tool to check whether it completed.",
            access_type="offline", prompt="consent select_account", login_hint=sender,
            include_granted_scopes="false",
        )
        if not _secret(credentials.refresh_token):
            raise SetupError("Google did not return an offline token. Reauthorize with consent enabled.")
        granted = credentials.granted_scopes
        if set(credentials.scopes or []) != {SEND_SCOPE} or (
            granted is not None and set(granted) != {SEND_SCOPE}
        ):
            raise SetupError("Google returned unexpected permissions; credentials were not saved.")
        return {
            "GMAIL_CLIENT_ID": config["installed"]["client_id"],
            "GMAIL_CLIENT_SECRET": config["installed"]["client_secret"],
            "GMAIL_REFRESH_TOKEN": credentials.refresh_token,
            "NOTIFICATION_FROM_EMAIL": sender,
        }
    except SetupError:
        raise
    except Exception:
        # Provider exceptions can contain an authorization code or credentials.
        raise SetupError("Gmail authorization did not finish. Check the account setup and retry; no credentials were saved.") from None
    finally:
        try:
            if flow is not None:
                flow.oauth2session.close()
        finally:
            logging.disable(previous_logging)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-secrets", "--client-json", dest="client_json", required=True, type=Path,
                        help="Downloaded Desktop OAuth client JSON (keep it outside Git)")
    parser.add_argument("--sender", required=True, help="Dedicated sender's @gmail.com address")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="New private JSON file directly under instance/deployment")
    args = parser.parse_args(argv)
    try:
        sender = gmail_address(args.sender)
        config = load_client_config(args.client_json)
        with output_destination(args.output) as (directory, filename):
            values = authorize(config, sender)
            save_credentials(directory, filename, values)
    except SetupError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Gmail setup cancelled; email remains disabled.", file=sys.stderr)
        return 130
    print("Saved private sender settings. Email is still disabled; no message was sent.")
    print("Confirm the actual From account with a controlled test before enabling the app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
