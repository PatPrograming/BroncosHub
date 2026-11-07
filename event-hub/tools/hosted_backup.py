"""Offline PostgreSQL/private-cover backups. No live restore or cloud writes."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

import psycopg
from psycopg import sql

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.data_backup import _copy_checked, _publish_directory

# Load only the existing standalone storage adapter. Importing through `app`
# would execute Flask's package/config initialization and load an unrelated .env.
_storage_spec = importlib.util.spec_from_file_location(
    "broncoshub_backup_storage", Path(__file__).resolve().parents[1] / "app/services/remote_cover_storage.py"
)
_storage_module = importlib.util.module_from_spec(_storage_spec)
_storage_spec.loader.exec_module(_storage_module)
RemoteCoverStorage = _storage_module.RemoteCoverStorage

SCHEMA = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
SERVICE = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
COVER = re.compile(r"[a-f0-9]{32}\.webp\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
FORMAT = "broncoshub-hosted-v1"
MAX_DATABASE_BYTES = 512 * 1024 * 1024
MAX_COVER_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_COVERS = 10000
MAX_TABLES = 100


def _schema(value):
    if (not isinstance(value, str) or not SCHEMA.fullmatch(value)
            or value in {"public", "information_schema"} or value.startswith("pg_")):
        raise ValueError("Use the dedicated private application schema.")
    return value


def _service(value):
    if not isinstance(value, str) or not SERVICE.fullmatch(value):
        raise ValueError("Use a named PostgreSQL connection service, not a connection URL.")
    return value


def _pg_environment():
    # Do not hand the Storage key, Flask session secret, or unrelated credentials
    # to PostgreSQL command-line tools. The password stays in the operator's
    # private pgpass file; PostgreSQL itself checks its filesystem permissions.
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "APPDATA",
               "PGSERVICEFILE", "PGSYSCONFDIR", "PGPASSFILE", "PGSSLROOTCERT"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def _inventory(connection, schema):
    tables = [row[0] for row in connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename", (schema,)
    ).fetchall()]
    if (not {"users", "events", "registrations"}.issubset(tables)
            or len(tables) > MAX_TABLES or any(not SCHEMA.fullmatch(x) for x in tables)):
        raise ValueError("The database is not a supported BroncosHub application schema.")
    counts = {table: connection.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(
        sql.Identifier(schema), sql.Identifier(table))).fetchone()[0] for table in tables}
    rows = connection.execute(sql.SQL(
        "SELECT DISTINCT cover_image_path FROM {}.events WHERE cover_image_path IS NOT NULL LIMIT %s"
    ).format(sql.Identifier(schema)), (MAX_COVERS + 1,)).fetchall()
    covers = [row[0] for row in rows]
    if len(covers) > MAX_COVERS or any(not isinstance(x, str) or not COVER.fullmatch(x) for x in covers):
        raise ValueError("The database has too many covers or an unsafe cover filename.")
    return {"table_counts": counts, "covers": sorted(covers)}


@contextmanager
def _snapshot(service, schema):
    # Explicit verified TLS cannot be weakened by the service profile or env.
    with psycopg.connect(service=service, sslmode="verify-full", connect_timeout=10) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '30s'")
        snapshot = connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
        if not re.fullmatch(r"[0-9A-Fa-f-]+", snapshot):
            raise ValueError("Invalid PostgreSQL snapshot identifier.")
        yield _inventory(connection, schema), snapshot


def _dump(service, schema, snapshot, path):
    import resource

    binary = shutil.which("pg_dump")
    if not binary:
        raise ValueError("Install PostgreSQL client tools with pg_dump before backing up.")

    def limit_file():
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_DATABASE_BYTES, MAX_DATABASE_BYTES))

    # Passwords are never placed in argv or printed in failure output. A named
    # service carries host/user settings and uses a private password file.
    command = [binary, "--dbname", f"service={service} sslmode=verify-full connect_timeout=10",
               "--format=custom", "--schema", schema, "--strict-names", "--no-password",
               "--no-owner", "--no-privileges", "--no-comments", "--no-security-labels",
               "--no-publications", "--no-subscriptions", "--no-tablespaces",
               "--lock-wait-timeout=10000", "--snapshot", snapshot, "--file", str(path)]
    try:
        subprocess.run(command, check=True, timeout=300, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=_pg_environment(), preexec_fn=limit_file)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("PostgreSQL export failed. Check client/server versions, private connection settings, and schema permissions.") from None
    os.chmod(path, 0o600)
    with path.open("rb") as source:
        if path.stat().st_size > MAX_DATABASE_BYTES or source.read(5) != b"PGDMP":
            raise ValueError("PostgreSQL export is invalid or exceeds the size limit.")


def _new_parent(path):
    if os.path.lexists(path):
        raise ValueError("Choose a new destination; existing data is never overwritten.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def _file_limit(name):
    if name == "database.dump":
        return MAX_DATABASE_BYTES
    if isinstance(name, str) and name.startswith("covers/") and COVER.fullmatch(name[7:]):
        return MAX_COVER_BYTES
    raise ValueError("Unsafe backup path.")


def create_backup(service, schema, storage_config, output, *, app_stopped=False):
    if app_stopped is not True:
        raise ValueError("Stop web writes, background workers, and other data editors before backing up.")
    service, schema, output = _service(service), _schema(schema), Path(output)
    _new_parent(output)
    storage = RemoteCoverStorage(storage_config)
    with tempfile.TemporaryDirectory(prefix=".broncos-backup-", dir=output.parent) as work:
        stage = Path(work)
        dump = stage / "database.dump"
        with _snapshot(service, schema) as (inventory, snapshot):
            _dump(service, schema, snapshot, dump)
        staged_archive = stage / "backup.zip"
        manifest = {"format": FORMAT, "schema": schema,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "scope": "application schema and referenced event covers",
                    **inventory, "files": {}}
        total = dump.stat().st_size
        with staged_archive.open("xb") as target:
            os.fchmod(target.fileno(), 0o600)
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                with dump.open("rb") as source, archive.open("database.dump", "w") as entry:
                    manifest["files"]["database.dump"] = _copy_checked(source, entry, MAX_DATABASE_BYTES)
                for name in inventory["covers"]:
                    data = storage.read(name)
                    if data is None:
                        raise ValueError("A referenced cover is missing. The backup was not published.")
                    total += len(data)
                    if len(data) > MAX_COVER_BYTES or total > MAX_TOTAL_BYTES:
                        raise ValueError("Backup exceeds its size limit.")
                    member = "covers/" + name
                    archive.writestr(member, data)
                    manifest["files"][member] = hashlib.sha256(data).hexdigest()
                encoded = json.dumps(manifest, sort_keys=True).encode()
                if len(encoded) > MAX_MANIFEST_BYTES or total + len(encoded) > MAX_TOTAL_BYTES:
                    raise ValueError("Backup manifest exceeds its size limit.")
                archive.writestr("manifest.json", encoded)
            target.flush()
            os.fsync(target.fileno())
        verify_backup(staged_archive)
        # Same-filesystem hard link is atomic and refuses concurrent replacement.
        os.link(staged_archive, output)
    return manifest


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate backup manifest key.")
        result[key] = value
    return result


def _read_manifest(archive):
    entries = archive.infolist()
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)) or len(names) > MAX_COVERS + 2:
        raise ValueError("Duplicate or excessive backup entries.")
    if sum(entry.file_size for entry in entries) > MAX_TOTAL_BYTES:
        raise ValueError("Backup exceeds its expanded size limit.")
    for entry in entries:
        mode = stat.S_IFMT(entry.external_attr >> 16)
        limit = MAX_MANIFEST_BYTES if entry.filename == "manifest.json" else _file_limit(entry.filename)
        if entry.file_size > limit or mode not in (0, stat.S_IFREG) or entry.flag_bits & 1:
            raise ValueError("Unsafe backup entry or size limit exceeded.")
    try:
        manifest = json.loads(archive.read("manifest.json"), object_pairs_hook=_unique_pairs)
    except (KeyError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Missing or invalid backup manifest.") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("Unsupported backup format.")
    _schema(manifest.get("schema"))
    files, covers, counts = (manifest.get(key) for key in ("files", "covers", "table_counts"))
    if (not isinstance(files, dict) or "database.dump" not in files
            or not isinstance(covers, list) or len(covers) > MAX_COVERS
            or any(not isinstance(name, str) or not COVER.fullmatch(name) for name in covers)
            or len(covers) != len(set(covers)) or not isinstance(counts, dict)
            or not {"users", "events", "registrations"}.issubset(counts) or len(counts) > MAX_TABLES
            or any(not SCHEMA.fullmatch(name) or type(count) is not int or count < 0 for name, count in counts.items())):
        raise ValueError("Incomplete backup inventory.")
    if (set(names) != set(files) | {"manifest.json"}
            or set(files) != {"database.dump"} | {"covers/" + name for name in covers}):
        raise ValueError("Backup is missing a referenced cover or contains unexpected files.")
    if any(not isinstance(value, str) or not DIGEST.fullmatch(value) for value in files.values()):
        raise ValueError("Invalid backup checksum.")
    return manifest


def _verify_or_extract(archive_path, destination=None):
    archive_path = Path(archive_path)
    if archive_path.is_symlink() or not archive_path.is_file() or archive_path.stat().st_size > MAX_TOTAL_BYTES:
        raise ValueError("Use a regular backup file within the size limit.")
    with zipfile.ZipFile(archive_path) as archive:
        manifest = _read_manifest(archive)
        for name, expected in manifest["files"].items():
            with archive.open(name) as source:
                prefix = source.read(12)
                if (name == "database.dump" and not prefix.startswith(b"PGDMP")) or (
                    name.startswith("covers/") and (prefix[:4] != b"RIFF" or prefix[8:12] != b"WEBP")
                ):
                    raise ValueError("Invalid database or cover file in backup.")
            with archive.open(name) as source:
                if destination is None:
                    with open(os.devnull, "wb") as target:
                        actual = _copy_checked(source, target, _file_limit(name))
                else:
                    path = destination / name
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with path.open("xb") as target:
                        os.fchmod(target.fileno(), 0o600)
                        actual = _copy_checked(source, target, _file_limit(name))
            if actual != expected:
                raise ValueError("Backup checksum failed.")
        return manifest


def verify_backup(archive_path):
    """Check bounded file inventory and hashes; does not execute database SQL."""
    return _verify_or_extract(archive_path)


def extract_backup(archive_path, destination):
    """Publish a checked private tree atomically, never overwrite any directory."""
    destination = Path(destination)
    _new_parent(destination)
    with tempfile.TemporaryDirectory(prefix=".broncos-extract-", dir=destination.parent) as work:
        stage = Path(work) / "data"
        stage.mkdir(mode=0o700)
        manifest = _verify_or_extract(archive_path, stage)
        with (stage / "manifest.json").open("x") as target:
            os.fchmod(target.fileno(), 0o600)
            json.dump(manifest, target, sort_keys=True)
        _publish_directory(stage, destination)
    return manifest


def check_restored(archive_path, service, database):
    """Read-only comparison against a separately restored, local scratch DB."""
    manifest = verify_backup(archive_path)
    _service(service)
    if not re.fullmatch(r"broncoshub_restore_[a-z0-9_]{1,40}", database):
        raise ValueError("Use a new local scratch database named broncoshub_restore_<name>.")
    # Explicit loopback defeats a service profile accidentally pointing at cloud.
    # The service provides local port/user and pgpass, never the live host/dbname.
    with psycopg.connect(service=service, host="127.0.0.1", hostaddr="127.0.0.1",
                         dbname=database, sslmode="disable", connect_timeout=5) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '30s'")
        actual = _inventory(connection, manifest["schema"])
    if actual != {key: manifest[key] for key in ("table_counts", "covers")}:
        raise ValueError("Restored database counts or cover references differ from the backup.")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--service", default="broncoshub")
    backup.add_argument("--schema", default="broncoshub")
    backup.add_argument("--output", required=True)
    backup.add_argument("--app-stopped", action="store_true", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("archive")
    extract = commands.add_parser("extract")
    extract.add_argument("archive")
    extract.add_argument("destination")
    check = commands.add_parser("check-restored")
    check.add_argument("archive")
    check.add_argument("--service", default="broncoshub-restore-local")
    check.add_argument("--database", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            config = {"COVER_STORAGE_BACKEND": "supabase", "COVER_MAX_BYTES": MAX_COVER_BYTES,
                      "SUPABASE_URL": os.environ.get("SUPABASE_URL", ""),
                      "SUPABASE_SECRET_KEY": os.environ.get("SUPABASE_SECRET_KEY", ""),
                      "SUPABASE_STORAGE_BUCKET": os.environ.get("SUPABASE_STORAGE_BUCKET", "event-covers")}
            manifest = create_backup(args.service, args.schema, config, args.output, app_stopped=args.app_stopped)
        elif args.command == "verify":
            manifest = verify_backup(args.archive)
        elif args.command == "extract":
            manifest = extract_backup(args.archive, args.destination)
        else:
            manifest = check_restored(args.archive, args.service, args.database)
    except (OSError, ValueError, RuntimeError, psycopg.Error, zipfile.BadZipFile):
        # Connection/storage exceptions can include credentials or student data.
        parser.exit(1, "Backup operation failed; no live restore was attempted. Check private connection settings, file integrity, and available space.\n")
    print(f"Verified {len(manifest['table_counts'])} tables and {len(manifest['covers'])} referenced covers.")


if __name__ == "__main__":
    main()
