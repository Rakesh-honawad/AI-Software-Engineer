"""
store.py

Persistence for entitlement records. Ships with SQLite so it runs with zero
extra setup; the SQL is plain enough to point at your Supabase Postgres
connection later (same schema, swap the `connect()` call and placeholder
style from `?` to `%s`).

The critical property this file guarantees: `start_trial_if_new()` can be
called a thousand times for the same user_id and the trial only ever starts
once. That's enforced by a UNIQUE constraint at the database level, not by
application logic — so it can't be raced or bypassed by concurrent requests.
"""

import sqlite3
import os
from datetime import datetime, timezone
from contextlib import contextmanager

from entitlements.config import Plan

DB_PATH = os.getenv("ENTITLEMENTS_DB_PATH", "entitlements.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entitlements (
    user_id TEXT PRIMARY KEY,
    plan TEXT NOT NULL,
    trial_started_at TEXT,
    trial_ends_at TEXT,
    subscription_started_at TEXT,
    subscription_ends_at TEXT,
    secondary_signal_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_secondary_signal
    ON entitlements(secondary_signal_hash);
"""


_schema_ensured = False


@contextmanager
def _conn():
    global _schema_ensured
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")  # safe concurrent access
    if not _schema_ensured:
        # Runs once per process (not once per call) — CREATE TABLE IF NOT
        # EXISTS is cheap and idempotent, but the flag avoids re-running it
        # on every single query. Makes this module self-healing: no
        # separate startup hook (FastAPI lifespan, etc.) is required for
        # the table to exist — first real use creates it.
        conn.executescript(_SCHEMA)
        conn.commit()
        _schema_ensured = True
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as conn:
        conn.executescript(_SCHEMA)


def _row_to_dict(row) -> dict:
    cols = ["user_id", "plan", "trial_started_at", "trial_ends_at",
            "subscription_started_at", "subscription_ends_at",
            "secondary_signal_hash", "created_at", "updated_at"]
    return dict(zip(cols, row))


def get_record(user_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT user_id, plan, trial_started_at, trial_ends_at, "
            "subscription_started_at, subscription_ends_at, "
            "secondary_signal_hash, created_at, updated_at "
            "FROM entitlements WHERE user_id = ?", (user_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def insert_trial_if_new(user_id: str, trial_days: int,
                         secondary_signal_hash: str | None = None) -> bool:
    """
    Returns True if a trial was newly started for this user, False if a
    record already existed (trial already used, or already subscribed —
    caller decides what that means via EntitlementRecord.status()).

    Uses INSERT ... ON CONFLICT DO NOTHING so this is safe under concurrent
    requests for the same user_id — exactly one of them wins the insert.
    """
    now = datetime.now(timezone.utc)
    ends = now.timestamp() + trial_days * 86400
    now_iso = now.isoformat()
    ends_iso = datetime.fromtimestamp(ends, tz=timezone.utc).isoformat()

    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO entitlements "
            "(user_id, plan, trial_started_at, trial_ends_at, "
            " subscription_started_at, subscription_ends_at, "
            " secondary_signal_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?) "
            "ON CONFLICT(user_id) DO NOTHING",
            (user_id, Plan.TRIAL.value, now_iso, ends_iso,
             secondary_signal_hash, now_iso, now_iso),
        )
        return cur.rowcount == 1


def has_secondary_signal_been_used(secondary_signal_hash: str) -> bool:
    """Cheap abuse check: has this device/email hash already claimed a trial?"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM entitlements WHERE secondary_signal_hash = ? LIMIT 1",
            (secondary_signal_hash,),
        ).fetchone()
    return row is not None


def upsert_subscription(user_id: str, plan: Plan, ends_at: datetime):
    now_iso = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO entitlements "
            "(user_id, plan, trial_started_at, trial_ends_at, "
            " subscription_started_at, subscription_ends_at, "
            " secondary_signal_hash, created_at, updated_at) "
            "VALUES (?, ?, NULL, NULL, ?, ?, NULL, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "plan=excluded.plan, "
            "subscription_started_at=excluded.subscription_started_at, "
            "subscription_ends_at=excluded.subscription_ends_at, "
            "updated_at=excluded.updated_at",
            (user_id, plan.value, now_iso, ends_at.isoformat(), now_iso, now_iso),
        )