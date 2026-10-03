"""Password hashing, signed session cookies and sign-in throttling."""
from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from itsdangerous import BadSignature, URLSafeTimedSerializer

COOKIE_NAME = "hazard_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "hazard-ext"
MIN_PASSWORD_LENGTH = 10

_hasher = PasswordHasher()                       # argon2id with library defaults
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-effort check: an unknown user still costs one hash verification."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"The password must be at least {MIN_PASSWORD_LENGTH} characters"
    return None


def _stamp(password_hash: str, session_version: int = 0) -> str:
    """Changes whenever the password changes or the user signs out, so old sessions stop working."""
    return hashlib.sha256(f"{password_hash}:{session_version}".encode()).hexdigest()[:16]


class SessionSigner:
    def __init__(self, secret: str, max_age_seconds: int):
        self._s = URLSafeTimedSerializer(secret, salt="hazard-session")
        self.max_age = max_age_seconds

    def make(self, user_id: int, password_hash: str, session_version: int = 0) -> str:
        return self._s.dumps({"uid": user_id, "ps": _stamp(password_hash, session_version)})

    def read(self, token: str | None) -> dict | None:
        if not token:
            return None
        try:
            data = self._s.loads(token, max_age=self.max_age)
        except BadSignature:                     # includes expired signatures
            return None
        return data if isinstance(data, dict) and "uid" in data else None

    @staticmethod
    def matches(data: dict, password_hash: str, session_version: int = 0) -> bool:
        return data.get("ps") == _stamp(password_hash, session_version)


class LoginThrottle:
    """Blocks further attempts for a key after too many failures in a time window."""

    def __init__(self, max_failures: int = 8, window_seconds: int = 900):
        self.max_failures = max_failures
        self.window = window_seconds
        self._fails: dict[str, deque] = defaultdict(deque)

    def _trim(self, key: str, now: float) -> deque:
        q = self._fails[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        key = key[:400]
        if key not in self._fails:                 # looking must not create an entry
            return False
        q = self._trim(key, time.monotonic())
        if not q:
            del self._fails[key]
            return False
        return len(q) >= self.max_failures

    def fail(self, key: str) -> None:
        key = key[:400]
        now = time.monotonic()
        if len(self._fails) > 50_000:              # bound memory under a flood of distinct keys
            for k in [k for k, q in self._fails.items() if not q or now - q[-1] > self.window]:
                del self._fails[k]
        self._trim(key, now).append(now)

    def reset(self, key: str) -> None:
        self._fails.pop(key[:400], None)
