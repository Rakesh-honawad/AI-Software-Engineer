"""Authenticated GitHub App installation and repository discovery routes."""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi.responses import RedirectResponse
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth.dependencies import AuthenticatedUser, get_current_user
from github_integration import store
from github_integration.client import (
    get_installation as github_get_installation,
    list_installation_repositories,
)
from github_integration.config import load_config

router = APIRouter(prefix="/auth/github", tags=["github-integration"])


class ConfirmInstallationRequest(BaseModel):
    state: str = Field(min_length=32, max_length=512)


def _verified_installation(installation_id: int) -> dict:
    try:
        config = load_config()
        installation = github_get_installation(installation_id)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not verify the GitHub App installation.",
        ) from exc

    if str(installation.get("app_id")) != str(config.app_id):
        raise HTTPException(
            status_code=403,
            detail="This installation does not belong to the configured GitHub App.",
        )

    if not installation.get("account_login"):
        raise HTTPException(
            status_code=502,
            detail="GitHub returned incomplete installation account information.",
        )

    return installation


@router.get("/install")
def begin_installation(user: AuthenticatedUser = Depends(get_current_user)):
    try:
        config = load_config()
        store.init_db()

        state = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=config.pending_ttl_minutes
        )

        store.create_oauth_state(user.id, state, expires_at)
        store.audit_event(user.id, "installation_started")

        return {
            "install_url": (
                f"https://github.com/apps/{config.app_slug}/installations/new"
                f"?state={state}"
            ),
            "expires_at": expires_at.isoformat(),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="GitHub App installation is not currently configured.",
        ) from exc


@router.get("/callback")
def installation_callback(
    installation_id: int = Query(gt=0),
    setup_action: str = Query(),
    state: str = Query(min_length=32, max_length=512),
):
    if setup_action not in {"install", "update"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported GitHub App setup action.",
        )

    try:
        user_id = store.consume_oauth_state(state)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not validate the GitHub installation state.",
        ) from exc

    if not user_id:
        raise HTTPException(
            status_code=400,
            detail="Installation state is invalid, expired, or already used.",
        )

    installation = _verified_installation(installation_id)

    try:
        config = load_config()
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=config.pending_ttl_minutes
        )

        store.save_pending_installation(
            user_id=user_id,
            installation_id=installation_id,
            state=state,
            expires_at=expires_at,
        )
        store.audit_event(
            user_id,
            "installation_pending_confirmation",
            installation_id=installation_id,
            details=f"account={installation['account_login']}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not save the pending GitHub installation.",
        ) from exc

    # Redirect to the frontend when configured. Keep the JSON response
    # as the default so existing API clients and tests remain compatible.
    frontend_url = getattr(config, "frontend_url", "").strip().rstrip("/")
    if frontend_url:
        if not frontend_url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=500,
                detail="GITHUB_FRONTEND_URL must be an HTTP or HTTPS URL.",
            )

        return RedirectResponse(
            url=f"{frontend_url}/connect?github_state={state}",
            status_code=303,
        )

    return {
        "status": "pending_confirmation",
        "state": state,
        "account_login": installation["account_login"],
        "account_type": installation["account_type"],
        "expires_at": expires_at.isoformat(),
        "confirmation_required": True,
    }


@router.post("/confirm")
def confirm_installation(
    request: ConfirmInstallationRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        installation_id = store.get_pending_installation(user.id, request.state)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not read the pending GitHub installation.",
        ) from exc

    if installation_id is None:
        raise HTTPException(
            status_code=404,
            detail="No valid pending installation was found for this user.",
        )

    installation = _verified_installation(installation_id)

    try:
        store.save_installation(
            user_id=user.id,
            installation_id=installation_id,
            account_login=installation["account_login"],
            account_type=installation["account_type"],
        )
        store.delete_pending_installation(user.id, request.state)
        store.audit_event(
            user.id,
            "installation_confirmed",
            installation_id=installation_id,
            details=f"account={installation['account_login']}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not confirm the GitHub installation.",
        ) from exc

    return {
        "connected": True,
        "installation_id": installation_id,
        "account_login": installation["account_login"],
        "account_type": installation["account_type"],
    }


@router.get("/installations")
def get_installations(user: AuthenticatedUser = Depends(get_current_user)):
    try:
        return {"installations": store.list_installations(user.id)}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not load GitHub installations.",
        ) from exc


@router.get("/installations/{installation_id}/repositories")
def get_installation_repositories(
    installation_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
):
    if installation_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid installation ID.")

    try:
        owned = store.get_installation(user.id, installation_id)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not verify installation ownership.",
        ) from exc

    if owned is None:
        raise HTTPException(
            status_code=404,
            detail="GitHub installation not found for this user.",
        )

    _verified_installation(installation_id)

    try:
        repositories = list_installation_repositories(installation_id)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not list repositories from GitHub.",
        ) from exc

    store.audit_event(
        user.id,
        "installation_repositories_listed",
        installation_id=installation_id,
        details=f"repository_count={len(repositories)}",
    )
    return {"repositories": repositories}


@router.delete("/installations/{installation_id}")
def disconnect_installation(
    installation_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
):
    if installation_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid installation ID.")

    try:
        deleted = store.delete_installation(user.id, installation_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail="GitHub installation not found for this user.",
            )

        store.audit_event(
            user.id,
            "installation_disconnected",
            installation_id=installation_id,
        )
        return {"disconnected": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not disconnect the GitHub installation.",
        ) from exc
