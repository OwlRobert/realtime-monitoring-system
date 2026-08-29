"""Shared API dependencies."""

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import decode_access_token
from app.crud import user as user_crud
from app.db.session import get_session
from app.models.user import User

# tokenUrl is documentation only; it tells Swagger where tokens come from.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Resolve the bearer token to an active user, or raise 401."""
    try:
        claims = decode_access_token(token)
        user_id = int(claims["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise CREDENTIALS_EXCEPTION from None

    user = await user_crud.get_by_id(session, user_id)
    if user is None or not user.is_active:
        raise CREDENTIALS_EXCEPTION

    return user
