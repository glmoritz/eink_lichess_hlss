"""
Security helpers for LLSS ↔ HLSS authentication.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from hlss.config import get_settings

settings = get_settings()
_bearer_scheme = HTTPBearer(auto_error=False)

_ALLOWED_TOKEN_TYPES = {"llss_admin", "instance_access"}


def _get_shared_key() -> str:
    if not settings.hlss_shared_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="HLSS_SHARED_KEY is not configured",
        )
    return settings.hlss_shared_key


def create_llss_token(token_type: str, subject: str | None = None) -> str:
    """
    Create a JWT signed with the shared HLSS/LLSS key.

    Args:
        token_type: Token type (e.g., "llss_admin" or "instance_access").
        subject: Optional subject (e.g., instance_id).

    Returns:
        Encoded JWT string.
    """
    if token_type not in _ALLOWED_TOKEN_TYPES:
        raise ValueError(f"Invalid token type: {token_type}")

    shared_key = _get_shared_key()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=settings.llss_token_ttl_seconds)

    payload: dict[str, Any] = {
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    if subject:
        payload["sub"] = subject

    return jwt.encode(payload, shared_key, algorithm="HS256")


def require_llss_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """
    Validate LLSS-signed JWT using shared key.

    Returns the decoded payload on success.
    """
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
        )

    shared_key = _get_shared_key()

    try:
        payload = jwt.decode(credentials.credentials, shared_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from exc

    token_type = payload.get("type") or payload.get("token_type")
    if token_type not in _ALLOWED_TOKEN_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid token type",
        )

    return payload
