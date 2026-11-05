
"""
FastAPI dependencies for Engineer Mode entitlement checks.

All user identity comes from the authenticated Supabase JWT.
"""

from fastapi import Depends, HTTPException, Request

from auth.dependencies import AuthenticatedUser, get_current_user
from entitlements.service import (
    check_access,
    start_trial_if_new,
    AccessDeniedError,
)
from entitlements.security import trial_start_limiter


async def require_engineer_mode_access(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> str:
    """
    Gate Engineer Mode routes using the authenticated user's entitlement.

    Returns the service tier when access is allowed.
    Raises 402 when access has expired and 403 when no entitlement exists.
    """

    user_id = user.id

    try:
        return check_access(user_id)
    except AccessDeniedError as exc:
        code = 402 if "expired" in exc.message.lower() else 403
        raise HTTPException(
            status_code=code,
            detail=exc.message,
        ) from exc


async def start_trial_endpoint(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Start a trial for the authenticated user.

    Rate-limited to prevent repeated trial-start attempts.
    """

    user_id = user.id

    if not trial_start_limiter.allow(user_id):
        raise HTTPException(
            status_code=429,
            detail="Too many requests, try again shortly.",
        )

    secondary_signal = request.headers.get("X-Device-Signal")

    status = start_trial_if_new(
        user_id,
        secondary_signal_raw=secondary_signal,
    )

    return {"status": status.value}
