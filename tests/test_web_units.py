from __future__ import annotations

import httpx
import pytest

from hazard_ext.web.config import Settings
from hazard_ext.web.db import init_db, make_engine, make_session_factory
from hazard_ext.web.security import (LoginThrottle, SessionSigner, hash_password, password_problem,
                                     verify_password)
from hazard_ext.web.storage import (DbBlobStore, LocalBlobStore, StorageError, SupabaseBlobStore,
                                    make_blob_store)


# ----------------------------------------------------------------- security
def test_password_hash_round_trip():
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$") and "correct" not in hashed
    assert verify_password("correct horse battery", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("anything", None)            # unknown user
    assert not verify_password("anything", "not-a-hash")


def test_password_rule():
    assert password_problem("short") and password_problem("long-enough-1") is None


def test_session_cookie_is_signed_and_tied_to_the_password():
    signer = SessionSigner("secret", 3600)
    token = signer.make(7, "hash-one")
    data = signer.read(token)
    assert data["uid"] == 7 and signer.matches(data, "hash-one")
    assert not signer.matches(data, "hash-two")              # password changed
    assert signer.read(token[:-2] + ("aa" if not token.endswith("aa") else "bb")) is None   # tampered
    assert SessionSigner("other-secret", 3600).read(token) is None
    assert signer.read(None) is None and signer.read("") is None
    assert SessionSigner("secret", -1).read(token) is None   # expired


def test_login_throttle_blocks_after_repeated_failures():
    t = LoginThrottle(max_failures=3, window_seconds=60)
    for _ in range(3):
        assert not t.blocked("a")
        t.fail("a")
    assert t.blocked("a") and not t.blocked("b")
    t.reset("a")
    assert not t.blocked("a")
    assert not t._fails                    # checking a key does not store it


def test_app_refuses_to_start_on_postgres_with_the_default_secret():
    from hazard_ext.web.main import create_app
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        create_app(Settings(_env_file=None, database_url="postgresql://u:p@localhost/db"))


def test_supabase_transport_errors_become_storage_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    store = SupabaseBlobStore("https://ref.supabase.co", "sb_secret_x", "b",
                              client=httpx.Client(transport=httpx.MockTransport(handler)))
    for call in (lambda: store.put("a", b"x"), lambda: store.get("a"), lambda: store.delete("a")):
        with pytest.raises(StorageError, match="could not be reached"):
            call()


# ------------------------------------------------------------------ storage
def test_local_blob_store(tmp_path):
    store = LocalBlobStore(str(tmp_path))
    store.put("p1/abc.npz", b"\x00\x01data")
    assert store.get("p1/abc.npz") == b"\x00\x01data"
    store.delete("p1/abc.npz")
    store.delete("p1/abc.npz")                               # deleting twice is fine
    with pytest.raises(StorageError, match="not found"):
        store.get("p1/abc.npz")


@pytest.mark.parametrize("key", ["../escape", "/abs", "a b", "p1/../../x", ""])
def test_storage_keys_cannot_leave_the_store(tmp_path, key):
    with pytest.raises(StorageError, match="Invalid storage key"):
        LocalBlobStore(str(tmp_path)).put(key, b"x")


def test_db_blob_store(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'x.db').as_posix()}", storage_backend="db")
    engine = make_engine(settings)
    init_db(engine, settings)
    store = make_blob_store(settings, make_session_factory(engine))
    assert isinstance(store, DbBlobStore)
    store.put("p1/a.npz", b"one")
    store.put("p1/a.npz", b"two")                            # overwrite
    assert store.get("p1/a.npz") == b"two"
    store.delete("p1/a.npz")
    with pytest.raises(StorageError):
        store.get("p1/a.npz")
    engine.dispose()


@pytest.mark.parametrize("key,expect_bearer", [("sb_secret_abc", False), ("eyJhbGciOi.legacy.jwt", True)])
def test_supabase_blob_store_requests(key, expect_bearer):
    seen = []
    objects = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if request.method == "POST":
            objects[path] = request.content
            return httpx.Response(200, json={"Key": path})
        if request.method == "GET":
            return httpx.Response(200, content=objects[path]) if path in objects else httpx.Response(404, json={"error": "not found"})
        objects.pop(path, None)
        return httpx.Response(200, json={})

    store = SupabaseBlobStore("https://ref.supabase.co/", key, "hazard-data",
                              client=httpx.Client(transport=httpx.MockTransport(handler)))
    store.put("p1/a.npz", b"payload")
    assert store.get("p1/a.npz") == b"payload"
    store.delete("p1/a.npz")
    with pytest.raises(StorageError, match="404"):
        store.get("p1/a.npz")

    up = seen[0]
    assert str(up.url) == "https://ref.supabase.co/storage/v1/object/hazard-data/p1/a.npz"
    assert up.headers["apikey"] == key and up.headers["x-upsert"] == "true"
    assert ("authorization" in up.headers) is expect_bearer


def test_supabase_backend_needs_its_settings():
    settings = Settings(_env_file=None, storage_backend="supabase")
    assert any("SUPABASE_URL" in p for p in settings.problems())
    with pytest.raises(StorageError):
        make_blob_store(settings, None)


def test_settings_accept_supabase_connection_strings():
    s = Settings(_env_file=None, database_url="postgresql://postgres:pw@db.ref.supabase.co:5432/postgres")
    assert s.sqlalchemy_url == "postgresql+psycopg://postgres:pw@db.ref.supabase.co:5432/postgres"
    assert not s.is_sqlite
    assert any("SESSION_SECRET" in p for p in s.fatal_problems())      # the default secret is refused
    assert any("COOKIE_SECURE" in p for p in s.problems())
    ok = Settings(_env_file=None, database_url=s.database_url, session_secret="x" * 40, cookie_secure=True)
    assert not ok.fatal_problems() and not ok.problems()
    assert Settings(_env_file=None, db_schema="hazard; drop").fatal_problems()
    assert Settings(_env_file=None, database_url="postgres://u:p@h/db").sqlalchemy_url.startswith("postgresql+psycopg://")
