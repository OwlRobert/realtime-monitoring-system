"""Audit log persistence and queries.

`record_event` only stages the row: it never commits. The caller commits it
together with the mutation being audited, so an administrative change and its
audit trail land in the same transaction.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

MAX_DETAIL_LENGTH = 500


def record_event(
    session: AsyncSession,
    *,
    user_id: int | None,
    action: str,
    resource_type: str,
    resource_id: int | None = None,
    detail: str | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Stage one audit row on the session. The caller commits it."""
    event = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail[:MAX_DETAIL_LENGTH] if detail else None,
        ip_address=ip_address,
    )
    session.add(event)
    return event


async def record_and_commit(session: AsyncSession, **event: object) -> AuditLog:
    """Stage and commit an audit row on its own.

    Used where there is no surrounding mutation to join, such as login.
    """
    entry = record_event(session, **event)  # type: ignore[arg-type]
    await session.commit()
    return entry


def _apply_filters(
    statement,
    *,
    action: str | None,
    user_id: int | None,
    resource_type: str | None,
    start: datetime | None,
    end: datetime | None,
):
    if action:
        statement = statement.where(AuditLog.action == action)
    if user_id is not None:
        statement = statement.where(AuditLog.user_id == user_id)
    if resource_type:
        statement = statement.where(AuditLog.resource_type == resource_type)
    if start is not None:
        statement = statement.where(AuditLog.created_at >= start)
    if end is not None:
        statement = statement.where(AuditLog.created_at <= end)
    return statement


async def list_events(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    action: str | None = None,
    user_id: int | None = None,
    resource_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[list[AuditLog], int]:
    """One page of audit events, newest first, plus the total match count."""
    filters = {
        "action": action,
        "user_id": user_id,
        "resource_type": resource_type,
        "start": start,
        "end": end,
    }

    total = await session.scalar(
        _apply_filters(select(func.count()).select_from(AuditLog), **filters)
    )

    statement = (
        _apply_filters(select(AuditLog), **filters)
        # id breaks ties so paging stays stable within the same second.
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    events = (await session.execute(statement)).scalars().all()
    return list(events), total or 0


async def count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(AuditLog)) or 0
