
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError


bearer_scheme = HTTPBearer(auto_error=False)

ASYMMETRIC_ALGORITHMS = {"ES256", "ES384", "RS256", "EdDSA"}


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str | None
    claims: dict[str, Any]


@lru_cache(maxsize=1)
def get_jwks_client() -> PyJWKClient:
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")

    if not supabase_url:
        raise RuntimeError("SUPABASE_URL is not configured.")

    jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
    return PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")

    if not supabase_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication is not configured on the server.",
        )

    issuer = f"{supabase_url}/auth/v1"

    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")

        if algorithm == "HS256":
            # Legacy Supabase projects may use a shared JWT secret.
            secret = os.getenv("SUPABASE_JWT_SECRET")
            if not secret:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="This Supabase signing method needs server-side configuration.",
                )
            signing_key = secret

        elif algorithm in ASYMMETRIC_ALGORITHMS:
            signing_key = get_jwks_client().get_signing_key_from_jwt(token).key

        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unsupported token signing algorithm.",
            )

        claims = jwt.decode(
            token,
            signing_key,
            algorithms=[algorithm],
            audience="authenticated",
            issuer=issuer,
            options={"require": ["exp", "sub", "iss", "aud"]},
        )

        user_id = claims.get("sub")
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid user identity.",
            )

        return AuthenticatedUser(
            id=user_id,
            email=claims.get("email"),
            claims=claims,
        )

    except HTTPException:
        raise
    except (PyJWTError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except Exception as exc:
        # Includes JWKS retrieval failures. Never accept an unverified token.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to verify authentication at this time.",
        ) from exc
