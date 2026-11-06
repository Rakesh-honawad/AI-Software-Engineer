"""Authenticated GitHub App installation and repository discovery routes."""

import secrets
from datetime import datetime, timedelta, timezone
import logging
logger = logging.getLogger(__name__)
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

    if not installation.get("account_login") or not installation.get("account_id"):
        raise HTTPException(
            status_code=502,
            detail="GitHub returned incomplete installation account information.",
        )

    if installation.get("account_type") not in ("User", "Organization"):
        raise HTTPException(
            status_code=502,
            detail="GitHub returned an unsupported installation account type.",
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
    # Step 1: read the pending installation
    try:
        installation_id = store.get_pending_installation(user.id, request.state)
    except Exception as exc:
        logger.exception("confirm: get_pending_installation failed")
        raise HTTPException(
            status_code=503,
            detail="Could not read the pending GitHub installation.",
        ) from exc

    if installation_id is None:
        raise HTTPException(
            status_code=404,
            detail="No valid pending installation was found for this user.",
        )

    # Step 2: verify with GitHub
    installation = _verified_installation(installation_id)

    # Step 3: save the installation
    try:
        save_kwargs = {
            "user_id": user.id,
            "installation_id": installation_id,
            "account_login": installation["account_login"],
            "account_type": installation["account_type"],
        }
        if installation.get("account_id") is not None:
            save_kwargs["account_id"] = installation["account_id"]

        store.save_installation(**save_kwargs)
    except Exception as exc:
        logger.exception("confirm: save_installation failed")
        raise HTTPException(
            status_code=503,
            detail="Could not confirm the GitHub installation.",
        ) from exc

    # Step 4: cleanup and audit. A failure here must not undo the connection.
    try:
        store.delete_pending_installation(user.id, request.state)
    except Exception:
        logger.exception("confirm: delete_pending_installation failed")

    try:
        store.audit_event(
            user.id,
            "installation_confirmed",
            installation_id=installation_id,
            details=f"account={installation['account_login']}",
        )
    except Exception:
        logger.exception("confirm: audit_event failed")

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

@router.get("/debug")
def github_debug(installation_id: int = 0):
    import os

    out = {}
    for name in [
        "GITHUB_APP_ID", "GITHUB_APP_SLUG", "GITHUB_CLIENT_ID",
        "GITHUB_CLIENT_SECRET", "GITHUB_STATE_SECRET",
        "GITHUB_ENCRYPTION_KEY", "GITHUB_FRONTEND_URL", "DATABASE_URL",
    ]:
        out[f"env_{name}_set"] = bool(os.getenv(name, "").strip())

    path = os.getenv("GITHUB_PRIVATE_KEY_PATH", "").strip()
    if path.startswith("-----"):
        out["key_source"] = "key text in env var"
    else:
        out["key_path"] = path
        out["key_file_exists"] = os.path.exists(path)
        if os.path.exists(path):
            with open(path) as f:
                text = f.read()
            out["key_first_line"] = text.splitlines()[0][:40] if text else ""
            out["key_line_count"] = len(text.splitlines())

    try:
        config = load_config()
        out["config_loaded"] = True
        out["app_id"] = str(config.app_id)
    except Exception as exc:
        out["config_loaded"] = False
        out["config_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return out

    try:
        with store._connection() as conn:
            conn.execute("SELECT 1").fetchone()
        out["database_ok"] = True
    except Exception as exc:
        out["database_ok"] = False
        out["database_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"

    if installation_id:
        try:
            inst = github_get_installation(installation_id)
            out["github_ok"] = True
            out["github_account"] = inst.get("account_login")
        except Exception as exc:
            out["github_ok"] = False
            out["github_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return out