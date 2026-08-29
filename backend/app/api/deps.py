"""Shared API dependencies."""

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import decode_access_token
from app.crud import user as user_crud
from app.db.session import get_session
from app.models.user import User, UserRole

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


class RequireRoles:
    """Dependency allowing only the listed roles through.

    Authentication runs first, so an unauthenticated caller still gets 401;
    an authenticated caller with the wrong role gets 403.
    """

    def __init__(self, *allowed_roles: UserRole) -> None:
        self.allowed_roles = frozenset(allowed_roles)

    async def __call__(self, current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    def __repr__(self) -> str:
        roles = ", ".join(sorted(role.value for role in self.allowed_roles))
        return f"RequireRoles({roles})"


# Read access: any authenticated account, Viewer included.
require_read = RequireRoles(UserRole.ADMIN, UserRole.USER, UserRole.VIEWER)

# Write access: Viewer is read-only. Ownership rules come with DataRecord CRUD.
require_write = RequireRoles(UserRole.ADMIN, UserRole.USER)

# Administration.
require_admin = RequireRoles(UserRole.ADMIN)
