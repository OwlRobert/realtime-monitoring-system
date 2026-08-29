"""JWT access-token utilities.

PyJWT is used directly. Tokens are signed symmetrically (HS256 by default)
with a secret supplied through configuration.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.config import get_settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(
    subject: str | int, expires_delta: timedelta | None = None
) -> str:
    """Sign an access token for `subject`, normally the user id.

    `expires_delta` overrides the configured lifetime; it is mainly useful
    for tests.
    """
    settings = get_settings()
    issued_at = _utcnow()
    expires_at = issued_at + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.access_token_expire_minutes)
    )

    claims: dict[str, Any] = {
        "sub": str(subject),
        "iat": issued_at,
        "exp": expires_at,
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify an access token and return its claims.

    Raises a `jwt.PyJWTError` subclass when the token is expired, tampered
    with, signed with another key, or missing a required claim.
    """
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "exp"]},
    )
