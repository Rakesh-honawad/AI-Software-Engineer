"""Configuration for the GitHub App integration."""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class GitHubAppConfig:
    app_id: str
    client_id: str
    client_secret: str
    private_key_path: Path
    app_slug: str
    state_secret: str
    pending_ttl_minutes: int
    frontend_url: str = ""


def load_config() -> GitHubAppConfig:
    app_id = os.getenv("GITHUB_APP_ID", "").strip()
    private_key_value = os.getenv("GITHUB_PRIVATE_KEY_PATH", "").strip()
    app_slug = os.getenv("GITHUB_APP_SLUG", "").strip()

    if not app_id:
        raise RuntimeError("GITHUB_APP_ID is not configured.")
    if not private_key_value:
        raise RuntimeError("GITHUB_PRIVATE_KEY_PATH is not configured.")
    if not app_slug:
        raise RuntimeError("GITHUB_APP_SLUG is not configured.")

    private_key_path = Path(private_key_value).expanduser()
    if not private_key_path.is_absolute():
        private_key_path = PROJECT_ROOT / private_key_path
    private_key_path = private_key_path.resolve()

    if not private_key_path.is_file():
        raise RuntimeError("The configured GitHub App private key file does not exist.")

    try:
        ttl = int(os.getenv("GITHUB_PENDING_TTL_MINUTES", "10"))
    except ValueError as exc:
        raise RuntimeError("GITHUB_PENDING_TTL_MINUTES must be an integer.") from exc

    if not 1 <= ttl <= 60:
        raise RuntimeError("GITHUB_PENDING_TTL_MINUTES must be between 1 and 60.")

    return GitHubAppConfig(
        app_id=app_id,
        client_id=os.getenv("GITHUB_CLIENT_ID", "").strip(),
        client_secret=os.getenv("GITHUB_CLIENT_SECRET", "").strip(),
        private_key_path=private_key_path,
        app_slug=app_slug,
        state_secret=os.getenv("GITHUB_STATE_SECRET", "").strip(),
        pending_ttl_minutes=ttl,
        frontend_url=os.getenv("GITHUB_FRONTEND_URL", "").strip(),
    )
