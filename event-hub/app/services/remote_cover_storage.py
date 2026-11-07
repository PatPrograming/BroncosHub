"""Server-only access to one private Supabase bucket.

The app's media route checks the event and viewer before reading these bytes.
Neither the service secret nor a storage URL is returned to the browser.
"""

import re
import time

import requests


class CoverStorageError(OSError):
    """A temporary storage failure with no provider credentials in its message."""


def validate_cover_storage_config(config):
    backend = config.get("COVER_STORAGE_BACKEND", "local")
    if backend not in {"local", "supabase"}:
        raise RuntimeError("COVER_STORAGE_BACKEND must be local or supabase.")
    if backend == "local":
        return
    url = config.get("SUPABASE_URL", "")
    if not isinstance(url, str) or not re.fullmatch(r"https://[a-z0-9]{20}\.supabase\.co/?", url):
        raise RuntimeError("SUPABASE_URL must be the HTTPS origin of your Supabase project.")
    key = config.get("SUPABASE_SECRET_KEY", "")
    if (not isinstance(key, str) or len(key) < 24 or
            not (key.startswith("sb_secret_") or key.startswith("eyJ")) or
            any(ord(character) <= 32 for character in key)):
        raise RuntimeError("Set a server-only SUPABASE_SECRET_KEY for private cover storage.")
    bucket = config.get("SUPABASE_STORAGE_BUCKET", "event-covers")
    if not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", bucket):
        raise RuntimeError("SUPABASE_STORAGE_BUCKET must be a simple lowercase bucket name.")


class RemoteCoverStorage:
    def __init__(self, config):
        validate_cover_storage_config(config)
        self.base = config["SUPABASE_URL"].rstrip("/") + "/storage/v1/object"
        self.bucket = config.get("SUPABASE_STORAGE_BUCKET", "event-covers")
        key = config["SUPABASE_SECRET_KEY"]
        self.headers = {"apikey": key}
        # Modern secret keys are not JWTs. Only legacy service-role JWTs use
        # Authorization; the gateway resolves a modern secret from apikey.
        if key.startswith("eyJ"):
            self.headers["Authorization"] = "Bearer " + key
        self.limit = config["COVER_MAX_BYTES"]

    def _request(self, method, suffix="", **kwargs):
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", {}))
        # Never send a privileged key to a redirected host. Disable automatic
        # response buffering so image reads are bounded before allocation.
        return requests.request(
            method, f"{self.base}/{self.bucket}{suffix}", headers=headers,
            timeout=(3.05, 15), allow_redirects=False, stream=True, **kwargs,
        )

    def save(self, name, data):
        if len(data) > self.limit:
            raise CoverStorageError("Cover exceeds the storage limit.")
        try:
            with self._request("POST", "/" + name, data=data, headers={
                "Content-Type": "image/webp", "Cache-Control": "private, no-store",
                "x-upsert": "false",
            }) as response:
                if response.status_code not in (200, 201):
                    raise CoverStorageError("Cover storage is temporarily unavailable.")
        except requests.RequestException:
            raise CoverStorageError("Cover storage is temporarily unavailable.") from None

    def read(self, name):
        try:
            with self._request("GET", "/" + name) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise CoverStorageError("Cover storage is temporarily unavailable.")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if content_type != "image/webp":
                    raise CoverStorageError("Stored cover could not be read.")
                declared = response.headers.get("Content-Length")
                if declared is not None:
                    try:
                        if int(declared) < 0 or int(declared) > self.limit:
                            raise CoverStorageError("Stored cover exceeds the storage limit.")
                    except ValueError:
                        raise CoverStorageError("Stored cover could not be read.") from None
                data = bytearray()
                deadline = time.monotonic() + 20
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if time.monotonic() > deadline or len(data) + len(chunk) > self.limit:
                        raise CoverStorageError("Stored cover exceeds the storage limit.")
                    data.extend(chunk)
                if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
                    raise CoverStorageError("Stored cover could not be read.")
                return bytes(data)
        except requests.RequestException:
            raise CoverStorageError("Cover storage is temporarily unavailable.") from None

    def delete(self, name):
        try:
            with self._request("DELETE", json={"prefixes": [name]}) as response:
                if response.status_code not in (200, 204, 404):
                    raise CoverStorageError("Cover storage is temporarily unavailable.")
        except requests.RequestException:
            raise CoverStorageError("Cover storage is temporarily unavailable.") from None
