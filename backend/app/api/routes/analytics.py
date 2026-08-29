from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_read
from app.crud import analytics as analytics_crud
from app.db.session import get_session
from app.models.data_record import DataSource
from app.models.user import User
from app.schemas.analytics import (
    AnalyticsSummary,
    CategoryAggregation,
    TrendInterval,
    TrendSeries,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])

StartTime = Query(None, description="Earliest timestamp, inclusive.")
EndTime = Query(None, description="Latest timestamp, inclusive.")


@router.get("/summary", response_model=AnalyticsSummary, summary="Overall statistics")
async def read_summary(
    start_time: datetime | None = StartTime,
    end_time: datetime | None = EndTime,
    category: str | None = Query(None, max_length=100),
    source: DataSource | None = Query(None),
    current_user: User = Depends(require_read),
    session: AsyncSession = Depends(get_session),
) -> AnalyticsSummary:
    """Total, average, maximum and minimum over the matching records."""
    return await analytics_crud.summary(
        session, category=category, source=source, start=start_time, end=end_time
    )


@router.get(
    "/categories",
    response_model=CategoryAggregation,
    summary="Statistics grouped by category",
)
async def read_category_aggregation(
    start_time: datetime | None = StartTime,
    end_time: datetime | None = EndTime,
    source: DataSource | None = Query(None),
    current_user: User = Depends(require_read),
    session: AsyncSession = Depends(get_session),
) -> CategoryAggregation:
    items = await analytics_crud.by_category(
        session, source=source, start=start_time, end=end_time
    )
    return CategoryAggregation(items=items)


@router.get("/trend", response_model=TrendSeries, summary="Values over time")
async def read_trend(
    start_time: datetime | None = StartTime,
    end_time: datetime | None = EndTime,
    interval: TrendInterval = Query(TrendInterval.MINUTE),
    category: str | None = Query(None, max_length=100),
    source: DataSource | None = Query(None),
    current_user: User = Depends(require_read),
    session: AsyncSession = Depends(get_session),
) -> TrendSeries:
    """Time-bucketed series, ordered oldest to newest."""
    points = await analytics_crud.trend(
        session,
        interval=interval,
        category=category,
        source=source,
        start=start_time,
        end=end_time,
    )
    return TrendSeries(interval=interval, points=points)
