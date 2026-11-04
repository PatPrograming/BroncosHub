"""Restore a real SQLite record and cover; reject unsafe or incomplete archives."""
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile
import pytest
from tools import data_backup
from tools.data_backup import check_database, create_backup, restore_backup


@pytest.fixture
def data(tmp_path):
    database = tmp_path / "source.db"
    covers = tmp_path / "covers"
    covers.mkdir()
    name = "a" * 32 + ".webp"
    (covers / name).write_bytes(b"normalized cover fixture")
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT, cover_image_path TEXT)")
        connection.execute("INSERT INTO events VALUES (1, ?, ?)", ("Student event", name))
    return database, covers, name


def test_round_trip_preserves_db_and_referenced_images_only(data, tmp_path):
    database, covers, name = data
    (covers / "unreferenced.webp").write_bytes(b"old")
    archive = tmp_path / "backup.zip"
    assert create_backup(database, covers, archive) == 1
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored) == 1
    with sqlite3.connect(restored / "broncoshub.db") as connection:
        assert connection.execute("SELECT title FROM events").fetchone() == ("Student event",)
    assert (restored / "uploads/event-covers" / name).read_bytes() == (covers / name).read_bytes()
    assert not (restored / "uploads/event-covers/unreferenced.webp").exists()
    with pytest.raises(ValueError, match="new directory"):
        restore_backup(archive, restored)
    with pytest.raises(ValueError, match="filename"):
        create_backup(database, covers, archive)


def test_missing_cover_fails_without_leaving_broken_backup(data, tmp_path):
    database, covers, name = data
    (covers / name).unlink()
    output = tmp_path / "bad.zip"
    with pytest.raises(ValueError, match="missing"):
        create_backup(database, covers, output)
    assert not output.exists()


def test_restore_rejects_path_traversal_without_writing(data, tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("manifest.json", json.dumps({"format": 1, "files": {"broncoshub.db": "x", "../escaped": "x"}}))
        target.writestr("broncoshub.db", b"x")
        target.writestr("../escaped", b"x")
    with pytest.raises(ValueError):
        restore_backup(archive, tmp_path / "out")
    assert not (tmp_path / "escaped").exists()
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("filename", ["campus?backup.db", "campus#backup.db", "campus%backup.db"])
def test_database_uri_handles_reserved_filename_characters(data, tmp_path, filename):
    database, covers, name = data
    renamed = tmp_path / filename
    database.rename(renamed)
    assert check_database(renamed) == {name}
    archive = tmp_path / "backup.zip"
    assert create_backup(renamed, covers, archive) == 1
    assert restore_backup(archive, tmp_path / "restored") == 1
    assert not (tmp_path / "campus").exists()


@pytest.mark.parametrize("schema", [None, "CREATE TABLE events (id INTEGER PRIMARY KEY)"])
def test_empty_or_wrong_database_is_not_verified(tmp_path, schema):
    database = tmp_path / "unrelated.db"
    with sqlite3.connect(database) as connection:
        if schema:
            connection.execute(schema)
    with pytest.raises(ValueError, match="schema"):
        check_database(database)
    with pytest.raises(ValueError, match="schema"):
        create_backup(database, tmp_path, tmp_path / "bad.zip")
    assert not (tmp_path / "bad.zip").exists()


def _archive_with_manifest(path, manifest):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))


@pytest.mark.parametrize("manifest", [None, [], "text", 1, {"format": 1, "files": []}, {"format": True, "files": {"broncoshub.db": "a" * 64}}])
def test_malformed_manifest_rejected_without_partial_restore(tmp_path, manifest):
    archive = tmp_path / "bad.zip"
    _archive_with_manifest(archive, manifest)
    with pytest.raises(ValueError, match="Unsupported"):
        restore_backup(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".broncos-restore-*"))


@pytest.mark.parametrize("limit_name, limit", [("MAX_DATABASE_BYTES", 10), ("MAX_COVER_BYTES", 5), ("MAX_ARCHIVE_BYTES", 50), ("MAX_MANIFEST_BYTES", 5)])
def test_restore_size_limits_before_expansion(data, tmp_path, monkeypatch, limit_name, limit):
    database, covers, _ = data
    archive = tmp_path / "backup.zip"
    create_backup(database, covers, archive)
    monkeypatch.setattr(data_backup, limit_name, limit)
    with pytest.raises(ValueError, match="size"):
        restore_backup(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".broncos-restore-*"))


def test_expanded_archive_limit_applies_to_compressed_files(tmp_path, monkeypatch):
    archive = tmp_path / "compressed.zip"
    payload = b"x" * 20000
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
        target.writestr("broncoshub.db", payload)
        target.writestr("manifest.json", json.dumps({"format": 1, "files": {"broncoshub.db": hashlib.sha256(payload).hexdigest()}}))
    assert archive.stat().st_size < 1000
    monkeypatch.setattr(data_backup, "MAX_ARCHIVE_BYTES", 1000)
    with pytest.raises(ValueError, match="expanded size"):
        restore_backup(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_restore_write_failure_never_exposes_partial_directory(data, tmp_path, monkeypatch):
    database, covers, _ = data
    archive = tmp_path / "backup.zip"
    create_backup(database, covers, archive)
    real_copy = data_backup._copy_checked
    calls = []
    def fail_second_file(source, target, limit):
        calls.append(target.name)
        if len(calls) == 2:
            target.write(b"partial")
            raise OSError("disk full")
        return real_copy(source, target, limit)
    monkeypatch.setattr(data_backup, "_copy_checked", fail_second_file)
    with pytest.raises(OSError, match="disk full"):
        restore_backup(archive, tmp_path / "restored")
    assert len(calls) == 2
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".broncos-restore-*"))


@pytest.mark.parametrize("with_file", [False, True])
def test_restore_publish_race_preserves_existing_directory(data, tmp_path, monkeypatch, with_file):
    database, covers, _ = data
    archive = tmp_path / "backup.zip"
    create_backup(database, covers, archive)
    real_publish = data_backup._publish_directory
    def raced_publish(source, destination):
        destination.mkdir()
        if with_file:
            (destination / "existing.txt").write_text("keep me")
        real_publish(source, destination)
    monkeypatch.setattr(data_backup, "_publish_directory", raced_publish)
    with pytest.raises(ValueError, match="new directory"):
        restore_backup(archive, tmp_path / "restored")
    expected = [tmp_path / "restored/existing.txt"] if with_file else []
    assert list((tmp_path / "restored").iterdir()) == expected
    if with_file:
        assert (tmp_path / "restored/existing.txt").read_text() == "keep me"
    assert not list(tmp_path.glob(".broncos-restore-*"))


def test_backup_creation_race_does_not_replace_or_delete_existing_file(data, tmp_path, monkeypatch):
    database, covers, _ = data
    output = tmp_path / "backup.zip"
    real_open = Path.open
    def raced_open(path, mode="r", *args, **kwargs):
        if path == output and mode == "xb":
            with real_open(path, "wb") as existing:
                existing.write(b"existing backup")
        return real_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", raced_open)
    with pytest.raises(FileExistsError):
        create_backup(database, covers, output)
    assert output.read_bytes() == b"existing backup"


def test_restore_rejects_invalid_checksum_without_exposing_files(data, tmp_path):
    database, covers, _ = data
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("broncoshub.db", database.read_bytes())
        target.writestr("manifest.json", json.dumps({"format": 1, "files": {"broncoshub.db": "0" * 64}}))
    with pytest.raises(ValueError, match="checksum"):
        restore_backup(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
