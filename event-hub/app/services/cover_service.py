"""Validate event covers and keep only normalized images in persistent storage."""

from io import BytesIO
from pathlib import Path
import re
import secrets
import warnings

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import inspect, text

from app.extensions import db


FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
MIMES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
STORED_NAME = re.compile(r"[a-f0-9]{32}\.webp\Z")


class InvalidCover(ValueError):
    """A user-facing image validation error."""


def normalize_cover(upload):
    """Return safe image bytes, or None when the optional upload is empty."""
    if upload is None or not upload.filename:
        return None
    name = upload.filename
    if "/" in name or "\\" in name or "\x00" in name:
        raise InvalidCover("Choose an image file with a simple file name.")
    expected = FORMATS.get(Path(name).suffix.lower())
    if expected is None:
        raise InvalidCover("Choose a JPG, PNG or WebP image.")
    limit = current_app.config["COVER_MAX_BYTES"]
    source = upload.stream.read(limit + 1)
    if len(source) > limit:
        raise InvalidCover("Your cover must be 5 MB or smaller.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(source), formats=["JPEG", "PNG", "WEBP"]) as image:
                if image.format != expected or upload.mimetype not in (
                    MIMES[expected], "application/octet-stream", ""
                ):
                    raise InvalidCover("The file contents must match its JPG, PNG or WebP type.")
                if image.width * image.height > current_app.config["COVER_MAX_PIXELS"]:
                    raise InvalidCover("Use an image with no more than 20 million pixels.")
                if image.width > 10000 or image.height > 10000:
                    raise InvalidCover("Use an image no more than 10,000 pixels wide or tall.")
                if getattr(image, "n_frames", 1) != 1:
                    raise InvalidCover("Choose a still image instead of an animated cover.")
                image.verify()
            # Reopen after verify, decode every pixel, orient, then re-encode.
            # Original metadata and any trailing non-image bytes are discarded.
            with Image.open(BytesIO(source), formats=[expected]) as image:
                image.load()
                image = ImageOps.exif_transpose(image)
                image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
                clean = Image.new("RGBA", image.size)
                clean.paste(image.convert("RGBA"))
                output = BytesIO()
                clean.save(output, format="WEBP", quality=88, method=4)
                normalized = output.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        if isinstance(error, InvalidCover):
            raise
        raise InvalidCover("That image couldn't be read. Choose a valid JPG, PNG or WebP.") from error
    if len(normalized) > limit:
        raise InvalidCover("This image is too large to save. Choose a smaller cover.")
    return normalized


def cover_directory():
    """Resolve relative settings against Flask's instance directory, never cwd."""
    configured = current_app.config.get("COVER_UPLOAD_DIR")
    directory = Path(configured) if configured else Path("uploads") / "event-covers"
    if not directory.is_absolute():
        directory = Path(current_app.instance_path) / directory
    return directory.resolve()


def cover_path(name):
    """Never treat a stored value as an arbitrary path or external URL."""
    if not isinstance(name, str) or not STORED_NAME.fullmatch(name):
        return None
    path = cover_directory() / name
    return None if path.is_symlink() else path


def store_cover(data):
    directory = cover_directory()
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(16)}.webp"
    path = directory / name
    try:
        with path.open("xb") as target:
            target.write(data)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return name


def delete_cover(name):
    path = cover_path(name)
    if path is not None:
        path.unlink(missing_ok=True)


def commit_event_with_cover(event, image_data=None, remove=False):
    """Keep the old file until the event save succeeds; clean failed new saves."""
    previous = event.cover_image_path
    replacement = store_cover(image_data) if image_data is not None else None
    if replacement is not None or remove:
        event.cover_image_path = replacement
    try:
        db.session.add(event)
        db.session.commit()
    except Exception:
        db.session.rollback()
        if replacement is not None:
            delete_cover(replacement)
        raise
    if previous and previous != event.cover_image_path:
        try:
            delete_cover(previous)
        except OSError:
            # The saved event is valid even if a stale file needs later cleanup.
            current_app.logger.warning("Could not remove an old event cover file.")


def ensure_event_schema():
    """Add this nullable column to existing SQLite databases without data resets.

    create_all handles fresh databases. This small upgrade is idempotent for the
    project's existing SQLite database; other databases need a schema migration.
    """
    with db.engine.begin() as connection:
        if db.engine.dialect.name == "sqlite":
            # Multiple workers may start together. Check after taking SQLite's
            # write lock so only one of them can add the missing column.
            connection.execute(text("BEGIN IMMEDIATE"))
        needs_column = "cover_image_path" not in {
            column["name"] for column in inspect(connection).get_columns("events")
        }
        if needs_column and db.engine.dialect.name != "sqlite":
            raise RuntimeError("Add nullable events.cover_image_path VARCHAR(64) before starting this version.")
        if needs_column:
            connection.execute(text("ALTER TABLE events ADD COLUMN cover_image_path VARCHAR(64)"))
        # Preserve past moderation removals, which used the same status as an
        # ordinary organizer cancellation before removed became a separate state.
        connection.execute(text(
            "UPDATE events SET status = 'removed' WHERE status = 'cancelled' "
            "AND EXISTS (SELECT 1 FROM reports WHERE reports.event_id = events.id "
            "AND reports.status = 'removed')"
        ))
