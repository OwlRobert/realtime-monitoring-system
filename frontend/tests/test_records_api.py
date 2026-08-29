from datetime import datetime
from unittest.mock import patch

import pytest

from lib import records_api
from lib.api_client import ApiResult

ADMIN = {"id": 1, "username": "admin", "role": "ADMIN"}
USER = {"id": 2, "username": "user", "role": "USER"}
OTHER = {"id": 3, "username": "other", "role": "USER"}
VIEWER = {"id": 4, "username": "viewer", "role": "VIEWER"}

OWNED = {"id": 10, "title": "cpu", "value": 1.0, "category": "cpu", "owner_id": 2}
FOREIGN = {**OWNED, "id": 11, "owner_id": 3}
GENERATED = {**OWNED, "id": 12, "owner_id": None}


# --------------------------------------------------------------------------
# Query building
# --------------------------------------------------------------------------


def test_defaults_include_only_paging_and_sorting():
    assert records_api.build_query() == {
        "page": 1,
        "page_size": 20,
        "sort_by": "timestamp",
        "order": "desc",
    }


def test_optional_filters_are_omitted_when_empty():
    query = records_api.build_query(category="", source=None, start=None, end=None)

    assert "category" not in query
    assert "source" not in query
    assert "start" not in query
    assert "end" not in query


def test_filters_are_included_when_supplied():
    query = records_api.build_query(
        page=3,
        page_size=50,
        category="cpu",
        source="REALTIME",
        start=datetime(2026, 8, 1, 0, 0, 0),
        end=datetime(2026, 8, 2, 23, 59, 59),
        sort_by="value",
        order="asc",
    )

    assert query == {
        "page": 3,
        "page_size": 50,
        "sort_by": "value",
        "order": "asc",
        "category": "cpu",
        "source": "REALTIME",
        "start": "2026-08-01T00:00:00",
        "end": "2026-08-02T23:59:59",
    }


def test_records_endpoint_uses_start_and_end_not_start_time():
    """The analytics endpoints use different parameter names."""
    query = records_api.build_query(start=datetime(2026, 1, 1))

    assert "start" in query
    assert "start_time" not in query


# --------------------------------------------------------------------------
# Pagination maths
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "size", "expected"),
    [(0, 20, 0), (1, 20, 1), (20, 20, 1), (21, 20, 2), (100, 20, 5), (101, 20, 6)],
)
def test_total_pages(total, size, expected):
    assert records_api.total_pages(total, size) == expected


@pytest.mark.parametrize(
    ("page", "pages", "expected"),
    [(1, 0, 1), (5, 3, 3), (0, 3, 1), (2, 3, 2), (-1, 5, 1)],
)
def test_clamp_page(page, pages, expected):
    assert records_api.clamp_page(page, pages) == expected


# --------------------------------------------------------------------------
# Ownership / role rules
# --------------------------------------------------------------------------


def test_admin_may_modify_anything():
    assert records_api.can_modify(OWNED, ADMIN) is True
    assert records_api.can_modify(FOREIGN, ADMIN) is True
    assert records_api.can_modify(GENERATED, ADMIN) is True


def test_user_may_modify_only_their_own():
    assert records_api.can_modify(OWNED, USER) is True
    assert records_api.can_modify(FOREIGN, USER) is False
    assert records_api.can_modify(GENERATED, USER) is False


def test_viewer_may_modify_nothing():
    assert records_api.can_modify(OWNED, VIEWER) is False
    assert records_api.can_modify(FOREIGN, VIEWER) is False


def test_no_user_means_no_modification():
    assert records_api.can_modify(OWNED, None) is False


def test_modifiable_filters_a_page_of_records():
    records = [OWNED, FOREIGN, GENERATED]

    assert [r["id"] for r in records_api.modifiable(records, USER)] == [10]
    assert [r["id"] for r in records_api.modifiable(records, ADMIN)] == [10, 11, 12]
    assert records_api.modifiable(records, VIEWER) == []


def test_realtime_records_are_untouchable_by_ordinary_users():
    """Generated rows have no owner, so only an admin can reach them."""
    assert records_api.can_modify(GENERATED, USER) is False
    assert records_api.can_modify(GENERATED, ADMIN) is True


# --------------------------------------------------------------------------
# PATCH bodies
# --------------------------------------------------------------------------


def test_only_changed_fields_are_sent():
    record = {"title": "cpu", "value": 1.0, "category": "cpu"}
    submitted = {"title": "cpu", "value": 2.0, "category": "cpu"}

    assert records_api.changed_fields(record, submitted) == {"value": 2.0}


def test_no_changes_produces_an_empty_body():
    record = {"title": "cpu", "value": 1.0}

    assert records_api.changed_fields(record, {"title": "cpu", "value": 1.0}) == {}


def test_creatable_sources_exclude_realtime():
    assert "REALTIME" not in records_api.CREATABLE_SOURCES
    assert records_api.CREATABLE_SOURCES == ["MANUAL", "IMPORT"]


# --------------------------------------------------------------------------
# Requests go through the authenticated helper
# --------------------------------------------------------------------------


def test_list_records_calls_the_backend_with_query_parameters():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(200, {"items": [], "total": 0})
        records_api.list_records(page=2, category="cpu")

    method, path = request.call_args.args
    assert (method, path) == ("GET", "/records")
    assert request.call_args.kwargs["params"]["page"] == 2
    assert request.call_args.kwargs["params"]["category"] == "cpu"


def test_write_calls_use_the_expected_verbs_and_paths():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(200, {})
        records_api.create_record({"title": "x"})
        records_api.update_record(7, {"value": 1.0})
        records_api.delete_record(7)

    calls = [call.args for call in request.call_args_list]
    assert calls == [("POST", "/records"), ("PATCH", "/records/7"), ("DELETE", "/records/7")]


def test_empty_result_is_passed_through_untouched():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(
            200, {"items": [], "total": 0, "page": 1, "page_size": 20, "pages": 0}
        )
        result = records_api.list_records()

    assert result.data["items"] == []
    assert result.data["pages"] == 0


def test_backend_errors_are_returned_not_raised():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(403, {"detail": "Not permitted to modify this record"})
        result = records_api.update_record(11, {"value": 2.0})

    assert result.ok is False
    assert result.error_message == "Not permitted to modify this record"
