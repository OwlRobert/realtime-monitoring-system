"""Analytics tests over a small, fully deterministic dataset.

Fixture data (values chosen so every aggregate is checkable by hand):

  cpu     MANUAL    12:00:00   10.0
  cpu     MANUAL    12:00:30   20.0
  cpu     REALTIME  12:01:00   30.0
  memory  MANUAL    12:01:30   40.0
  memory  REALTIME  12:02:00   80.0

  all: count 5, total 180.0, avg 36.0, min 10.0, max 80.0
"""

from datetime import datetime, timedelta

import pytest

from app.core.tokens import create_access_token
from app.models.data_record import DataRecord, DataSource
from app.models.user import UserRole
from tests.conftest import auth_headers

BASE = datetime(2026, 8, 29, 12, 0, 0)
DATASET = [
    ("cpu", DataSource.MANUAL, 0, 10.0),
    ("cpu", DataSource.MANUAL, 30, 20.0),
    ("cpu", DataSource.REALTIME, 60, 30.0),
    ("memory", DataSource.MANUAL, 90, 40.0),
    ("memory", DataSource.REALTIME, 120, 80.0),
]


@pytest.fixture
async def dataset(db_session):
    for category, source, offset, value in DATASET:
        db_session.add(
            DataRecord(
                title=f"{category} reading",
                value=value,
                category=category,
                timestamp=BASE + timedelta(seconds=offset),
                source=source,
                is_anomaly=value > 75.0,
                owner_id=None,
            )
        )
    await db_session.commit()


@pytest.fixture
async def reader(make_user):
    user = await make_user(UserRole.USER)
    return auth_headers(create_access_token(user.id))


async def get(client, path, headers, **params):
    return await client.get(f"/analytics/{path}", params=params, headers=headers)


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


async def test_summary_over_everything(client, dataset, reader):
    response = await get(client, "summary", reader)

    assert response.status_code == 200
    assert response.json() == {
        "count": 5,
        "total": 180.0,
        "average": 36.0,
        "minimum": 10.0,
        "maximum": 80.0,
    }


async def test_summary_of_an_empty_dataset(client, reader):
    response = await get(client, "summary", reader)

    assert response.status_code == 200
    assert response.json() == {
        "count": 0,
        "total": 0.0,
        "average": None,
        "minimum": None,
        "maximum": None,
    }


async def test_summary_filtered_by_category(client, dataset, reader):
    body = (await get(client, "summary", reader, category="cpu")).json()

    assert body == {
        "count": 3,
        "total": 60.0,
        "average": 20.0,
        "minimum": 10.0,
        "maximum": 30.0,
    }


async def test_summary_filtered_by_source(client, dataset, reader):
    body = (await get(client, "summary", reader, source="REALTIME")).json()

    assert body["count"] == 2
    assert body["total"] == 110.0
    assert body["average"] == 55.0
    assert body["minimum"] == 30.0
    assert body["maximum"] == 80.0


async def test_summary_filtered_by_time_range(client, dataset, reader):
    body = (
        await get(
            client,
            "summary",
            reader,
            start_time=(BASE + timedelta(seconds=30)).isoformat(),
            end_time=(BASE + timedelta(seconds=90)).isoformat(),
        )
    ).json()

    assert body["count"] == 3  # 20.0, 30.0, 40.0
    assert body["total"] == 90.0
    assert body["average"] == 30.0
    assert body["minimum"] == 20.0
    assert body["maximum"] == 40.0


async def test_summary_filters_combine(client, dataset, reader):
    body = (
        await get(client, "summary", reader, category="cpu", source="MANUAL")
    ).json()

    assert body["count"] == 2
    assert body["maximum"] == 20.0


async def test_summary_of_a_category_with_no_matches(client, dataset, reader):
    body = (await get(client, "summary", reader, category="nothing")).json()

    assert body["count"] == 0
    assert body["average"] is None


# --------------------------------------------------------------------------
# Category aggregation
# --------------------------------------------------------------------------


async def test_category_aggregation(client, dataset, reader):
    items = (await get(client, "categories", reader)).json()["items"]

    assert [item["category"] for item in items] == ["cpu", "memory"]
    assert items[0] == {
        "category": "cpu",
        "count": 3,
        "total": 60.0,
        "average": 20.0,
        "minimum": 10.0,
        "maximum": 30.0,
    }
    assert items[1] == {
        "category": "memory",
        "count": 2,
        "total": 120.0,
        "average": 60.0,
        "minimum": 40.0,
        "maximum": 80.0,
    }


async def test_category_aggregation_filtered_by_source(client, dataset, reader):
    items = (await get(client, "categories", reader, source="REALTIME")).json()["items"]

    assert [(item["category"], item["count"]) for item in items] == [
        ("cpu", 1),
        ("memory", 1),
    ]
    assert items[0]["average"] == 30.0


async def test_category_aggregation_filtered_by_time(client, dataset, reader):
    items = (
        await get(
            client,
            "categories",
            reader,
            end_time=(BASE + timedelta(seconds=60)).isoformat(),
        )
    ).json()["items"]

    assert len(items) == 1  # memory records are all later
    assert items[0]["category"] == "cpu"
    assert items[0]["count"] == 3


async def test_category_aggregation_of_an_empty_dataset(client, reader):
    assert (await get(client, "categories", reader)).json() == {"items": []}


# --------------------------------------------------------------------------
# Trend
# --------------------------------------------------------------------------


async def test_trend_buckets_by_minute_in_order(client, dataset, reader):
    body = (await get(client, "trend", reader, interval="minute")).json()

    assert body["interval"] == "minute"
    points = body["points"]
    assert [point["bucket"] for point in points] == [
        "2026-08-29T12:00:00",
        "2026-08-29T12:01:00",
        "2026-08-29T12:02:00",
    ]
    assert [point["count"] for point in points] == [2, 2, 1]
    assert [point["average"] for point in points] == [15.0, 35.0, 80.0]
    assert points[0]["minimum"] == 10.0
    assert points[0]["maximum"] == 20.0


async def test_trend_buckets_by_hour(client, dataset, reader):
    points = (await get(client, "trend", reader, interval="hour")).json()["points"]

    assert len(points) == 1
    assert points[0]["bucket"] == "2026-08-29T12:00:00"
    assert points[0]["count"] == 5
    assert points[0]["average"] == 36.0


async def test_trend_buckets_by_day(client, dataset, reader):
    points = (await get(client, "trend", reader, interval="day")).json()["points"]

    assert len(points) == 1
    assert points[0]["bucket"] == "2026-08-29T00:00:00"
    assert points[0]["count"] == 5


async def test_trend_filtered_by_category_and_source(client, dataset, reader):
    points = (
        await get(client, "trend", reader, category="cpu", source="MANUAL")
    ).json()["points"]

    assert len(points) == 1
    assert points[0]["count"] == 2
    assert points[0]["average"] == 15.0


async def test_trend_respects_the_time_range(client, dataset, reader):
    points = (
        await get(
            client,
            "trend",
            reader,
            start_time=(BASE + timedelta(seconds=60)).isoformat(),
        )
    ).json()["points"]

    assert [point["bucket"] for point in points] == [
        "2026-08-29T12:01:00",
        "2026-08-29T12:02:00",
    ]


async def test_trend_of_an_empty_dataset(client, reader):
    body = (await get(client, "trend", reader)).json()

    assert body == {"interval": "minute", "points": []}


# --------------------------------------------------------------------------
# Authorization
# --------------------------------------------------------------------------


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.USER, UserRole.VIEWER])
@pytest.mark.parametrize("path", ["summary", "categories", "trend"])
async def test_every_role_may_read_analytics(client, dataset, make_user, role, path):
    user = await make_user(role)
    response = await get(client, path, auth_headers(create_access_token(user.id)))

    assert response.status_code == 200


@pytest.mark.parametrize("path", ["summary", "categories", "trend"])
async def test_unauthenticated_requests_are_rejected(client, path):
    response = await client.get(f"/analytics/{path}")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
