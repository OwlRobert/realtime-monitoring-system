"""Admin API helpers.

Same shape as records_api/analytics_api: pure query building plus thin
wrappers over `auth.request`, which attaches the token and handles 401.
"""

from datetime import datetime
from typing import Any

from lib import auth, records_api
from lib.api_client import ApiResult

ROLES = ["ADMIN", "USER", "VIEWER"]
AUDIT_ACTIONS = [
    "USER_REGISTER",
    "USER_LOGIN",
    "USER_ROLE_CHANGE",
    "USER_STATUS_CHANGE",
    "RECORD_CREATE",
    "RECORD_UPDATE",
    "RECORD_DELETE",
    "RECORD_IMPORT",
]
RESOURCE_TYPES = ["USER", "RECORD"]
AUDIT_PAGE_SIZE = 25

USER_COLUMNS = ["id", "username", "email", "role", "is_active", "created_at"]
AUDIT_COLUMNS = [
    "created_at",
    "action",
    "user_id",
    "resource_type",
    "resource_id",
    "detail",
    "ip_address",
]


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


def list_users() -> ApiResult:
    return auth.request("GET", "/admin/users")


def set_role(user_id: int, role: str) -> ApiResult:
    return auth.request("PATCH", f"/admin/users/{user_id}/role", json={"role": role})


def set_active(user_id: int, is_active: bool) -> ApiResult:
    return auth.request(
        "PATCH", f"/admin/users/{user_id}/status", json={"is_active": is_active}
    )


def may_change_own(user_id: int, admin_id: int) -> bool:
    """The backend refuses self-demotion/deactivation; mirror it in the UI."""
    return user_id != admin_id


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------


def audit_query(
    *,
    page: int = 1,
    page_size: int = AUDIT_PAGE_SIZE,
    action: str | None = None,
    user_id: int | None = None,
    resource_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    """Audit filters use start_time/end_time, like the analytics endpoints."""
    query: dict[str, Any] = {"page": page, "page_size": page_size}
    if action:
        query["action"] = action
    if user_id is not None:
        query["user_id"] = user_id
    if resource_type:
        query["resource_type"] = resource_type
    if start is not None:
        query["start_time"] = start.isoformat()
    if end is not None:
        query["end_time"] = end.isoformat()
    return query


def audit_logs(**filters: Any) -> ApiResult:
    return auth.request("GET", "/admin/audit-logs", params=audit_query(**filters))


# --------------------------------------------------------------------------
# Database status
# --------------------------------------------------------------------------


def database_status() -> ApiResult:
    return auth.request("GET", "/admin/database-status")


def health_label(status: dict[str, Any] | None) -> str:
    if not status:
        return "Unknown"
    return "Healthy" if status.get("healthy") else "Unavailable"


# --------------------------------------------------------------------------
# Realtime history — the existing records endpoint, filtered by source
# --------------------------------------------------------------------------


def realtime_history(page: int = 1, page_size: int = 25, **filters: Any) -> ApiResult:
    """Persisted realtime rows. No new backend endpoint is needed for this."""
    return records_api.list_records(
        page=page,
        page_size=page_size,
        source="REALTIME",
        sort_by="timestamp",
        order="desc",
        **filters,
    )
