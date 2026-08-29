from datetime import datetime, timedelta

import pytest

from app.core.tokens import create_access_token
from app.models.data_record import DataRecord, DataSource
from app.models.user import UserRole
from tests.conftest import auth_headers

BASE_TIME = datetime(2026, 8, 1, 12, 0, 0)
PAYLOAD = {
    "title": "CPU load",
    "value": 42.5,
    "category": "cpu",
    "timestamp": BASE_TIME.isoformat(),
}


def token_for(user) -> dict[str, str]:
    return auth_headers(create_access_token(user.id))


async def seed(db_session, count: int, *, owner_id: int | None = None, **overrides):
    """Insert `count` records one minute apart, oldest first."""
    records = []
    for index in range(count):
        record = DataRecord(
            title=f"record {index}",
            value=float(index),
            category=overrides.get("category", "cpu"),
            timestamp=overrides.get("timestamp", BASE_TIME) + timedelta(minutes=index),
            source=overrides.get("source", DataSource.MANUAL),
            owner_id=owner_id,
        )
        db_session.add(record)
        records.append(record)
    await db_session.commit()
    return records


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.USER])
async def test_admin_and_user_can_create(client, make_user, role):
    user = await make_user(role)
    response = await client.post("/records", json=PAYLOAD, headers=token_for(user))

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "CPU load"
    assert body["source"] == "MANUAL"
    assert body["is_anomaly"] is False


async def test_viewer_cannot_create(client, make_user):
    viewer = await make_user(UserRole.VIEWER)
    response = await client.post("/records", json=PAYLOAD, headers=token_for(viewer))

    assert response.status_code == 403


async def test_owner_is_taken_from_the_authenticated_user(client, make_user):
    user = await make_user(UserRole.USER)
    other = await make_user(UserRole.USER, username="someone_else")

    response = await client.post(
        "/records",
        json={**PAYLOAD, "owner_id": other.id},
        headers=token_for(user),
    )

    # owner_id is forbidden input, so the request is rejected outright...
    assert response.status_code == 422

    # ...and a clean request is owned by the caller.
    created = await client.post("/records", json=PAYLOAD, headers=token_for(user))
    assert created.json()["owner_id"] == user.id


async def test_client_cannot_create_realtime_records(client, make_user):
    user = await make_user(UserRole.USER)
    response = await client.post(
        "/records", json={**PAYLOAD, "source": "REALTIME"}, headers=token_for(user)
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------
# List: pagination, filtering, sorting
# --------------------------------------------------------------------------


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.USER, UserRole.VIEWER])
async def test_every_role_can_list(client, db_session, make_user, role):
    user = await make_user(role)
    await seed(db_session, 3)

    response = await client.get("/records", headers=token_for(user))

    assert response.status_code == 200
    assert response.json()["total"] == 3


async def test_pagination_metadata(client, db_session, make_user):
    user = await make_user(UserRole.USER)
    await seed(db_session, 25)

    response = await client.get(
        "/records", params={"page": 2, "page_size": 10}, headers=token_for(user)
    )
    body = response.json()

    assert body["total"] == 25
    assert body["page"] == 2
    assert body["page_size"] == 10
    assert body["pages"] == 3
    assert len(body["items"]) == 10


async def test_empty_result_has_zero_pages(client, make_user):
    user = await make_user(UserRole.USER)
    response = await client.get("/records", headers=token_for(user))

    assert response.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "pages": 0,
    }


async def test_category_filter(client, db_session, make_user):
    user = await make_user(UserRole.USER)
    await seed(db_session, 2, category="cpu")
    await seed(db_session, 3, category="memory")

    response = await client.get(
        "/records", params={"category": "memory"}, headers=token_for(user)
    )
    body = response.json()

    assert body["total"] == 3
    assert {item["category"] for item in body["items"]} == {"memory"}


async def test_source_filter(client, db_session, make_user):
    user = await make_user(UserRole.USER)
    await seed(db_session, 2, source=DataSource.MANUAL)
    await seed(db_session, 4, source=DataSource.REALTIME)

    response = await client.get(
        "/records", params={"source": "REALTIME"}, headers=token_for(user)
    )
    body = response.json()

    assert body["total"] == 4
    assert {item["source"] for item in body["items"]} == {"REALTIME"}


async def test_timestamp_range_filter(client, db_session, make_user):
    user = await make_user(UserRole.USER)
    await seed(db_session, 10)  # BASE_TIME .. BASE_TIME + 9 minutes

    response = await client.get(
        "/records",
        params={
            "start": (BASE_TIME + timedelta(minutes=3)).isoformat(),
            "end": (BASE_TIME + timedelta(minutes=5)).isoformat(),
        },
        headers=token_for(user),
    )
    body = response.json()

    assert body["total"] == 3
    assert [item["value"] for item in body["items"]] == [5.0, 4.0, 3.0]


@pytest.mark.parametrize(
    ("order", "expected"),
    [("asc", [0.0, 1.0, 2.0]), ("desc", [2.0, 1.0, 0.0])],
)
async def test_sorting(client, db_session, make_user, order, expected):
    user = await make_user(UserRole.USER)
    await seed(db_session, 3)

    response = await client.get(
        "/records",
        params={"sort_by": "value", "order": order},
        headers=token_for(user),
    )

    assert [item["value"] for item in response.json()["items"]] == expected


@pytest.mark.parametrize(
    "params",
    [
        {"sort_by": "hashed_password"},
        {"sort_by": "id; DROP TABLE data_records"},
        {"order": "sideways"},
        {"page": 0},
        {"page_size": 101},
    ],
)
async def test_invalid_query_parameters_are_rejected(client, make_user, params):
    user = await make_user(UserRole.USER)
    response = await client.get("/records", params=params, headers=token_for(user))

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Read single
# --------------------------------------------------------------------------


async def test_read_single_record(client, db_session, make_user):
    user = await make_user(UserRole.USER)
    other = await make_user(UserRole.USER, username="someone_else")
    record = (await seed(db_session, 1, owner_id=other.id))[0]

    response = await client.get(f"/records/{record.id}", headers=token_for(user))

    # Reading is not ownership-restricted.
    assert response.status_code == 200
    assert response.json()["id"] == record.id


async def test_read_missing_record_is_404(client, make_user):
    user = await make_user(UserRole.USER)
    response = await client.get("/records/9999", headers=token_for(user))

    assert response.status_code == 404


# --------------------------------------------------------------------------
# Update
# --------------------------------------------------------------------------


async def test_user_can_update_own_record(client, db_session, make_user):
    user = await make_user(UserRole.USER)
    record = (await seed(db_session, 1, owner_id=user.id))[0]

    response = await client.patch(
        f"/records/{record.id}", json={"value": 99.0}, headers=token_for(user)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["value"] == 99.0
    assert body["title"] == "record 0"  # untouched by the partial update


async def test_user_cannot_update_another_users_record(client, db_session, make_user):
    user = await make_user(UserRole.USER)
    other = await make_user(UserRole.USER, username="someone_else")
    record = (await seed(db_session, 1, owner_id=other.id))[0]

    response = await client.patch(
        f"/records/{record.id}", json={"value": 99.0}, headers=token_for(user)
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Not permitted to modify this record"


async def test_admin_can_update_another_users_record(client, db_session, make_user):
    admin = await make_user(UserRole.ADMIN)
    other = await make_user(UserRole.USER, username="someone_else")
    record = (await seed(db_session, 1, owner_id=other.id))[0]

    response = await client.patch(
        f"/records/{record.id}", json={"value": 99.0}, headers=token_for(admin)
    )

    assert response.status_code == 200
    assert response.json()["owner_id"] == other.id  # ownership is unchanged


async def test_viewer_cannot_update(client, db_session, make_user):
    viewer = await make_user(UserRole.VIEWER)
    record = (await seed(db_session, 1))[0]

    response = await client.patch(
        f"/records/{record.id}", json={"value": 99.0}, headers=token_for(viewer)
    )

    assert response.status_code == 403


async def test_update_cannot_change_server_controlled_fields(client, db_session, make_user):
    user = await make_user(UserRole.USER)
    record = (await seed(db_session, 1, owner_id=user.id))[0]

    for payload in [{"source": "REALTIME"}, {"owner_id": 999}, {"is_anomaly": True}]:
        response = await client.patch(
            f"/records/{record.id}", json=payload, headers=token_for(user)
        )
        assert response.status_code == 422


async def test_update_missing_record_is_404(client, make_user):
    user = await make_user(UserRole.USER)
    response = await client.patch(
        "/records/9999", json={"value": 1.0}, headers=token_for(user)
    )

    assert response.status_code == 404


# --------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------


async def test_user_can_delete_own_record(client, db_session, make_user):
    user = await make_user(UserRole.USER)
    record = (await seed(db_session, 1, owner_id=user.id))[0]

    response = await client.delete(f"/records/{record.id}", headers=token_for(user))

    assert response.status_code == 204
    assert (
        await client.get(f"/records/{record.id}", headers=token_for(user))
    ).status_code == 404


async def test_user_cannot_delete_another_users_record(client, db_session, make_user):
    user = await make_user(UserRole.USER)
    other = await make_user(UserRole.USER, username="someone_else")
    record = (await seed(db_session, 1, owner_id=other.id))[0]

    response = await client.delete(f"/records/{record.id}", headers=token_for(user))

    assert response.status_code == 403


async def test_admin_can_delete_another_users_record(client, db_session, make_user):
    admin = await make_user(UserRole.ADMIN)
    other = await make_user(UserRole.USER, username="someone_else")
    record = (await seed(db_session, 1, owner_id=other.id))[0]

    response = await client.delete(f"/records/{record.id}", headers=token_for(admin))

    assert response.status_code == 204


async def test_viewer_cannot_delete(client, db_session, make_user):
    viewer = await make_user(UserRole.VIEWER)
    record = (await seed(db_session, 1))[0]

    response = await client.delete(f"/records/{record.id}", headers=token_for(viewer))

    assert response.status_code == 403


async def test_delete_missing_record_is_404(client, make_user):
    user = await make_user(UserRole.USER)
    response = await client.delete("/records/9999", headers=token_for(user))

    assert response.status_code == 404


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/records"),
        ("GET", "/records"),
        ("GET", "/records/1"),
        ("PATCH", "/records/1"),
        ("DELETE", "/records/1"),
    ],
)
async def test_unauthenticated_requests_are_401(client, method, path):
    response = await client.request(method, path, json={})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
