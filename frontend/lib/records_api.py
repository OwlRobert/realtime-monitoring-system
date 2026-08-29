"""Records API helpers.

Query building and permission checks are pure functions so they can be
tested without Streamlit. All HTTP goes through `auth.request`, which
attaches the token and clears the session on 401.
"""

from datetime import datetime
from typing import Any

from lib import auth
from lib.api_client import ApiResult

DEFAULT_PAGE_SIZE = 20
PAGE_SIZE_OPTIONS = [10, 20, 50, 100]
SORT_FIELDS = ["timestamp", "value", "title", "category", "created_at", "id"]
SORT_ORDERS = ["desc", "asc"]
SOURCES = ["MANUAL", "IMPORT", "REALTIME"]
# Sources a client may legitimately submit; REALTIME belongs to the generator.
CREATABLE_SOURCES = ["MANUAL", "IMPORT"]

TABLE_COLUMNS = [
    "id",
    "title",
    "value",
    "category",
    "timestamp",
    "source",
    "is_anomaly",
    "owner_id",
]


def build_query(
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    category: str | None = None,
    source: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    sort_by: str = "timestamp",
    order: str = "desc",
) -> dict[str, Any]:
    """Parameters for GET /records.

    Note the names: /records uses `start`/`end`, while /analytics uses
    `start_time`/`end_time`.
    """
    query: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
        "sort_by": sort_by,
        "order": order,
    }
    if category:
        query["category"] = category
    if source:
        query["source"] = source
    if start is not None:
        query["start"] = start.isoformat()
    if end is not None:
        query["end"] = end.isoformat()
    return query


def total_pages(total: int, page_size: int) -> int:
    """Pages needed for `total` rows; zero when there is nothing to show."""
    if total <= 0 or page_size <= 0:
        return 0
    return -(-total // page_size)  # ceiling division


def clamp_page(page: int, pages: int) -> int:
    """Keep the requested page inside the available range."""
    if pages <= 0:
        return 1
    return max(1, min(page, pages))


def can_modify(record: dict[str, Any], user: dict[str, Any] | None) -> bool:
    """Mirror of the backend rule: admins anything, others their own records.

    This only decides whether controls are offered; FastAPI still enforces it.
    """
    if not user:
        return False
    if user.get("role") == "ADMIN":
        return True
    if user.get("role") == "VIEWER":
        return False
    return record.get("owner_id") is not None and record.get("owner_id") == user.get("id")


def modifiable(records: list[dict], user: dict | None) -> list[dict]:
    return [record for record in records if can_modify(record, user)]


def describe(record: dict[str, Any]) -> str:
    """Short label for select boxes."""
    flag = " ⚠️" if record.get("is_anomaly") else ""
    return f"#{record['id']} · {record['title']} · {record['value']} ({record['category']}){flag}"


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------


def list_records(**query: Any) -> ApiResult:
    return auth.request("GET", "/records", params=build_query(**query))


def create_record(payload: dict[str, Any]) -> ApiResult:
    return auth.request("POST", "/records", json=payload)


def update_record(record_id: int, changes: dict[str, Any]) -> ApiResult:
    return auth.request("PATCH", f"/records/{record_id}", json=changes)


def delete_record(record_id: int) -> ApiResult:
    return auth.request("DELETE", f"/records/{record_id}")


def changed_fields(record: dict[str, Any], submitted: dict[str, Any]) -> dict[str, Any]:
    """Only the fields whose value actually differs, for a PATCH body."""
    return {
        field: value
        for field, value in submitted.items()
        if record.get(field) != value
    }
