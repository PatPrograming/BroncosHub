"""Render startup defaults must come only from its validated service address."""

from pathlib import Path
import runpy

import pytest


@pytest.fixture
def read_config(monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for key in ("RENDER", "RENDER_EXTERNAL_HOSTNAME", "RENDER_EXTERNAL_URL",
                "TRUSTED_HOSTS", "PUBLIC_BASE_URL", "DATABASE_URL",
                "DATABASE_SCHEMA", "DATABASE_SSL_ROOT_CERT"):
        monkeypatch.delenv(key, raising=False)

    def read(**values):
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        return runpy.run_path(str(Path(__file__).parents[1] / "config.py"))["Config"]

    return read


@pytest.mark.parametrize("url", ["", "https://broncoshub-ab12.onrender.com",
                                  "https://broncoshub-ab12.onrender.com/"])
def test_render_service_address_bootstraps_initial_deployment(read_config, url):
    config = read_config(RENDER="true", RENDER_EXTERNAL_HOSTNAME="broncoshub-ab12.onrender.com",
                         RENDER_EXTERNAL_URL=url)
    assert config.TRUSTED_HOSTS == ["broncoshub-ab12.onrender.com"]
    assert config.PUBLIC_BASE_URL == "https://broncoshub-ab12.onrender.com"


@pytest.mark.parametrize("marker", ["", "false", "1", "True"])
def test_render_address_is_ignored_without_platform_marker(read_config, marker):
    config = read_config(RENDER=marker, RENDER_EXTERNAL_HOSTNAME="broncoshub.onrender.com",
                         RENDER_EXTERNAL_URL="https://broncoshub.onrender.com")
    assert config.TRUSTED_HOSTS is None
    assert config.PUBLIC_BASE_URL == ""


@pytest.mark.parametrize("host", [
    "", "attacker.example", "onrender.com", "broncoshub.onrender.com.evil.example",
    "*.onrender.com", ".broncoshub.onrender.com", "one.two.onrender.com",
    "https://broncoshub.onrender.com", "broncoshub.onrender.com/path",
    "broncoshub.onrender.com:443", "one.onrender.com,two.onrender.com",
    "-broncoshub.onrender.com", "broncoshub-.onrender.com", "broncós.onrender.com",
    "broncos\nhub.onrender.com", "a" * 64 + ".onrender.com",
])
def test_invalid_platform_hostname_does_not_create_a_trusted_host(read_config, host):
    config = read_config(RENDER="true", RENDER_EXTERNAL_HOSTNAME=host)
    assert config.TRUSTED_HOSTS is None
    assert config.PUBLIC_BASE_URL == ""


@pytest.mark.parametrize("url", [
    "http://broncoshub.onrender.com", "https://other.onrender.com",
    "https://broncoshub.onrender.com/path", "https://broncoshub.onrender.com//",
    "https://broncoshub.onrender.com?x=1", "https://broncoshub.onrender.com#fragment",
    "https://user@broncoshub.onrender.com", "https://broncoshub.onrender.com:444",
    "https://broncoshub.onrender.com\\evil", "https://broncoshub.onrender.com\n.evil.example",
])
def test_mismatched_or_non_origin_platform_url_fails_closed(read_config, url):
    config = read_config(RENDER="true", RENDER_EXTERNAL_HOSTNAME="broncoshub.onrender.com",
                         RENDER_EXTERNAL_URL=url)
    assert config.TRUSTED_HOSTS is None
    assert config.PUBLIC_BASE_URL == ""


@pytest.mark.parametrize("render_url", ["https://broncoshub.onrender.com", "https://bad.example"])
def test_explicit_custom_domain_settings_take_precedence(read_config, render_url):
    config = read_config(RENDER="true", RENDER_EXTERNAL_HOSTNAME="broncoshub.onrender.com",
                         RENDER_EXTERNAL_URL=render_url,
                         TRUSTED_HOSTS="events.example.edu, www.events.example.edu",
                         PUBLIC_BASE_URL="https://events.example.edu")
    assert config.TRUSTED_HOSTS == ["events.example.edu", "www.events.example.edu"]
    assert config.PUBLIC_BASE_URL == "https://events.example.edu"


def test_explicit_settings_work_without_render(read_config):
    config = read_config(TRUSTED_HOSTS="events.example.edu",
                         PUBLIC_BASE_URL="https://events.example.edu")
    assert config.TRUSTED_HOSTS == ["events.example.edu"]
    assert config.PUBLIC_BASE_URL == "https://events.example.edu"


def test_malformed_explicit_host_list_is_not_silently_replaced(read_config):
    config = read_config(RENDER="true", RENDER_EXTERNAL_HOSTNAME="broncoshub.onrender.com",
                         TRUSTED_HOSTS=",")
    assert config.TRUSTED_HOSTS is None


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg"])
def test_hosted_database_requires_verified_tls_and_private_search_path(read_config, scheme):
    config = read_config(DATABASE_URL=f"{scheme}://app:unused@aws-0-us-west-1.pooler.supabase.com:5432/postgres")
    assert config.SQLALCHEMY_DATABASE_URI.startswith("postgresql+psycopg://")
    options = config.SQLALCHEMY_ENGINE_OPTIONS
    assert options["pool_size"] + options["max_overflow"] == 4
    args = options["connect_args"]
    assert args["sslmode"] == "verify-full"
    assert Path(args["sslrootcert"]).is_file()
    assert Path(args["sslrootcert"]).name == "supabase-ca.crt"
    assert "-csearch_path=broncoshub" in args["options"]
    assert args["prepare_threshold"] is None


@pytest.mark.parametrize("schema", ["broncoshub,public", "app -csearch_path=public", "../public"])
def test_schema_cannot_inject_connection_options(read_config, schema):
    with pytest.raises(RuntimeError, match="DATABASE_SCHEMA"):
        read_config(DATABASE_URL="postgresql://app:unused@db.example/postgres", DATABASE_SCHEMA=schema)
