
"""
entitlements/routes.py

Authenticated entitlement endpoints for the Knowledge Engine.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from auth.dependencies import AuthenticatedUser, get_current_user
from entitlements.security import trial_start_limiter
from entitlements.service import start_trial_if_new

router = APIRouter(prefix="/entitlements", tags=["entitlements"])


@router.post("/start-trial")
def start_trial(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Called by the frontend before the user's first AI action.

    Idempotent: repeated calls for the same authenticated user do not
    start additional trials.

    Rate-limited to prevent abuse (5 calls/60s per user).
    """
    user_id = user.id

    if not trial_start_limiter.allow(user_id):
        raise HTTPException(
            status_code=429,
            detail="Too many requests, try again shortly.",
        )

    status = start_trial_if_new(user_id)

    return {"status": status.value}
