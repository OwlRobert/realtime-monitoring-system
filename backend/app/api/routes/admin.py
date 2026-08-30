import logging
import math
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import literal, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.crud import audit_log as audit_crud
from app.crud import data_record as record_crud
from app.crud import user as user_crud
from app.db.session import engine, get_session
from app.models.audit_log import AuditAction, ResourceType
from app.models.data_record import DataSource
from app.models.user import User, UserRole
from app.schemas.admin import (
    AuditLogPage,
    AuditLogRead,
    ConnectionPoolStatus,
    DatabaseStatus,
    UserList,
    UserRoleUpdate,
    UserStatusUpdate,
)
from app.schemas.user import UserRead
from app.utils.request_context import client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

MAX_PAGE_SIZE = 100
USER_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
)


async def _get_user_or_404(session: AsyncSession, user_id: int) -> User:
    user = await user_crud.get_by_id(session, user_id)
    if user is None:
        raise USER_NOT_FOUND
    return user


def _refuse_self_change(target: User, admin: User, what: str) -> None:
    """Stop an administrator removing their own access.

    Not a full IAM policy — just protection against locking yourself out.
    """
    if target.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You cannot {what} your own account",
        )


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


@router.get("/users", response_model=UserList, summary="List all users")
async def list_users(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UserList:
    users = await user_crud.list_all(session)
    return UserList(
        items=[UserRead.model_validate(user) for user in users], total=len(users)
    )


@router.patch(
    "/users/{user_id}/role",
    response_model=UserRead,
    summary="Change a user's role",
    responses={
        404: {"description": "No such user."},
        409: {"description": "An administrator may not demote themselves."},
    },
)
async def change_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    """Set a role. The change and its audit row share one transaction."""
    target = await _get_user_or_404(session, user_id)
    if payload.role is not UserRole.ADMIN:
        _refuse_self_change(target, admin, "change the role of")

    previous = target.role
    target.role = payload.role
    audit_crud.record_event(
        session,
        user_id=admin.id,
        action=AuditAction.USER_ROLE_CHANGE,
        resource_type=ResourceType.USER,
        resource_id=target.id,
        detail=f"{target.username}: {previous.value} -> {payload.role.value}",
        ip_address=client_ip(request),
    )
    await session.commit()
    await session.refresh(target)

    logger.info(
        "Admin id=%s changed role of user id=%s to %s",
        admin.id,
        target.id,
        payload.role.value,
    )
    return UserRead.model_validate(target)


@router.patch(
    "/users/{user_id}/status",
    response_model=UserRead,
    summary="Activate or deactivate a user",
    responses={
        404: {"description": "No such user."},
        409: {"description": "An administrator may not deactivate themselves."},
    },
)
async def change_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    target = await _get_user_or_404(session, user_id)
    if not payload.is_active:
        _refuse_self_change(target, admin, "deactivate")

    previous = target.is_active
    target.is_active = payload.is_active
    audit_crud.record_event(
        session,
        user_id=admin.id,
        action=AuditAction.USER_STATUS_CHANGE,
        resource_type=ResourceType.USER,
        resource_id=target.id,
        detail=f"{target.username}: active {previous} -> {payload.is_active}",
        ip_address=client_ip(request),
    )
    await session.commit()
    await session.refresh(target)

    logger.info(
        "Admin id=%s set user id=%s active=%s", admin.id, target.id, payload.is_active
    )
    return UserRead.model_validate(target)


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------


@router.get("/audit-logs", response_model=AuditLogPage, summary="Browse the audit log")
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=MAX_PAGE_SIZE),
    action: str | None = Query(None, max_length=50),
    user_id: int | None = Query(None),
    resource_type: str | None = Query(None, max_length=50),
    start_time: datetime | None = Query(None, description="Earliest event, inclusive."),
    end_time: datetime | None = Query(None, description="Latest event, inclusive."),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AuditLogPage:
    """Newest first. Entries never contain credentials."""
    events, total = await audit_crud.list_events(
        session,
        page=page,
        page_size=page_size,
        action=action,
        user_id=user_id,
        resource_type=resource_type,
        start=start_time,
        end=end_time,
    )
    return AuditLogPage(
        items=[AuditLogRead.model_validate(event) for event in events],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size),
    )


# --------------------------------------------------------------------------
# Database status
# --------------------------------------------------------------------------


def _pool_status() -> ConnectionPoolStatus:
    """Read pool counters where the configured pool exposes them.

    Async engines wrap a synchronous pool; NullPool (used by tests) has no
    counters, so the fields stay null rather than being faked.
    """
    pool = engine.sync_engine.pool
    values: dict[str, int | None] = {}
    for field, method in (
        ("size", "size"),
        ("checked_in", "checkedin"),
        ("checked_out", "checkedout"),
        ("overflow", "overflow"),
    ):
        getter = getattr(pool, method, None)
        try:
            values[field] = int(getter()) if callable(getter) else None
        except Exception:  # noqa: BLE001 - pool metrics are best-effort
            values[field] = None
    return ConnectionPoolStatus(**values)


@router.get(
    "/database-status",
    response_model=DatabaseStatus,
    summary="Database health and content counts",
)
async def database_status(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DatabaseStatus:
    """Operational snapshot. Host, user and password are never included."""
    try:
        await session.execute(select(literal(1)))
        healthy = True
    except SQLAlchemyError:
        logger.exception("Database status check failed")
        healthy = False

    url = engine.url
    return DatabaseStatus(
        healthy=healthy,
        dialect=url.get_backend_name(),
        driver=url.get_driver_name(),
        database=url.database or "",
        pool=_pool_status(),
        users=await user_crud.count(session),
        data_records=await record_crud.count(session),
        realtime_records=await record_crud.count(session, source=DataSource.REALTIME),
        audit_logs=await audit_crud.count(session),
        latest_realtime_timestamp=await record_crud.latest_timestamp(
            session, source=DataSource.REALTIME
        ),
    )
