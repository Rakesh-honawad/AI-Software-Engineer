"""SQLite persistence for user-owned GitHub App installations.

Installation access tokens and GitHub private keys are never stored here.
Only encrypted installation references are persisted.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from github_integration.security import (
    encrypt_installation_id,
    decrypt_installation_id,
    hash_oauth_state,
    hash_installation_id,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _db_path() -> Path:
    configured = os.getenv("GITHUB_INTEGRATION_DB_PATH", "").strip()
    path = Path(configured) if configured else PROJECT_ROOT / "storage" / "github_integrations.db"
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS github_oauth_states (
    state_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS github_pending_installations (
    state_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    installation_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS github_installations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    installation_id_hash TEXT NOT NULL,
    encrypted_installation_id TEXT NOT NULL,
    account_login TEXT NOT NULL,
    account_type TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, installation_id_hash)
);

CREATE INDEX IF NOT EXISTS idx_github_installations_user
    ON github_installations(user_id);

CREATE TABLE IF NOT EXISTS github_integration_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    event TEXT NOT NULL,
    installation_id_hash TEXT,
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


@contextmanager
def _connection():
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _connection():
        pass


def create_oauth_state(user_id: str, state: str, expires_at: datetime) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connection() as conn:
        conn.execute(
            "INSERT INTO github_oauth_states "
            "(state_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (hash_oauth_state(state), user_id, expires_at.isoformat(), now),
        )


def consume_oauth_state(state: str) -> str | None:
    """Consume a state exactly once and return its authenticated user."""
    state_hash = hash_oauth_state(state)
    now = datetime.now(timezone.utc).isoformat()

    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT user_id, expires_at FROM github_oauth_states WHERE state_hash = ?",
            (state_hash,),
        ).fetchone()
        conn.execute(
            "DELETE FROM github_oauth_states WHERE state_hash = ?",
            (state_hash,),
        )

        if row is None or row["expires_at"] <= now:
            return None
        return str(row["user_id"])


def save_pending_installation(
    user_id: str,
    installation_id: int,
    state: str,
    expires_at: datetime,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO github_pending_installations "
            "(state_hash, user_id, installation_id, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                hash_oauth_state(state),
                user_id,
                encrypt_installation_id(installation_id),
                expires_at.isoformat(),
                now,
            ),
        )


def get_pending_installation(user_id: str, state: str) -> int | None:
    now = datetime.now(timezone.utc).isoformat()
    with _connection() as conn:
        row = conn.execute(
            "SELECT installation_id, expires_at "
            "FROM github_pending_installations "
            "WHERE state_hash = ? AND user_id = ?",
            (hash_oauth_state(state), user_id),
        ).fetchone()

    if row is None or row["expires_at"] <= now:
        return None

    stored_id = str(row["installation_id"])

    try:
        return decrypt_installation_id(stored_id)
    except RuntimeError:
        # Migrate a pending ID written by the previous plaintext implementation.
        if not stored_id.isdigit():
            raise

        installation_id = int(stored_id)
        encrypted = encrypt_installation_id(installation_id)

        with _connection() as conn:
            conn.execute(
                "UPDATE github_pending_installations "
                "SET installation_id = ? "
                "WHERE state_hash = ? AND user_id = ?",
                (encrypted, hash_oauth_state(state), user_id),
            )

        return installation_id


def delete_pending_installation(user_id: str, state: str) -> None:
    with _connection() as conn:
        conn.execute(
            "DELETE FROM github_pending_installations "
            "WHERE state_hash = ? AND user_id = ?",
            (hash_oauth_state(state), user_id),
        )


def save_installation(
    user_id: str,
    installation_id: int,
    account_login: str,
    account_type: str = "",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    encrypted = encrypt_installation_id(installation_id)
    installation_hash = hash_installation_id(installation_id)

    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO github_installations
                (user_id, installation_id_hash, encrypted_installation_id,
                 account_login, account_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, installation_id_hash) DO UPDATE SET
                encrypted_installation_id = excluded.encrypted_installation_id,
                account_login = excluded.account_login,
                account_type = excluded.account_type,
                updated_at = excluded.updated_at
            """,
            (
                user_id, installation_hash, encrypted,
                account_login, account_type, now, now,
            ),
        )


def list_installations(user_id: str) -> list[dict]:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT installation_id_hash, encrypted_installation_id, "
            "account_login, account_type, created_at, updated_at "
            "FROM github_installations WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ).fetchall()

    result = []
    for row in rows:
        result.append({
            "installation_id": decrypt_installation_id(row["encrypted_installation_id"]),
            "account_login": row["account_login"],
            "account_type": row["account_type"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
    return result


def get_installation(user_id: str, installation_id: int) -> dict | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT encrypted_installation_id, account_login, account_type "
            "FROM github_installations "
            "WHERE user_id = ? AND installation_id_hash = ?",
            (user_id, hash_installation_id(installation_id)),
        ).fetchone()

    if row is None:
        return None

    return {
        "installation_id": decrypt_installation_id(row["encrypted_installation_id"]),
        "account_login": row["account_login"],
        "account_type": row["account_type"],
    }


def delete_installation(user_id: str, installation_id: int) -> bool:
    with _connection() as conn:
        cursor = conn.execute(
            "DELETE FROM github_installations "
            "WHERE user_id = ? AND installation_id_hash = ?",
            (user_id, hash_installation_id(installation_id)),
        )
        return cursor.rowcount == 1


def audit_event(
    user_id: str,
    event: str,
    installation_id: int | None = None,
    details: str = "",
) -> None:
    installation_hash = (
        hash_installation_id(installation_id)
        if installation_id is not None else None
    )
    with _connection() as conn:
        conn.execute(
            "INSERT INTO github_integration_audit "
            "(user_id, event, installation_id_hash, details, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                user_id, event, installation_hash, details[:1000],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
