"""Admin user management, audit log and database status."""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.tokens import create_access_token
from app.models.audit_log import AuditAction, AuditLog, ResourceType
from app.models.data_record import DataRecord, DataSource
from app.models.user import User, UserRole
from tests.conftest import auth_headers

SECRET_MARKERS = ("hashed_password", "$2b$", "access_token", "jwt_secret", "password")


def token_for(user):
    return auth_headers(create_access_token(user.id))


async def events(db_session, action: str | None = None) -> list[AuditLog]:
    statement = select(AuditLog).order_by(AuditLog.id)
    if action:
        statement = statement.where(AuditLog.action == action)
    return list((await db_session.execute(statement)).scalars().all())


@pytest.fixture
async def admin(make_user):
    return await make_user(UserRole.ADMIN)


# --------------------------------------------------------------------------
# Listing users
# --------------------------------------------------------------------------


async def test_admin_can_list_users(client, admin, make_user):
    await make_user(UserRole.USER)
    await make_user(UserRole.VIEWER)

    response = await client.get("/admin/users", headers=token_for(admin))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert {item["role"] for item in body["items"]} == {"ADMIN", "USER", "VIEWER"}


async def test_user_listing_never_exposes_credentials(client, admin):
    response = await client.get("/admin/users", headers=token_for(admin))

    for marker in SECRET_MARKERS:
        assert marker not in response.text
    assert set(response.json()["items"][0]) == {
        "id", "username", "email", "role", "is_active", "created_at",
    }


@pytest.mark.parametrize("role", [UserRole.USER, UserRole.VIEWER])
async def test_non_admins_may_not_list_users(client, make_user, role):
    user = await make_user(role)

    response = await client.get("/admin/users", headers=token_for(user))

    assert response.status_code == 403


async def test_unauthenticated_may_not_list_users(client):
    response = await client.get("/admin/users")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


# --------------------------------------------------------------------------
# Role changes
# --------------------------------------------------------------------------


async def test_admin_can_change_another_users_role(client, db_session, admin, make_user):
    target = await make_user(UserRole.USER)

    response = await client.patch(
        f"/admin/users/{target.id}/role",
        json={"role": "VIEWER"},
        headers=token_for(admin),
    )

    assert response.status_code == 200
    assert response.json()["role"] == "VIEWER"
    await db_session.refresh(target)
    assert target.role is UserRole.VIEWER


async def test_role_change_writes_an_audit_event(client, db_session, admin, make_user):
    target = await make_user(UserRole.USER)

    await client.patch(
        f"/admin/users/{target.id}/role",
        json={"role": "ADMIN"},
        headers=token_for(admin),
    )

    entry = (await events(db_session, AuditAction.USER_ROLE_CHANGE))[0]
    assert entry.user_id == admin.id           # who did it
    assert entry.resource_id == target.id      # to whom
    assert entry.resource_type == ResourceType.USER
    assert "USER -> ADMIN" in entry.detail
    assert entry.created_at is not None


async def test_setting_the_same_role_is_accepted_and_audited(client, db_session, admin, make_user):
    target = await make_user(UserRole.USER)

    response = await client.patch(
        f"/admin/users/{target.id}/role", json={"role": "USER"}, headers=token_for(admin)
    )

    assert response.status_code == 200
    assert response.json()["role"] == "USER"
    assert "USER -> USER" in (await events(db_session, AuditAction.USER_ROLE_CHANGE))[0].detail


async def test_admin_may_not_demote_themselves(client, db_session, admin):
    response = await client.patch(
        f"/admin/users/{admin.id}/role", json={"role": "USER"}, headers=token_for(admin)
    )

    assert response.status_code == 409
    assert "your own account" in response.json()["detail"]
    await db_session.refresh(admin)
    assert admin.role is UserRole.ADMIN
    assert await events(db_session, AuditAction.USER_ROLE_CHANGE) == []


async def test_admin_may_reassert_their_own_admin_role(client, admin):
    """Setting your own role to ADMIN cannot lock you out, so it is allowed."""
    response = await client.patch(
        f"/admin/users/{admin.id}/role", json={"role": "ADMIN"}, headers=token_for(admin)
    )

    assert response.status_code == 200


@pytest.mark.parametrize("payload", [{"role": "SUPERUSER"}, {"role": None}, {}, {"role": "USER", "id": 1}])
async def test_invalid_role_payloads_are_rejected(client, admin, make_user, payload):
    target = await make_user(UserRole.USER)

    response = await client.patch(
        f"/admin/users/{target.id}/role", json=payload, headers=token_for(admin)
    )

    assert response.status_code == 422


async def test_role_change_for_missing_user_is_404(client, admin):
    response = await client.patch(
        "/admin/users/9999/role", json={"role": "USER"}, headers=token_for(admin)
    )

    assert response.status_code == 404


@pytest.mark.parametrize("role", [UserRole.USER, UserRole.VIEWER])
async def test_non_admins_may_not_change_roles(client, make_user, role):
    actor = await make_user(role)
    target = await make_user(UserRole.USER, username="target_account")

    response = await client.patch(
        f"/admin/users/{target.id}/role", json={"role": "ADMIN"}, headers=token_for(actor)
    )

    assert response.status_code == 403


# --------------------------------------------------------------------------
# Status changes
# --------------------------------------------------------------------------


async def test_admin_can_deactivate_and_reactivate_a_user(client, db_session, admin, make_user):
    target = await make_user(UserRole.USER)

    deactivated = await client.patch(
        f"/admin/users/{target.id}/status",
        json={"is_active": False},
        headers=token_for(admin),
    )
    assert deactivated.json()["is_active"] is False
    await db_session.refresh(target)
    assert target.is_active is False

    reactivated = await client.patch(
        f"/admin/users/{target.id}/status",
        json={"is_active": True},
        headers=token_for(admin),
    )
    assert reactivated.json()["is_active"] is True


async def test_status_change_writes_an_audit_event(client, db_session, admin, make_user):
    target = await make_user(UserRole.USER)

    await client.patch(
        f"/admin/users/{target.id}/status",
        json={"is_active": False},
        headers=token_for(admin),
    )

    entry = (await events(db_session, AuditAction.USER_STATUS_CHANGE))[0]
    assert entry.user_id == admin.id
    assert entry.resource_id == target.id
    assert "active True -> False" in entry.detail


async def test_admin_may_not_deactivate_themselves(client, db_session, admin):
    response = await client.patch(
        f"/admin/users/{admin.id}/status",
        json={"is_active": False},
        headers=token_for(admin),
    )

    assert response.status_code == 409
    await db_session.refresh(admin)
    assert admin.is_active is True
    assert await events(db_session, AuditAction.USER_STATUS_CHANGE) == []


async def test_admin_may_reactivate_themselves(client, admin):
    response = await client.patch(
        f"/admin/users/{admin.id}/status", json={"is_active": True}, headers=token_for(admin)
    )

    assert response.status_code == 200


async def test_status_change_for_missing_user_is_404(client, admin):
    response = await client.patch(
        "/admin/users/9999/status", json={"is_active": False}, headers=token_for(admin)
    )

    assert response.status_code == 404


@pytest.mark.parametrize("role", [UserRole.USER, UserRole.VIEWER])
async def test_non_admins_may_not_change_status(client, make_user, role):
    actor = await make_user(role)
    target = await make_user(UserRole.USER, username="target_account")

    response = await client.patch(
        f"/admin/users/{target.id}/status",
        json={"is_active": False},
        headers=token_for(actor),
    )

    assert response.status_code == 403


async def test_deactivated_user_can_no_longer_authenticate(client, admin, make_user):
    target = await make_user(UserRole.USER)
    await client.patch(
        f"/admin/users/{target.id}/status",
        json={"is_active": False},
        headers=token_for(admin),
    )

    assert (await client.get("/auth/me", headers=token_for(target))).status_code == 401


# --------------------------------------------------------------------------
# Audit log API
# --------------------------------------------------------------------------


async def seed_events(db_session, count=5, action=AuditAction.RECORD_CREATE, user_id=None):
    base = datetime(2026, 8, 30, 12, 0, 0)
    for index in range(count):
        db_session.add(
            AuditLog(
                user_id=user_id,
                action=action,
                resource_type=ResourceType.RECORD,
                resource_id=index,
                detail=f"event {index}",
                ip_address="10.0.0.1",
                created_at=base + timedelta(minutes=index),
            )
        )
    await db_session.commit()


async def test_admin_can_read_the_audit_log(client, db_session, admin):
    await seed_events(db_session, 3)

    response = await client.get("/admin/audit-logs", headers=token_for(admin))

    assert response.status_code == 200
    assert response.json()["total"] == 3


async def test_audit_log_is_newest_first(client, db_session, admin):
    await seed_events(db_session, 3)

    items = (await client.get("/admin/audit-logs", headers=token_for(admin))).json()["items"]

    assert [item["detail"] for item in items] == ["event 2", "event 1", "event 0"]


async def test_audit_log_pagination(client, db_session, admin):
    await seed_events(db_session, 30)

    body = (
        await client.get(
            "/admin/audit-logs", params={"page": 2, "page_size": 10}, headers=token_for(admin)
        )
    ).json()

    assert body["total"] == 30
    assert body["page"] == 2
    assert body["pages"] == 3
    assert len(body["items"]) == 10


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"page_size": 101}])
async def test_invalid_audit_pagination_is_rejected(client, admin, params):
    assert (
        await client.get("/admin/audit-logs", params=params, headers=token_for(admin))
    ).status_code == 422


async def test_audit_log_filters(client, db_session, admin, make_user):
    other = await make_user(UserRole.USER)
    await seed_events(db_session, 2, action=AuditAction.RECORD_CREATE)
    await seed_events(db_session, 3, action=AuditAction.RECORD_DELETE, user_id=other.id)

    by_action = (
        await client.get(
            "/admin/audit-logs", params={"action": "RECORD_DELETE"}, headers=token_for(admin)
        )
    ).json()
    by_user = (
        await client.get(
            "/admin/audit-logs", params={"user_id": other.id}, headers=token_for(admin)
        )
    ).json()
    by_resource = (
        await client.get(
            "/admin/audit-logs", params={"resource_type": "RECORD"}, headers=token_for(admin)
        )
    ).json()
    by_time = (
        await client.get(
            "/admin/audit-logs",
            params={
                "start_time": "2026-08-30T12:01:00",
                "end_time": "2026-08-30T12:02:00",
            },
            headers=token_for(admin),
        )
    ).json()

    assert by_action["total"] == 3
    assert by_user["total"] == 3
    assert by_resource["total"] == 5
    assert by_time["total"] == 3  # 12:01 and 12:02 across both batches


async def test_empty_audit_log_page(client, admin):
    body = (await client.get("/admin/audit-logs", headers=token_for(admin))).json()

    assert body["items"] == []
    assert body["total"] == 0
    assert body["pages"] == 0


@pytest.mark.parametrize("role", [UserRole.USER, UserRole.VIEWER])
async def test_non_admins_may_not_read_the_audit_log(client, make_user, role):
    user = await make_user(role)

    assert (await client.get("/admin/audit-logs", headers=token_for(user))).status_code == 403


async def test_unauthenticated_may_not_read_the_audit_log(client):
    assert (await client.get("/admin/audit-logs")).status_code == 401


# --------------------------------------------------------------------------
# Which operations are audited
# --------------------------------------------------------------------------


async def test_registration_and_login_are_audited(client, db_session):
    await client.post(
        "/auth/register",
        json={"username": "auditee", "email": "auditee@example.com", "password": "a-strong-password"},
    )
    await client.post(
        "/auth/login", json={"username": "auditee", "password": "a-strong-password"}
    )

    actions = [entry.action for entry in await events(db_session)]
    assert actions == [AuditAction.USER_REGISTER, AuditAction.USER_LOGIN]


async def test_login_audit_never_records_the_password_or_token(client, db_session):
    await client.post(
        "/auth/register",
        json={"username": "auditee", "email": "auditee@example.com", "password": "super-secret-pw"},
    )
    response = await client.post(
        "/auth/login", json={"username": "auditee", "password": "super-secret-pw"}
    )
    token = response.json()["access_token"]

    for entry in await events(db_session):
        assert "super-secret-pw" not in (entry.detail or "")
        assert token not in (entry.detail or "")
        assert "$2b$" not in (entry.detail or "")


async def test_failed_login_writes_no_audit_event(client, db_session):
    await client.post(
        "/auth/login", json={"username": "nobody", "password": "whatever-password"}
    )

    assert await events(db_session, AuditAction.USER_LOGIN) == []


async def test_record_mutations_are_audited(client, db_session, make_user):
    user = await make_user(UserRole.USER)
    payload = {
        "title": "CPU",
        "value": 1.0,
        "category": "cpu",
        "timestamp": "2026-08-30T10:00:00",
    }

    created = await client.post("/records", json=payload, headers=token_for(user))
    record_id = created.json()["id"]
    await client.patch(f"/records/{record_id}", json={"value": 2.0}, headers=token_for(user))
    await client.delete(f"/records/{record_id}", headers=token_for(user))

    actions = [entry.action for entry in await events(db_session)]
    assert actions == [
        AuditAction.RECORD_CREATE,
        AuditAction.RECORD_UPDATE,
        AuditAction.RECORD_DELETE,
    ]
    update_entry = (await events(db_session, AuditAction.RECORD_UPDATE))[0]
    assert update_entry.detail == "fields: value"
    assert update_entry.resource_id == record_id


async def test_import_writes_one_event_for_the_operation(client, db_session, make_user):
    user = await make_user(UserRole.USER)
    rows = [
        {"title": f"Row {index}", "value": float(index), "category": "cpu",
         "timestamp": "2026-08-30T10:00:00"}
        for index in range(25)
    ]

    response = await client.post(
        "/records/import",
        headers=token_for(user),
        files={"file": ("rows.json", json.dumps(rows).encode(), "application/json")},
    )

    assert response.json()["imported"] == 25
    import_events = await events(db_session, AuditAction.RECORD_IMPORT)
    assert len(import_events) == 1                      # one per operation, not 25
    assert "rows=25" in import_events[0].detail
    assert len(await events(db_session)) == 1


async def test_failed_import_writes_no_audit_event(client, db_session, make_user):
    user = await make_user(UserRole.USER)

    await client.post(
        "/records/import",
        headers=token_for(user),
        files={"file": ("rows.csv", b"title,value\nx,1\n", "text/csv")},
    )

    assert await events(db_session) == []


async def test_generated_readings_are_not_audited(db_session):
    """The 1 Hz generator must never write one audit row per reading."""
    from app.realtime.buffer import RealtimePersistenceBuffer
    from app.realtime.generator import generate_reading

    class Factory:
        def __call__(self):
            return _session_context(db_session)

    buffer = RealtimePersistenceBuffer(
        lambda: _session_context(db_session), batch_size=10, interval_seconds=60
    )
    for _ in range(5):
        await buffer.add(generate_reading(threshold=80.0))
    await buffer.flush()

    stored = (await db_session.execute(select(DataRecord))).scalars().all()
    assert len(stored) == 5
    assert all(record.source is DataSource.REALTIME for record in stored)
    assert await events(db_session) == []


class _session_context:
    """Wrap the test session so the buffer can use it as a context manager."""

    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


# --------------------------------------------------------------------------
# Database status
# --------------------------------------------------------------------------


async def test_database_status_reports_counts(client, db_session, admin, make_user):
    await make_user(UserRole.USER)
    db_session.add_all(
        [
            DataRecord(title="m", value=1.0, category="cpu",
                       timestamp=datetime(2026, 8, 30, 10, 0, 0), source=DataSource.MANUAL),
            DataRecord(title="r", value=2.0, category="cpu",
                       timestamp=datetime(2026, 8, 30, 11, 0, 0), source=DataSource.REALTIME),
            DataRecord(title="r", value=3.0, category="cpu",
                       timestamp=datetime(2026, 8, 30, 12, 0, 0), source=DataSource.REALTIME),
        ]
    )
    await seed_events(db_session, 4)

    body = (await client.get("/admin/database-status", headers=token_for(admin))).json()

    assert body["healthy"] is True
    assert body["users"] == 2
    assert body["data_records"] == 3
    assert body["realtime_records"] == 2
    assert body["audit_logs"] == 4
    assert body["latest_realtime_timestamp"] == "2026-08-30T12:00:00"


async def test_database_status_exposes_no_credentials(client, admin):
    response = await client.get("/admin/database-status", headers=token_for(admin))
    body = response.json()

    assert set(body) == {
        "healthy", "dialect", "driver", "database", "pool",
        "users", "data_records", "realtime_records", "audit_logs",
        "latest_realtime_timestamp",
    }
    for marker in ("password", "secret", "@", "://"):
        assert marker not in json.dumps(body)


async def test_database_status_includes_pool_fields(client, admin):
    pool = (await client.get("/admin/database-status", headers=token_for(admin))).json()["pool"]

    assert set(pool) == {"size", "checked_in", "checked_out", "overflow"}


async def test_empty_database_status(client, admin):
    body = (await client.get("/admin/database-status", headers=token_for(admin))).json()

    assert body["data_records"] == 0
    assert body["realtime_records"] == 0
    assert body["latest_realtime_timestamp"] is None


@pytest.mark.parametrize("role", [UserRole.USER, UserRole.VIEWER])
async def test_non_admins_may_not_read_database_status(client, make_user, role):
    user = await make_user(role)

    assert (
        await client.get("/admin/database-status", headers=token_for(user))
    ).status_code == 403


async def test_unauthenticated_may_not_read_database_status(client):
    assert (await client.get("/admin/database-status")).status_code == 401
