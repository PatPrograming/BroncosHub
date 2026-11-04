"""Offline backup/restore of SQLite and normalized event covers. Never includes .env."""
import argparse
import ctypes
from contextlib import closing
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import zipfile

COVER_NAME = re.compile(r"[a-f0-9]{32}\.webp\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_DATABASE_BYTES = 512 * 1024 * 1024
MAX_COVER_BYTES = 5 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


def _database_uri(path):
    # A raw file: URI would interpret '?' and '#' in a real filename as syntax.
    return Path(path).resolve().as_uri() + "?mode=ro"


def _safe_cover(name):
    return isinstance(name, str) and COVER_NAME.fullmatch(name)


def check_database(path):
    with closing(sqlite3.connect(_database_uri(path), uri=True)) as connection:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Database integrity check failed.")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Database contains broken references.")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
        if not {"id", "title", "cover_image_path"}.issubset(columns):
            raise ValueError("Database is missing the BroncosHub events schema.")
        names = {row[0] for row in connection.execute("SELECT cover_image_path FROM events WHERE cover_image_path IS NOT NULL")}
        if any(not _safe_cover(name) for name in names):
            raise ValueError("Database contains an unsafe cover filename.")
        return names


def _copy_checked(source, target, limit):
    digest, count = hashlib.sha256(), 0
    while chunk := source.read(min(CHUNK_BYTES, limit - count + 1)):
        count += len(chunk)
        if count > limit:
            raise ValueError("Backup file exceeds the allowed size.")
        digest.update(chunk)
        target.write(chunk)
    return digest.hexdigest()


def _file_limit(name):
    if name == "broncoshub.db":
        return MAX_DATABASE_BYTES
    if isinstance(name, str) and name.startswith("uploads/event-covers/") and _safe_cover(name.removeprefix("uploads/event-covers/")):
        return MAX_COVER_BYTES
    raise ValueError("Unsafe archive path.")


def _publish_directory(source, destination):
    """Atomically publish without replacing even a concurrently created folder."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows rename refuses existing targets.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(source), os.fsencode(destination), 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise OSError("This platform cannot safely publish a restore without overwriting existing data.")
    if result != 0:
        code = ctypes.get_errno()
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            raise ValueError("Restore to a new directory; live data is never overwritten.")
        raise OSError(code, os.strerror(code))


def create_backup(database, covers, output):
    database, covers, output = Path(database), Path(covers), Path(output)
    if not database.is_file():
        raise ValueError("Database does not exist.")
    if database.stat().st_size > MAX_DATABASE_BYTES:
        raise ValueError("Database exceeds the allowed size.")
    if os.path.lexists(output):
        raise ValueError("Choose a new backup filename; existing files are never replaced.")
    output.parent.mkdir(parents=True, exist_ok=True)
    created_identity = None
    try:
        with tempfile.TemporaryDirectory() as work:
            snapshot = Path(work) / "broncoshub.db"
            with closing(sqlite3.connect(_database_uri(database), uri=True)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
            names = check_database(snapshot)
            files = {"broncoshub.db": snapshot}
            for name in sorted(names):
                path = covers / name
                if not path.is_file() or path.is_symlink():
                    raise ValueError("A referenced cover is missing. Stop the app and retry.")
                files["uploads/event-covers/" + name] = path
            total = 0
            for name, path in files.items():
                size = path.stat().st_size
                if size > _file_limit(name):
                    raise ValueError("Backup file exceeds the allowed size.")
                total += size
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("Backup exceeds the allowed total size.")
            with output.open("xb") as target:
                stat = os.fstat(target.fileno())
                created_identity = (stat.st_dev, stat.st_ino)
                os.fchmod(target.fileno(), 0o600)
                with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                    hashes = {}
                    for name, path in files.items():
                        with path.open("rb") as source, archive.open(name, "w") as archived:
                            hashes[name] = _copy_checked(source, archived, _file_limit(name))
                    manifest = json.dumps({"format": 1, "files": hashes}, sort_keys=True).encode()
                    if len(manifest) > MAX_MANIFEST_BYTES:
                        raise ValueError("Backup manifest exceeds the allowed size.")
                    archive.writestr("manifest.json", manifest)
            if output.stat().st_size > MAX_ARCHIVE_BYTES:
                raise ValueError("Backup archive exceeds the allowed size.")
        return len(names)
    except Exception:
        if created_identity is not None:
            try:
                stat = output.lstat()
                if (stat.st_dev, stat.st_ino) == created_identity:
                    output.unlink()
            except FileNotFoundError:
                pass
        raise


def restore_backup(archive_path, destination):
    archive_path, destination = Path(archive_path), Path(destination)
    if os.path.lexists(destination):
        raise ValueError("Restore to a new directory; live data is never overwritten.")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Backup archive exceeds the allowed size.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Stage on the destination filesystem, then publish the complete verified tree.
    with zipfile.ZipFile(archive_path) as archive, tempfile.TemporaryDirectory(prefix=".broncos-restore-", dir=destination.parent) as work:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive entries.")
        if sum(entry.file_size for entry in entries) > MAX_ARCHIVE_BYTES:
            raise ValueError("Backup exceeds the allowed expanded size.")
        for entry in entries:
            limit = MAX_MANIFEST_BYTES if entry.filename == "manifest.json" else _file_limit(entry.filename)
            if entry.file_size > limit:
                raise ValueError("Backup file exceeds the allowed size.")
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict):
            raise ValueError("Unsupported or incomplete backup.")
        files = manifest.get("files")
        if type(manifest.get("format")) is not int or manifest["format"] != 1 or not isinstance(files, dict) or "broncoshub.db" not in files:
            raise ValueError("Unsupported or incomplete backup.")
        if set(names) != set(files) | {"manifest.json"}:
            raise ValueError("Unexpected archive entries.")
        stage = Path(work) / "data"
        stage.mkdir(mode=0o700)
        for name, digest in files.items():
            limit = _file_limit(name)
            if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
                raise ValueError("Invalid backup checksum.")
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with archive.open(name) as source, path.open("xb") as target:
                os.fchmod(target.fileno(), 0o600)
                actual = _copy_checked(source, target, limit)
            if actual != digest:
                raise ValueError("Backup checksum failed.")
        covers = check_database(stage / "broncoshub.db")
        if any("uploads/event-covers/" + name not in files for name in covers):
            raise ValueError("Backup is missing a referenced cover.")
        _publish_directory(stage, destination)
    return len(covers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--database", required=True)
    backup.add_argument("--covers", required=True)
    backup.add_argument("--output", required=True)
    backup.add_argument("--app-stopped", action="store_true", required=True, help="Confirm the web server and payment worker are stopped so files and DB match.")
    restore = sub.add_parser("restore")
    restore.add_argument("archive")
    restore.add_argument("destination", help="A new directory, never an existing app-data directory.")
    args = parser.parse_args()
    try:
        if args.command == "backup":
            count = create_backup(args.database, args.covers, args.output)
        else:
            count = restore_backup(args.archive, args.destination)
    except (ValueError, OSError, sqlite3.Error, zipfile.BadZipFile, KeyError) as error:
        parser.exit(1, str(error) + "\n")
    print(f"Verified database and {count} event covers.")


if __name__ == "__main__":
    main()
