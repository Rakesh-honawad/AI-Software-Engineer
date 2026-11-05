"""Encryption helpers for GitHub installation references and OAuth state."""

import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def get_fernet() -> Fernet:
    key = os.getenv("GITHUB_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("GITHUB_ENCRYPTION_KEY is not configured.")
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("GITHUB_ENCRYPTION_KEY is invalid.") from exc


def encrypt_installation_id(installation_id: int | str) -> str:
    return get_fernet().encrypt(str(installation_id).encode()).decode()


def decrypt_installation_id(encrypted: str) -> int:
    try:
        value = get_fernet().decrypt(encrypted.encode()).decode()
        return int(value)
    except (InvalidToken, ValueError, TypeError) as exc:
        raise RuntimeError("Could not decrypt the GitHub installation reference.") from exc


def hash_oauth_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def hash_installation_id(installation_id: int | str) -> str:
    return hashlib.sha256(str(installation_id).encode("utf-8")).hexdigest()
