"""Analytics queries.

Everything is aggregated by the database through SQLAlchemy expressions;
no rows are pulled into Python to be summed.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.data_record import apply_filters
from app.models.data_record import DataRecord, DataSource
from app.schemas.analytics import (
    AnalyticsSummary,
    CategoryAggregate,
    TrendInterval,
    TrendPoint,
)

# Truncation formats per dialect. MariaDB uses %i for minutes, SQLite %M.
_MARIADB_FORMATS = {
    TrendInterval.MINUTE: "%Y-%m-%d %H:%i:00",
    TrendInterval.HOUR: "%Y-%m-%d %H:00:00",
    TrendInterval.DAY: "%Y-%m-%d 00:00:00",
}
_SQLITE_FORMATS = {
    TrendInterval.MINUTE: "%Y-%m-%d %H:%M:00",
    TrendInterval.HOUR: "%Y-%m-%d %H:00:00",
    TrendInterval.DAY: "%Y-%m-%d 00:00:00",
}


def _bucket_expression(session: AsyncSession, interval: TrendInterval):
    """Truncate `timestamp` down to the start of its bucket.

    Two dialects are supported: MariaDB in production, SQLite in tests.
    """
    if session.get_bind().dialect.name == "sqlite":
        return func.strftime(_SQLITE_FORMATS[interval], DataRecord.timestamp)
    return func.date_format(DataRecord.timestamp, _MARIADB_FORMATS[interval])


def _as_float(value) -> float | None:
    """Aggregates come back as Decimal on MariaDB."""
    return None if value is None else float(value)


async def summary(
    session: AsyncSession,
    *,
    category: str | None = None,
    source: DataSource | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> AnalyticsSummary:
    """Count, sum, average, minimum and maximum over the matching records."""
    statement = apply_filters(
        select(
            func.count(DataRecord.id),
            func.sum(DataRecord.value),
            func.avg(DataRecord.value),
            func.min(DataRecord.value),
            func.max(DataRecord.value),
        ),
        category=category,
        source=source,
        start=start,
        end=end,
    )
    count, total, average, minimum, maximum = (await session.execute(statement)).one()

    return AnalyticsSummary(
        count=count or 0,
        total=_as_float(total) or 0.0,
        average=_as_float(average),
        minimum=_as_float(minimum),
        maximum=_as_float(maximum),
    )


async def by_category(
    session: AsyncSession,
    *,
    source: DataSource | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[CategoryAggregate]:
    """Same aggregates, grouped by category and ordered by name."""
    statement = apply_filters(
        select(
            DataRecord.category,
            func.count(DataRecord.id),
            func.sum(DataRecord.value),
            func.avg(DataRecord.value),
            func.min(DataRecord.value),
            func.max(DataRecord.value),
        ),
        category=None,
        source=source,
        start=start,
        end=end,
    ).group_by(DataRecord.category).order_by(DataRecord.category)

    return [
        CategoryAggregate(
            category=category,
            count=count,
            total=_as_float(total) or 0.0,
            average=_as_float(average) or 0.0,
            minimum=_as_float(minimum) or 0.0,
            maximum=_as_float(maximum) or 0.0,
        )
        for category, count, total, average, minimum, maximum in (
            await session.execute(statement)
        ).all()
    ]


async def trend(
    session: AsyncSession,
    *,
    interval: TrendInterval = TrendInterval.MINUTE,
    category: str | None = None,
    source: DataSource | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[TrendPoint]:
    """Aggregate values into fixed time buckets, oldest first.

    Buckets with no data are simply absent; the series is not gap-filled.
    """
    bucket = _bucket_expression(session, interval).label("bucket")
    statement = apply_filters(
        select(
            bucket,
            func.count(DataRecord.id),
            func.avg(DataRecord.value),
            func.min(DataRecord.value),
            func.max(DataRecord.value),
        ),
        category=category,
        source=source,
        start=start,
        end=end,
    ).group_by(bucket).order_by(bucket)

    return [
        TrendPoint(
            bucket=bucket_start,
            count=count,
            average=_as_float(average) or 0.0,
            minimum=_as_float(minimum) or 0.0,
            maximum=_as_float(maximum) or 0.0,
        )
        for bucket_start, count, average, minimum, maximum in (
            await session.execute(statement)
        ).all()
    ]
