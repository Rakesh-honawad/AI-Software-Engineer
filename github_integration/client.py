"""GitHub App JWT, installation-token, and repository API operations.

Installation tokens are short-lived and returned to the caller only.
They must never be persisted or logged.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from github_integration.config import load_config

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
TIMEOUT_SECONDS = 25


class GitHubAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"GitHub API returned HTTP {status_code}: {message}")


def create_app_jwt() -> str:
    """Create a short-lived JWT signed with the GitHub App private key."""
    config = load_config()
    private_key = config.private_key_path.read_text(encoding="utf-8")

    now = datetime.now(timezone.utc)
    claims = {
        "iat": int((now - timedelta(seconds=60)).timestamp()),
        "exp": int((now + timedelta(minutes=9)).timestamp()),
        "iss": config.app_id,
    }

    token = jwt.encode(claims, private_key, algorithm="RS256")
    return token.decode("utf-8") if isinstance(token, bytes) else token


def _request(
    method: str,
    path: str,
    token: str,
    *,
    token_type: str = "Bearer",
    params: dict | None = None,
    json_body: dict | None = None,
) -> Any:
    """Make a GitHub API request without exposing credentials in errors."""
    if not path.startswith("/"):
        raise ValueError("GitHub API path must start with '/'.")

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"{token_type} {token}",
        "X-GitHub-Api-Version": API_VERSION,
    }

    try:
        response = httpx.request(
            method,
            f"{API_BASE}{path}",
            headers=headers,
            params=params,
            json=json_body,
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError("Could not communicate with the GitHub API.") from exc

    if not response.is_success:
        message = "Request rejected."
        try:
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("message"), str):
                message = payload["message"][:300]
        except ValueError:
            pass
        raise GitHubAPIError(response.status_code, message)

    if not response.content:
        return None

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("GitHub API returned an invalid JSON response.") from exc


def get_installation(installation_id: int) -> dict:
    """Verify an installation using App-level authentication."""
    if installation_id <= 0:
        raise ValueError("Invalid GitHub installation ID.")

    app_jwt = create_app_jwt()
    result = _request(
        "GET",
        f"/app/installations/{installation_id}",
        app_jwt,
    )

    account = result.get("account") or {}
    return {
        "id": int(result["id"]),
        "account_login": str(account.get("login") or ""),
        "account_type": str(account.get("type") or ""),
        "app_id": int(result.get("app_id") or 0),
        "target_type": str(result.get("target_type") or ""),
    }


def create_installation_token(
    installation_id: int,
    repository_ids: list[int] | None = None,
) -> str:
    """Mint an ephemeral token, optionally restricted to specific repositories.

    When repository_ids is provided, the token is scoped to those repositories.
    When omitted, GitHub grants access to the repositories available to the
    installation; this is used only for repository discovery.
    """
    if installation_id <= 0:
        raise ValueError("Invalid GitHub installation ID.")

    body = {}
    if repository_ids is not None:
        if not repository_ids or any(
            not isinstance(repo_id, int) or repo_id <= 0
            for repo_id in repository_ids
        ):
            raise ValueError("repository_ids must contain positive integer IDs.")
        body["repository_ids"] = sorted(set(repository_ids))

    app_jwt = create_app_jwt()
    result = _request(
        "POST",
        f"/app/installations/{installation_id}/access_tokens",
        app_jwt,
        json_body=body,
    )

    token = result.get("token") if isinstance(result, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("GitHub did not return an installation access token.")

    # Deliberately do not write this token to disk, SQLite, or logs.
    return token


def list_installation_repositories(installation_id: int) -> list[dict]:
    """List repositories available to an installation.

    The discovery token exists only in memory and is not persisted.
    """
    token = create_installation_token(installation_id)
    repositories = []

    for page in range(1, 101):
        result = _request(
            "GET",
            "/installation/repositories",
            token,
            token_type="Bearer",
            params={"per_page": 100, "page": page},
        )

        batch = result.get("repositories", []) if isinstance(result, dict) else []
        for repo in batch:
            owner = repo.get("owner") or {}
            repositories.append({
                "id": int(repo["id"]),
                "name": str(repo.get("name") or ""),
                "full_name": str(repo.get("full_name") or ""),
                "owner_login": str(owner.get("login") or ""),
                "private": bool(repo.get("private", False)),
                "default_branch": str(repo.get("default_branch") or ""),
                "html_url": str(repo.get("html_url") or ""),
            })

        if len(batch) < 100:
            return repositories

    raise RuntimeError("Repository listing exceeded the pagination safety limit.")

def get_branch_commit_sha(
    owner: str,
    repo: str,
    branch: str,
    token: str,
) -> str:
    """Return the current commit SHA for a GitHub branch."""
    result = _request(
        "GET",
        f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/git/ref/heads/{quote(branch, safe='/')}",
        token,
        token_type="Bearer",
    )
    sha = (result.get("object") or {}).get("sha")
    if not isinstance(sha, str) or not sha:
        raise RuntimeError("GitHub returned no commit SHA for the branch.")
    return sha


def create_branch_ref(
    owner: str,
    repo: str,
    branch: str,
    sha: str,
    token: str,
) -> dict:
    """Create a new branch reference at an exact commit."""
    return _request(
        "POST",
        f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/git/refs",
        token,
        token_type="Bearer",
        json_body={
            "ref": f"refs/heads/{branch}",
            "sha": sha,
        },
    )
