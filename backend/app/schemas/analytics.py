from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class TrendInterval(str, Enum):
    """Size of one trend bucket."""

    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"


class AnalyticsSummary(BaseModel):
    """Aggregates over every record matching the filters."""

    count: int = Field(description="Number of matching records.")
    total: float = Field(description="Sum of the matching values.")
    average: float | None = Field(description="Null when nothing matched.")
    minimum: float | None = Field(description="Null when nothing matched.")
    maximum: float | None = Field(description="Null when nothing matched.")


class CategoryAggregate(BaseModel):
    """Aggregates for one category."""

    category: str
    count: int
    total: float
    average: float
    minimum: float
    maximum: float


class CategoryAggregation(BaseModel):
    items: list[CategoryAggregate]


class TrendPoint(BaseModel):
    """One time bucket.

    `bucket` is the inclusive start of the interval, so a minute point at
    12:03:00 covers every record from 12:03:00.000 up to 12:03:59.999.
    """

    bucket: datetime
    count: int
    average: float
    minimum: float
    maximum: float


class TrendSeries(BaseModel):
    interval: TrendInterval
    points: list[TrendPoint] = Field(description="Ordered oldest to newest.")
