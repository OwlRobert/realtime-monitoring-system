from datetime import datetime
from unittest.mock import patch

import pytest

from lib import analytics_api
from lib.api_client import ApiResult

SUMMARY = {
    "count": 5,
    "total": 180.0,
    "average": 36.0,
    "minimum": 10.0,
    "maximum": 80.0,
}
EMPTY_SUMMARY = {
    "count": 0,
    "total": 0.0,
    "average": None,
    "minimum": None,
    "maximum": None,
}


# --------------------------------------------------------------------------
# Query building
# --------------------------------------------------------------------------


def test_no_filters_produces_no_parameters():
    assert analytics_api.build_query() == {}


def test_analytics_uses_start_time_and_end_time():
    """/analytics names its range parameters differently from /records."""
    query = analytics_api.build_query(
        start=datetime(2026, 8, 1, 0, 0, 0), end=datetime(2026, 8, 1, 23, 59, 59)
    )

    assert query == {
        "start_time": "2026-08-01T00:00:00",
        "end_time": "2026-08-01T23:59:59",
    }
    assert "start" not in query
    assert "end" not in query


def test_category_source_and_interval_are_forwarded():
    query = analytics_api.build_query(category="cpu", source="REALTIME", interval="hour")

    assert query == {"category": "cpu", "source": "REALTIME", "interval": "hour"}


def test_blank_values_are_dropped():
    assert analytics_api.build_query(category="", source=None, interval=None) == {}


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


def test_summary_calls_the_summary_endpoint():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(200, SUMMARY)
        analytics_api.summary(category="cpu")

    assert request.call_args.args == ("GET", "/analytics/summary")
    assert request.call_args.kwargs["params"] == {"category": "cpu"}


def test_category_aggregation_drops_the_category_filter():
    """Filtering by one category would defeat grouping by category."""
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(200, {"items": []})
        analytics_api.categories(category="cpu", source="MANUAL")

    assert request.call_args.args == ("GET", "/analytics/categories")
    assert request.call_args.kwargs["params"] == {"source": "MANUAL"}


def test_trend_forwards_the_interval():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(200, {"interval": "hour", "points": []})
        analytics_api.trend(interval="hour", source="REALTIME")

    assert request.call_args.args == ("GET", "/analytics/trend")
    assert request.call_args.kwargs["params"] == {"interval": "hour", "source": "REALTIME"}


def test_realtime_data_can_be_selected():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(200, SUMMARY)
        analytics_api.summary(source="REALTIME")

    assert request.call_args.kwargs["params"]["source"] == "REALTIME"


def test_backend_failures_are_returned_not_raised():
    with patch("lib.auth.request") as request:
        request.return_value = ApiResult(401, {"detail": "Could not validate credentials"})
        result = analytics_api.summary()

    assert result.unauthorized is True


# --------------------------------------------------------------------------
# Presentation helpers
# --------------------------------------------------------------------------


def test_has_data_distinguishes_empty_results():
    assert analytics_api.has_data(SUMMARY) is True
    assert analytics_api.has_data(EMPTY_SUMMARY) is False
    assert analytics_api.has_data(None) is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "—"), (36.0, "36.00"), (1234.5, "1,234.50"), (0, "0.00")],
)
def test_metrics_render_readably(value, expected):
    assert analytics_api.format_metric(value) == expected
