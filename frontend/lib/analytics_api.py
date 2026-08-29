"""Analytics API helpers.

The analytics endpoints take `start_time`/`end_time`, unlike /records which
takes `start`/`end`; `build_query` is the single place that knows this.
"""

from datetime import datetime
from typing import Any

from lib import auth
from lib.api_client import ApiResult

INTERVALS = ["minute", "hour", "day"]


def build_query(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    category: str | None = None,
    source: str | None = None,
    interval: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if start is not None:
        query["start_time"] = start.isoformat()
    if end is not None:
        query["end_time"] = end.isoformat()
    if category:
        query["category"] = category
    if source:
        query["source"] = source
    if interval:
        query["interval"] = interval
    return query


def summary(**filters: Any) -> ApiResult:
    return auth.request("GET", "/analytics/summary", params=build_query(**filters))


def categories(**filters: Any) -> ApiResult:
    """The endpoint aggregates across categories, so it takes no category."""
    filters.pop("category", None)
    return auth.request("GET", "/analytics/categories", params=build_query(**filters))


def trend(**filters: Any) -> ApiResult:
    return auth.request("GET", "/analytics/trend", params=build_query(**filters))


def has_data(summary_payload: dict[str, Any] | None) -> bool:
    return bool(summary_payload) and (summary_payload.get("count") or 0) > 0


def format_metric(value: Any, digits: int = 2) -> str:
    """Render a number for st.metric, or an em dash when there is no data."""
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}"
    return str(value)
