"""Hosted backup safety: complete private archives or no published result."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from tools import hosted_backup as backup


@pytest.fixture
def source(monkeypatch):
    name = "a" * 32 + ".webp"
    image = b"RIFF\x10\x00\x00\x00WEBPfixture"
    dump = b"PGDMPdatabase fixture"
    inventory = {"table_counts": {"users": 2, "events": 1, "registrations": 1}, "covers": [name]}

    @contextmanager
    def snapshot(service, schema):
        assert service == "broncoshub" and schema == "broncoshub"
        yield inventory, "00000003-00000001-1"

    def write_dump(service, schema, snapshot, path):
        path.write_bytes(dump)

    monkeypatch.setattr(backup, "_snapshot", snapshot)
    monkeypatch.setattr(backup, "_dump", write_dump)
    reader = SimpleNamespace(read=lambda value: image if value == name else None)
    monkeypatch.setattr(backup, "RemoteCoverStorage", lambda config: reader)
    return SimpleNamespace(name=name, image=image, dump=dump, inventory=inventory, reader=reader)


def create(tmp_path):
    archive = tmp_path / "hosted.zip"
    backup.create_backup("broncoshub", "broncoshub", {}, archive, app_stopped=True)
    return archive


def rewrite(path, *, mutate_manifest=lambda x: x, extra=None, replacement=None):
    with zipfile.ZipFile(path) as archive:
        data = {name: archive.read(name) for name in archive.namelist()}
    manifest = mutate_manifest(json.loads(data["manifest.json"]))
    data["manifest.json"] = json.dumps(manifest).encode()
    if replacement:
        data.update(replacement)
    if extra:
        data.update(extra)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in data.items():
            archive.writestr(name, value)


def test_backup_round_trip_preserves_data_images_and_private_permissions(source, tmp_path):
    archive = create(tmp_path)
    manifest = backup.verify_backup(archive)
    assert manifest["table_counts"] == source.inventory["table_counts"]
    assert manifest["covers"] == [source.name]
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    output = tmp_path / "restored"
    backup.extract_backup(archive, output)
    assert (output / "database.dump").read_bytes() == source.dump
    assert (output / "covers" / source.name).read_bytes() == source.image
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE((output / "database.dump").stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".broncos-*"))
    with pytest.raises(ValueError, match="new destination"):
        backup.extract_backup(archive, output)
    with pytest.raises(ValueError, match="new destination"):
        backup.create_backup("broncoshub", "broncoshub", {}, archive, app_stopped=True)


def test_backup_refuses_running_app_before_network_access(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "RemoteCoverStorage", lambda _: pytest.fail("No storage access expected"))
    with pytest.raises(ValueError, match="Stop web writes"):
        backup.create_backup("broncoshub", "broncoshub", {}, tmp_path / "bad.zip")


@pytest.mark.parametrize("schema", ["public", "pg_authid", "information_schema", "broncoshub;drop", "../events", None])
def test_backup_rejects_unsafe_or_shared_schema(source, tmp_path, schema):
    with pytest.raises(ValueError, match="private"):
        backup.create_backup("broncoshub", schema, {}, tmp_path / "bad.zip", app_stopped=True)


@pytest.mark.parametrize("service", ["postgresql://user:secret@host/db", "x password=secret", "x\npassword=secret", "", None])
def test_service_is_a_name_not_a_credential_string(source, tmp_path, service):
    with pytest.raises(ValueError, match="named PostgreSQL"):
        backup.create_backup(service, "broncoshub", {}, tmp_path / "bad.zip", app_stopped=True)


def test_missing_cover_never_publishes_partial_backup(source, tmp_path):
    source.reader.read = lambda _: None
    with pytest.raises(ValueError, match="missing"):
        create(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_failed_provider_read_cleans_private_staging(source, tmp_path):
    def fail(_):
        raise OSError("private upstream details")
    source.reader.read = fail
    with pytest.raises(OSError):
        create(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_damaged_archive_is_not_extracted(source, tmp_path):
    archive = create(tmp_path)
    rewrite(archive, replacement={"database.dump": b"PGDMPtampered"})
    with pytest.raises(ValueError, match="checksum"):
        backup.extract_backup(archive, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()
    assert not list(tmp_path.glob(".broncos-*"))


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside", "covers/../outside", "covers/not-a-cover.webp"])
def test_unexpected_paths_are_rejected_before_any_extraction(source, tmp_path, path):
    archive = create(tmp_path)
    rewrite(archive, extra={path: b"bad"})
    with pytest.raises(ValueError, match="Unsafe"):
        backup.extract_backup(archive, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_duplicate_entries_are_rejected(source, tmp_path):
    archive = create(tmp_path)
    with zipfile.ZipFile(archive, "a") as target, pytest.warns(UserWarning):
        target.writestr("database.dump", source.dump)
    with pytest.raises(ValueError, match="Duplicate"):
        backup.verify_backup(archive)


def test_symlink_zip_entry_is_rejected(source, tmp_path):
    archive = create(tmp_path)
    with zipfile.ZipFile(archive) as target:
        entries = {name: target.read(name) for name in target.namelist()}
    with zipfile.ZipFile(archive, "w") as target:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            target.writestr(info, data)
    with pytest.raises(ValueError, match="Unsafe"):
        backup.verify_backup(archive)


def test_manifest_cannot_silently_drop_referenced_cover(source, tmp_path):
    archive = create(tmp_path)
    def remove(manifest):
        del manifest["files"]["covers/" + source.name]
        return manifest
    rewrite(archive, mutate_manifest=remove)
    with pytest.raises(ValueError, match="missing"):
        backup.verify_backup(archive)


@pytest.mark.parametrize("field,value", [("format", 1), ("table_counts", []), ("covers", ["../oops"]), ("files", [])])
def test_bad_manifest_types_fail_closed(source, tmp_path, field, value):
    archive = create(tmp_path)
    rewrite(archive, mutate_manifest=lambda m: {**m, field: value})
    with pytest.raises(ValueError):
        backup.verify_backup(archive)


@pytest.mark.parametrize("constant,limit", [("MAX_DATABASE_BYTES", 10), ("MAX_COVER_BYTES", 10), ("MAX_MANIFEST_BYTES", 10), ("MAX_TOTAL_BYTES", 10)])
def test_limits_are_checked_before_extraction(source, tmp_path, monkeypatch, constant, limit):
    archive = create(tmp_path)
    monkeypatch.setattr(backup, constant, limit)
    with pytest.raises(ValueError, match="size limit"):
        backup.extract_backup(archive, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_concurrent_backup_name_is_not_overwritten(source, tmp_path, monkeypatch):
    original = backup.verify_backup
    def verify_then_race(path):
        result = original(path)
        (tmp_path / "hosted.zip").write_bytes(b"another process")
        return result
    monkeypatch.setattr(backup, "verify_backup", verify_then_race)
    with pytest.raises(FileExistsError):
        create(tmp_path)
    assert (tmp_path / "hosted.zip").read_bytes() == b"another process"
    assert not list(tmp_path.glob(".broncos-*"))


def test_postgres_dump_sanitizes_environment_and_failure_output(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "secret-storage-key")
    monkeypatch.setenv("SECRET_KEY", "secret-session-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://password-in-url")
    monkeypatch.setenv("PGPASSWORD", "secret-db-password")
    monkeypatch.setenv("PGPASSFILE", "/private/.pgpass")
    monkeypatch.setattr(backup.shutil, "which", lambda _: "/pg_dump")
    def run(command, **kwargs):
        assert "secret-db-password" not in " ".join(command)
        assert "password-in-url" not in " ".join(command)
        assert kwargs["env"]["PGPASSFILE"] == "/private/.pgpass"
        assert not {"SUPABASE_SECRET_KEY", "SECRET_KEY", "DATABASE_URL", "PGPASSWORD"} & kwargs["env"].keys()
        assert kwargs["timeout"] == 300
        assert "sslmode=verify-full" in command[2]
        raise subprocess.CalledProcessError(1, command, stderr="secret-db-password")
    monkeypatch.setattr(backup.subprocess, "run", run)
    with pytest.raises(ValueError, match="PostgreSQL export failed") as error:
        backup._dump("broncoshub", "broncoshub", "0000-1111-1", tmp_path / "db")
    assert "secret" not in str(error.value)


def test_cli_does_not_echo_provider_credentials(source, tmp_path, monkeypatch, capsys):
    def fail(_):
        raise OSError("do-not-print-secret-value")
    source.reader.read = fail
    with pytest.raises(SystemExit) as error:
        backup.main(["backup", "--output", str(tmp_path / "out.zip"), "--app-stopped"])
    assert error.value.code == 1
    assert "do-not-print-secret-value" not in capsys.readouterr().err


def test_restore_check_cannot_target_live_database(source, tmp_path, monkeypatch):
    archive = create(tmp_path)
    monkeypatch.setattr(backup.psycopg, "connect", lambda **_: pytest.fail("No connection expected"))
    with pytest.raises(ValueError, match="scratch database"):
        backup.check_restored(archive, "broncoshub", "postgres")


def test_restore_check_forces_local_read_only_and_compares_inventory(source, tmp_path, monkeypatch):
    archive = create(tmp_path)
    calls = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, value): calls.append(value)
    def connect(**kwargs):
        assert kwargs["host"] == kwargs["hostaddr"] == "127.0.0.1"
        assert kwargs["dbname"] == "broncoshub_restore_test"
        return Connection()
    monkeypatch.setattr(backup.psycopg, "connect", connect)
    monkeypatch.setattr(backup, "_inventory", lambda *_: source.inventory)
    backup.check_restored(archive, "local", "broncoshub_restore_test")
    assert "READ ONLY" in calls[0]
    monkeypatch.setattr(backup, "_inventory", lambda *_: {**source.inventory, "covers": []})
    with pytest.raises(ValueError, match="differ"):
        backup.check_restored(archive, "local", "broncoshub_restore_test")
