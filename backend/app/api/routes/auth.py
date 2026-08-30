import logging
import secrets
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import hash_password, verify_password
from app.core.tokens import create_access_token
from app.crud import audit_log as audit_crud
from app.crud import user as user_crud
from app.db.session import get_session
from app.models.audit_log import AuditAction, ResourceType
from app.models.user import User, UserRole
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import UserCreate, UserRead
from app.utils.request_context import client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_CREDENTIALS = "Incorrect username or password"


@lru_cache
def _dummy_hash() -> str:
    """Hash of a throwaway password, verified against when no user is found.

    Keeps the cost of a failed login roughly equal whether or not the
    username exists, so response time does not disclose account existence.
    """
    return hash_password(secrets.token_urlsafe(32))


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    responses={409: {"description": "Username or email already registered."}},
)
async def register(
    payload: UserCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    """Create an account. Self-registration always yields the USER role."""
    if await user_crud.get_by_username(session, payload.username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username already registered"
        )
    if await user_crud.get_by_email(session, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    try:
        user = await user_crud.create(
            session,
            username=payload.username,
            email=payload.email,
            hashed_password=hash_password(payload.password),
            role=UserRole.USER,
        )
    except IntegrityError:
        # Two concurrent registrations for the same identity; the unique
        # constraints are the final authority.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already registered",
        ) from None

    # The id only exists after the insert, so this audit row is committed
    # separately; a failure here must not undo a successful registration.
    await audit_crud.record_and_commit(
        session,
        user_id=user.id,
        action=AuditAction.USER_REGISTER,
        resource_type=ResourceType.USER,
        resource_id=user.id,
        detail=f"username={user.username} role={user.role.value}",
        ip_address=client_ip(request),
    )

    logger.info("Registered user id=%s username=%s", user.id, user.username)
    return UserRead.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange credentials for an access token",
    responses={401: {"description": "Invalid credentials or inactive account."}},
)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    """Verify credentials and issue a JWT access token."""
    user = await user_crud.get_by_username(session, payload.username)
    stored_hash = user.hashed_password if user else _dummy_hash()
    password_ok = verify_password(payload.password, stored_hash)

    if user is None or not password_ok or not user.is_active:
        logger.info("Failed login attempt for username=%s", payload.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Login mutates nothing, so its audit row is its own transaction.
    # Neither the password nor the issued token is recorded.
    await audit_crud.record_and_commit(
        session,
        user_id=user.id,
        action=AuditAction.USER_LOGIN,
        resource_type=ResourceType.USER,
        resource_id=user.id,
        detail=f"username={user.username}",
        ip_address=client_ip(request),
    )

    logger.info("User id=%s logged in", user.id)
    return TokenResponse(access_token=create_access_token(user.id))


@router.get(
    "/me",
    response_model=UserRead,
    summary="Current authenticated user",
    responses={401: {"description": "Missing, invalid or expired credentials."}},
)
async def read_current_user(
    current_user: User = Depends(get_current_user),
) -> UserRead:
    """Return the account belonging to the supplied bearer token."""
    return UserRead.model_validate(current_user)
