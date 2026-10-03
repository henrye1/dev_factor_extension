"""Blob storage for parsed datasets: local folder, database table, or Supabase Storage."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

import httpx
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Blob

_KEY = re.compile(r"^[A-Za-z0-9_\-./]+$")


class StorageError(RuntimeError):
    pass


def _check(key: str) -> str:
    if not _KEY.match(key) or ".." in key or key.startswith("/"):
        raise StorageError(f"Invalid storage key: {key!r}")
    return key


class BlobStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalBlobStore:
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / _check(key)

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise StorageError(f"Stored data not found: {key}")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class DbBlobStore:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def put(self, key: str, data: bytes) -> None:
        with self.session_factory.begin() as s:
            s.merge(Blob(key=_check(key), data=data))

    def get(self, key: str) -> bytes:
        with self.session_factory() as s:
            row = s.get(Blob, _check(key))
            if row is None:
                raise StorageError(f"Stored data not found: {key}")
            return bytes(row.data)

    def delete(self, key: str) -> None:
        with self.session_factory.begin() as s:
            row = s.get(Blob, _check(key))
            if row is not None:
                s.delete(row)


class SupabaseBlobStore:
    """Supabase Storage over its REST API, using a server-side secret (or service_role) key.

    The bucket must exist and be private. The key never leaves the server.
    """

    def __init__(self, url: str, key: str, bucket: str, client: httpx.Client | None = None):
        self.base = f"{url.rstrip('/')}/storage/v1/object/{bucket}"
        headers = {"apikey": key}
        if key.startswith("eyJ"):                 # legacy JWT service_role key
            headers["Authorization"] = f"Bearer {key}"
        self.client = client or httpx.Client(timeout=60.0)
        self.headers = headers

    def _url(self, key: str) -> str:
        return f"{self.base}/{_check(key)}"

    def _send(self, method: str, key: str, **kw) -> httpx.Response:
        try:
            return self.client.request(method, self._url(key), **kw)
        except httpx.HTTPError as exc:
            raise StorageError(f"Supabase Storage could not be reached: {exc}") from None

    def put(self, key: str, data: bytes) -> None:
        r = self._send("POST", key, content=data, headers={
            **self.headers, "x-upsert": "true", "Content-Type": "application/octet-stream"})
        if r.status_code >= 300:
            raise StorageError(f"Supabase Storage upload failed ({r.status_code}): {r.text[:300]}")

    def get(self, key: str) -> bytes:
        r = self._send("GET", key, headers=self.headers)
        if r.status_code >= 300:
            raise StorageError(f"Supabase Storage download failed ({r.status_code}): {r.text[:300]}")
        return r.content

    def delete(self, key: str) -> None:
        r = self._send("DELETE", key, headers=self.headers)
        if r.status_code >= 300 and r.status_code != 404:
            raise StorageError(f"Supabase Storage delete failed ({r.status_code}): {r.text[:300]}")


def make_blob_store(settings: Settings, session_factory: sessionmaker[Session]) -> BlobStore:
    if settings.storage_backend == "local":
        return LocalBlobStore(settings.local_data_dir)
    if settings.storage_backend == "db":
        return DbBlobStore(session_factory)
    if not (settings.supabase_url and settings.supabase_service_key):
        raise StorageError("STORAGE_BACKEND=supabase needs SUPABASE_URL and SUPABASE_SERVICE_KEY")
    return SupabaseBlobStore(settings.supabase_url, settings.supabase_service_key, settings.supabase_bucket)
