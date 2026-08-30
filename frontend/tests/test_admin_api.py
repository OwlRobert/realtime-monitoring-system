from datetime import datetime
from unittest.mock import patch

import pytest

from lib import admin_api
from lib.api_client import ApiResult

USERS = {
    "items": [
        {"id": 1, "username": "admin", "email": "a@example.com", "role": "ADMIN",
         "is_active": True, "created_at": "2026-08-30T10:00:00"},
        {"id": 2, "username": "user", "email": "u@example.com", "role": "USER",
         "is_active": True, "created_at": "2026-08-30T10:01:00"},
    ],
    "total": 2,
}
STATUS = {
    "healthy": True,
    "dialect": "mysql",
    "driver": "asyncmy",
    "database": "monitoring",
    "pool": {"size": 10, "checked_in": 9, "checked_out": 1, "overflow": 0},
    "users": 2,
    "data_records": 100,
    "realtime_records": 90,
    "audit_logs": 12,
    "latest_realtime_timestamp": "2026-08-30T12:00:00",
}


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


def test_list_users_calls_the_admin_endpoint():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(200, USERS)
        result = admin_api.list_users()

    assert request.call_args.args == ("GET", "/admin/users")
    assert result.data["total"] == 2


def test_role_update_request_shape():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(200, USERS["items"][1])
        admin_api.set_role(2, "VIEWER")

    assert request.call_args.args == ("PATCH", "/admin/users/2/role")
    assert request.call_args.kwargs["json"] == {"role": "VIEWER"}


@pytest.mark.parametrize("is_active", [True, False])
def test_status_update_request_shape(is_active):
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(200, USERS["items"][1])
        admin_api.set_active(2, is_active)

    assert request.call_args.args == ("PATCH", "/admin/users/2/status")
    assert request.call_args.kwargs["json"] == {"is_active": is_active}


def test_self_management_is_excluded_in_the_ui():
    """Mirrors the backend rule that an admin cannot demote themselves."""
    assert admin_api.may_change_own(user_id=2, admin_id=1) is True
    assert admin_api.may_change_own(user_id=1, admin_id=1) is False


def test_self_change_conflict_is_surfaced():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(409, {"detail": "You cannot deactivate your own account"})
        result = admin_api.set_active(1, False)

    assert result.ok is False
    assert result.error_message == "You cannot deactivate your own account"


def test_forbidden_response_does_not_look_like_a_session_failure():
    """403 must be reported as authorization, not treated as a lost session."""
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(403, {"detail": "Insufficient permissions"})
        result = admin_api.list_users()

    assert result.status_code == 403
    assert result.unauthorized is False


def test_user_columns_exclude_credentials():
    assert "hashed_password" not in admin_api.USER_COLUMNS
    assert admin_api.USER_COLUMNS == [
        "id", "username", "email", "role", "is_active", "created_at"
    ]


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------


def test_audit_query_defaults_to_the_first_page():
    assert admin_api.audit_query() == {"page": 1, "page_size": admin_api.AUDIT_PAGE_SIZE}


def test_audit_query_includes_supplied_filters():
    query = admin_api.audit_query(
        page=3,
        page_size=10,
        action="USER_ROLE_CHANGE",
        user_id=7,
        resource_type="USER",
        start=datetime(2026, 8, 30, 0, 0, 0),
        end=datetime(2026, 8, 30, 23, 59, 59),
    )

    assert query == {
        "page": 3,
        "page_size": 10,
        "action": "USER_ROLE_CHANGE",
        "user_id": 7,
        "resource_type": "USER",
        "start_time": "2026-08-30T00:00:00",
        "end_time": "2026-08-30T23:59:59",
    }


def test_audit_query_omits_blank_filters():
    query = admin_api.audit_query(action="", resource_type=None, user_id=None)

    assert set(query) == {"page", "page_size"}


def test_audit_logs_calls_the_endpoint_with_the_query():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(200, {"items": [], "total": 0, "pages": 0})
        admin_api.audit_logs(page=2, action="USER_LOGIN")

    assert request.call_args.args == ("GET", "/admin/audit-logs")
    assert request.call_args.kwargs["params"]["action"] == "USER_LOGIN"
    assert request.call_args.kwargs["params"]["page"] == 2


def test_audit_columns_are_safe_to_display():
    for forbidden in ("hashed_password", "password", "token"):
        assert forbidden not in admin_api.AUDIT_COLUMNS


# --------------------------------------------------------------------------
# Database status
# --------------------------------------------------------------------------


def test_database_status_calls_the_endpoint():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(200, STATUS)
        result = admin_api.database_status()

    assert request.call_args.args == ("GET", "/admin/database-status")
    assert result.data["realtime_records"] == 90


def test_health_label():
    assert admin_api.health_label(STATUS) == "Healthy"
    assert admin_api.health_label({**STATUS, "healthy": False}) == "Unavailable"
    assert admin_api.health_label(None) == "Unknown"


# --------------------------------------------------------------------------
# Realtime history
# --------------------------------------------------------------------------


def test_realtime_history_reuses_the_records_endpoint():
    """No separate realtime-history endpoint exists, by design."""
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(200, {"items": [], "total": 0, "pages": 0})
        admin_api.realtime_history(page=2)

    assert request.call_args.args == ("GET", "/records")
    params = request.call_args.kwargs["params"]
    assert params["source"] == "REALTIME"
    assert params["page"] == 2
    assert params["sort_by"] == "timestamp"
    assert params["order"] == "desc"


def test_realtime_history_passes_extra_filters_through():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(200, {"items": []})
        admin_api.realtime_history(category="cpu")

    assert request.call_args.kwargs["params"]["category"] == "cpu"
    assert request.call_args.kwargs["params"]["source"] == "REALTIME"
