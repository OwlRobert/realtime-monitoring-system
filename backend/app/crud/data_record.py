from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_record import DataRecord, DataSource
from app.schemas.data_record import (
    DataRecordCreate,
    DataRecordImportRow,
    DataRecordUpdate,
    SortOrder,
    SortableField,
)

# Only these columns may be sorted on; the query parameter is validated
# against this mapping, so no client string ever reaches the SQL layer.
SORTABLE_COLUMNS = {
    SortableField.TIMESTAMP: DataRecord.timestamp,
    SortableField.VALUE: DataRecord.value,
    SortableField.TITLE: DataRecord.title,
    SortableField.CATEGORY: DataRecord.category,
    SortableField.CREATED_AT: DataRecord.created_at,
    SortableField.ID: DataRecord.id,
}


async def get_by_id(session: AsyncSession, record_id: int) -> DataRecord | None:
    return await session.get(DataRecord, record_id)


async def create(
    session: AsyncSession, payload: DataRecordCreate, *, owner_id: int | None
) -> DataRecord:
    """Persist a new record. Ownership is decided by the caller, not the client."""
    record = DataRecord(
        title=payload.title,
        value=payload.value,
        category=payload.category,
        timestamp=payload.timestamp,
        source=payload.source,
        owner_id=owner_id,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def bulk_create(
    session: AsyncSession,
    rows: list[DataRecordImportRow],
    *,
    owner_id: int,
) -> int:
    """Insert a whole validated batch in one transaction.

    Every row is marked as imported and owned by the caller; nothing from the
    uploaded file influences those fields. One commit, so a failure leaves the
    table exactly as it was.
    """
    session.add_all(
        [
            DataRecord(
                title=row.title,
                value=row.value,
                category=row.category,
                timestamp=row.timestamp,
                source=DataSource.IMPORT,
                owner_id=owner_id,
            )
            for row in rows
        ]
    )
    await session.commit()
    return len(rows)


async def list_for_export(
    session: AsyncSession,
    *,
    category: str | None = None,
    source: DataSource | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int,
) -> list[DataRecord]:
    """All matching records, oldest first, capped by an explicit limit."""
    statement = (
        apply_filters(
            select(DataRecord), category=category, source=source, start=start, end=end
        )
        .order_by(DataRecord.timestamp.asc(), DataRecord.id.asc())
        .limit(limit)
    )
    return list((await session.execute(statement)).scalars().all())


def apply_filters(
    statement,
    *,
    category: str | None,
    source: DataSource | None,
    start: datetime | None,
    end: datetime | None,
):
    if category is not None:
        statement = statement.where(DataRecord.category == category)
    if source is not None:
        statement = statement.where(DataRecord.source == source)
    if start is not None:
        statement = statement.where(DataRecord.timestamp >= start)
    if end is not None:
        statement = statement.where(DataRecord.timestamp <= end)
    return statement


async def list_records(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    category: str | None = None,
    source: DataSource | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    sort_by: SortableField = SortableField.TIMESTAMP,
    order: SortOrder = SortOrder.DESC,
) -> tuple[list[DataRecord], int]:
    """Return one page of records plus the total number of matches."""
    filters = {"category": category, "source": source, "start": start, "end": end}

    total = await session.scalar(
        apply_filters(select(func.count()).select_from(DataRecord), **filters)
    )

    column = SORTABLE_COLUMNS[sort_by]
    ordering = column.asc() if order is SortOrder.ASC else column.desc()

    statement = (
        apply_filters(select(DataRecord), **filters)
        # id breaks ties so paging stays stable when timestamps repeat.
        .order_by(ordering, DataRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    records = (await session.execute(statement)).scalars().all()
    return list(records), total or 0


async def update(
    session: AsyncSession, record: DataRecord, payload: DataRecordUpdate
) -> DataRecord:
    """Apply a partial update. Only fields present in the request are touched."""
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(record, field, value)
    await session.commit()
    await session.refresh(record)
    return record


async def delete(session: AsyncSession, record: DataRecord) -> None:
    await session.delete(record)
    await session.commit()
