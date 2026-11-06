
"""Persistence for user-owned GitHub App installations.

Uses Supabase PostgreSQL when DATABASE_URL is configured.
Falls back to SQLite for local development.

Installation access tokens and GitHub private keys are never stored here.
Only encrypted installation references are persisted.
"""

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
from github_integration.security import (
    encrypt_installation_id,
    decrypt_installation_id,
    hash_oauth_state,
    hash_installation_id,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _use_postgres() -> bool:
    return os.getenv("DATABASE_URL", "").strip().startswith(
        ("postgresql://", "postgres://")
    )


def _db_path() -> Path:
    configured = os.getenv("GITHUB_INTEGRATION_DB_PATH", "").strip()
    path = (
        Path(configured)
        if configured
        else PROJECT_ROOT / "storage" / "github_integrations.db"
    )
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

_SQLITE_SCHEMA = """
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

_POSTGRES_SCHEMA = """
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
"""

class _PostgresConnection:
    """Small adapter for the existing SQLite-style execute calls."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        # Convert SQLite placeholders to psycopg2 placeholders.
        sql = sql.replace("?", "%s")
        cursor = self.connection.cursor()
        cursor.execute(sql, params)
        return cursor

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


@contextmanager
def _connection():
    if _use_postgres():
        import psycopg2
        from psycopg2.extras import RealDictCursor

        raw = psycopg2.connect(
            os.environ["DATABASE_URL"],
            cursor_factory=RealDictCursor,
            connect_timeout=10,
            sslmode="require",
        )
        conn = _PostgresConnection(raw)

        try:
            with raw.cursor() as cursor:
                cursor.execute(_POSTGRES_SCHEMA)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    else:
        conn = sqlite3.connect(_db_path(), timeout=15)
        conn.row_factory = sqlite3.Row

        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SQLITE_SCHEMA)
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
            """
            INSERT INTO github_oauth_states
                (state_hash, user_id, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (hash_oauth_state(state), user_id, expires_at.isoformat(), now),
        )


def consume_oauth_state(state: str) -> str | None:
    """Consume an OAuth state exactly once."""
    state_hash = hash_oauth_state(state)
    now = datetime.now(timezone.utc).isoformat()

    with _connection() as conn:
        if _use_postgres():
            row = conn.execute(
                """
                SELECT user_id, expires_at
                FROM github_oauth_states
                WHERE state_hash = ?
                FOR UPDATE
                """,
                (state_hash,),
            ).fetchone()
        else:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT user_id, expires_at
                FROM github_oauth_states
                WHERE state_hash = ?
                """,
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
            """
            INSERT INTO github_pending_installations
                (state_hash, user_id, installation_id, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(state_hash) DO UPDATE SET
                user_id = excluded.user_id,
                installation_id = excluded.installation_id,
                expires_at = excluded.expires_at,
                created_at = excluded.created_at
            """,
            (
                hash_oauth_state(state),
                user_id,
                encrypt_installation_id(installation_id),
                expires_at.isoformat(),
                now,
            ),
        )


def _to_utc(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def get_pending_installation(user_id: str, state: str) -> int | None:
    now = datetime.now(timezone.utc)

    with _connection() as conn:
        row = conn.execute(
            """
            SELECT installation_id, expires_at
            FROM github_pending_installations
            WHERE state_hash = ? AND user_id = ?
            """,
            (hash_oauth_state(state), user_id),
        ).fetchone()

    if row is None or _to_utc(row["expires_at"]) <= now:
        return None

    stored_id = str(row["installation_id"])

    try:
        return decrypt_installation_id(stored_id)
    except RuntimeError:
        if not stored_id.isdigit():
            raise

        installation_id = int(stored_id)
        encrypted = encrypt_installation_id(installation_id)

        with _connection() as conn:
            conn.execute(
                """
                UPDATE github_pending_installations
                SET installation_id = ?
                WHERE state_hash = ? AND user_id = ?
                """,
                (encrypted, hash_oauth_state(state), user_id),
            )

        return installation_id

def delete_pending_installation(user_id: str, state: str) -> None:
    with _connection() as conn:
        conn.execute(
            """
            DELETE FROM github_pending_installations
            WHERE state_hash = ? AND user_id = ?
            """,
            (hash_oauth_state(state), user_id),
        )


def save_installation(
    user_id: str,
    installation_id: int,
    account_login: str,
    account_type: str = "",
    account_id: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    encrypted = encrypt_installation_id(installation_id)
    installation_hash = hash_installation_id(installation_id)

    if _use_postgres():
        import psycopg2

        if not account_id:
            raise ValueError("GitHub account ID is required for PostgreSQL.")

        normalized_type = account_type.strip().title()
        if normalized_type not in ("User", "Organization"):
            raise ValueError("Invalid GitHub account type.")

        encrypted_bytes = psycopg2.Binary(encrypted.encode("utf-8"))
        record_id = str(uuid.uuid4())

        with _connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO github_installations (
                    id,
                    user_id,
                    installation_id_encrypted,
                    installation_id_hash,
                    github_account_id,
                    github_account_login,
                    github_account_type,
                    status,
                    confirmed_at,
                    pending_expires_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?::uuid,
                    ?::uuid,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?::githubaccounttype,
                    'active'::installationstatus,
                    NOW(),
                    NOW(),
                    NOW(),
                    NOW()
                )
                ON CONFLICT (installation_id_hash) DO UPDATE SET
                    installation_id_encrypted = EXCLUDED.installation_id_encrypted,
                    github_account_id = EXCLUDED.github_account_id,
                    github_account_login = EXCLUDED.github_account_login,
                    github_account_type = EXCLUDED.github_account_type,
                    status = 'active'::installationstatus,
                    confirmed_at = NOW(),
                    pending_expires_at = NOW(),
                    updated_at = NOW()
                WHERE github_installations.user_id = EXCLUDED.user_id
                """,
                (
                    record_id,
                    user_id,
                    encrypted_bytes,
                    installation_hash,
                    str(account_id),
                    account_login,
                    normalized_type,
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError(
                    "This GitHub installation is already linked to another user."
                )

    else:
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
                    user_id,
                    installation_hash,
                    encrypted,
                    account_login,
                    account_type,
                    now,
                    now,
                ),
            )


def list_installations(user_id: str) -> list[dict]:
    if _use_postgres():
        with _connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    installation_id_encrypted,
                    github_account_login AS account_login,
                    github_account_type::text AS account_type,
                    created_at,
                    updated_at
                FROM github_installations
                WHERE user_id = ?::uuid
                  AND status = 'active'::installationstatus
                ORDER BY created_at
                """,
                (user_id,),
            ).fetchall()

        installations = []

        for row in rows:
            encrypted_value = row["installation_id_encrypted"]

            if isinstance(encrypted_value, memoryview):
                encrypted_value = encrypted_value.tobytes()

            if isinstance(encrypted_value, bytes):
                encrypted_value = encrypted_value.decode("utf-8")

            try:
                installation_id = decrypt_installation_id(encrypted_value)
            except Exception:
                logger.warning(
                    "list_installations: skipping %s, could not decrypt",
                    row["account_login"],
                )
                continue

            installations.append({
                "installation_id": installation_id,
                "account_login": row["account_login"],
                "account_type": row["account_type"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })

        return installations

    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT installation_id_hash, encrypted_installation_id,
                   account_login, account_type, created_at, updated_at
            FROM github_installations
            WHERE user_id = ?
            ORDER BY created_at
            """,
            (user_id,),
        ).fetchall()

    return [
        {
            "installation_id": decrypt_installation_id(
                row["encrypted_installation_id"]
            ),
            "account_login": row["account_login"],
            "account_type": row["account_type"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]

def get_installation(user_id: str, installation_id: int) -> dict | None:
    installation_hash = hash_installation_id(installation_id)

    if _use_postgres():
        with _connection() as conn:
            row = conn.execute(
                """
                SELECT
                    installation_id_encrypted,
                    github_account_login AS account_login,
                    github_account_type::text AS account_type
                FROM github_installations
                WHERE user_id = ?::uuid
                  AND installation_id_hash = ?
                  AND status = 'active'::installationstatus
                """,
                (user_id, installation_hash),
            ).fetchone()

        if row is None:
            return None

        encrypted_value = row["installation_id_encrypted"]

        if isinstance(encrypted_value, memoryview):
            encrypted_value = encrypted_value.tobytes()

        if isinstance(encrypted_value, bytes):
            encrypted_value = encrypted_value.decode("utf-8")

        return {
            "installation_id": decrypt_installation_id(encrypted_value),
            "account_login": row["account_login"],
            "account_type": row["account_type"],
        }

    with _connection() as conn:
        row = conn.execute(
            """
            SELECT encrypted_installation_id, account_login, account_type
            FROM github_installations
            WHERE user_id = ? AND installation_id_hash = ?
            """,
            (user_id, installation_hash),
        ).fetchone()

    if row is None:
        return None

    return {
        "installation_id": decrypt_installation_id(
            row["encrypted_installation_id"]
        ),
        "account_login": row["account_login"],
        "account_type": row["account_type"],
    }


def delete_installation(user_id: str, installation_id: int) -> bool:
    installation_hash = hash_installation_id(installation_id)

    if _use_postgres():
        with _connection() as conn:
            cursor = conn.execute(
                """
                UPDATE github_installations
                SET status = 'revoked'::installationstatus,
                    updated_at = NOW()
                WHERE user_id = ?::uuid
                  AND installation_id_hash = ?
                  AND status = 'active'::installationstatus
                """,
                (user_id, installation_hash),
            )
            return cursor.rowcount == 1

    with _connection() as conn:
        cursor = conn.execute(
            """
            DELETE FROM github_installations
            WHERE user_id = ? AND installation_id_hash = ?
            """,
            (user_id, installation_hash),
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
        if installation_id is not None
        else None
    )

    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO github_integration_audit
                (user_id, event, installation_id_hash, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                event,
                installation_hash,
                details[:1000],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
