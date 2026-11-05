"""
security.py

Everything here exists to answer one question: "can this be faked or
replayed by someone who controls the client?" The rule followed throughout:
the server's own clock and the server's own database are the only sources
of truth. The client never gets to assert a date, a trial state, or an
identity beyond its authenticated Supabase user_id.
"""

import hashlib
import hmac
import os
import time
from collections import defaultdict, deque

SECRET_KEY = os.getenv("ENTITLEMENT_SIGNING_SECRET")
if not SECRET_KEY:
    raise RuntimeError(
        "ENTITLEMENT_SIGNING_SECRET is not set. Generate one with "
        "`python -c \"import secrets; print(secrets.token_hex(32))\"` "
        "and put it in your env — never hardcode it or check it into git."
    )


def sign_status_token(user_id: str, status: str, expires_at_epoch: int) -> str:
    """
    Produces a short-lived signed token the frontend can cache to avoid
    hitting the DB on every render (e.g. to show 'N days left in trial').
    This token is NEVER trusted for the actual access decision — every
    real request re-checks the DB via EntitlementService.check_access().
    It's a display convenience only, so tampering with it can't grant access.
    """
    payload = f"{user_id}|{status}|{expires_at_epoch}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def verify_status_token(token: str) -> tuple[str, str, int] | None:
    try:
        user_id, status, exp_str, sig = token.split("|")
        payload = f"{user_id}|{status}|{exp_str}"
        expected = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return user_id, status, int(exp_str)
    except (ValueError, AttributeError):
        return None


def hash_secondary_signal(raw_value: str) -> str:
    """
    Salted hash of an optional secondary abuse signal (e.g. device
    fingerprint or normalized email). Store only the hash, never the raw
    value — this is a "have we seen this before" check, not identity data.
    """
    return hmac.new(SECRET_KEY.encode(), raw_value.encode(), hashlib.sha256).hexdigest()


class SimpleRateLimiter:
    """
    In-memory sliding-window limiter for entitlement-check endpoints, so a
    scripted loop can't hammer /entitlements/start-trial trying user_id
    variations or retry races. Swap for a Redis-backed limiter if you scale
    past a single process — interface stays the same.
    """

    def __init__(self, max_calls: int = 10, window_seconds: int = 60):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window_seconds:
            q.popleft()
        if len(q) >= self.max_calls:
            return False
        q.append(now)
        return True


trial_start_limiter = SimpleRateLimiter(max_calls=5, window_seconds=60)