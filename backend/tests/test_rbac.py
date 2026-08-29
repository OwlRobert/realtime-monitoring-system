"""RBAC tests.

The dependency is exercised through a probe app defined here, so the
production API stays free of endpoints that exist only for testing.
"""

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import require_admin, require_read, require_write
from app.core.security import hash_password
from app.core.tokens import create_access_token
from app.crud import user as user_crud
from app.db.session import get_session
from app.models.user import User, UserRole

probe_app = FastAPI()


@probe_app.get("/probe/read")
async def read_probe(current_user: User = Depends(require_read)) -> dict[str, str]:
    return {"role": current_user.role.value}


@probe_app.post("/probe/write")
async def write_probe(current_user: User = Depends(require_write)) -> dict[str, str]:
    return {"role": current_user.role.value}


@probe_app.get("/probe/admin")
async def admin_probe(current_user: User = Depends(require_admin)) -> dict[str, str]:
    return {"role": current_user.role.value}


ENDPOINTS = {
    "read": ("GET", "/probe/read"),
    "write": ("POST", "/probe/write"),
    "admin": ("GET", "/probe/admin"),
}


@pytest.fixture
async def rbac_client(session_factory):
    async def override_get_session():
        async with session_factory() as session:
            yield session

    probe_app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=probe_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    probe_app.dependency_overrides.clear()


@pytest.fixture
async def make_user(db_session):
    async def _make_user(role: UserRole, *, is_active: bool = True) -> User:
        name = role.value.lower()
        user = await user_crud.create(
            db_session,
            username=f"{name}_account",
            email=f"{name}@example.com",
            hashed_password=hash_password("a-strong-password"),
            role=role,
        )
        if not is_active:
            user.is_active = False
            await db_session.commit()
        return user

    return _make_user


async def call(client, endpoint: str, token: str | None = None):
    method, path = ENDPOINTS[endpoint]
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return await client.request(method, path, headers=headers)


# --------------------------------------------------------------------------
# Admin-only permission
# --------------------------------------------------------------------------


async def test_admin_allowed_on_admin_only(rbac_client, make_user):
    user = await make_user(UserRole.ADMIN)
    response = await call(rbac_client, "admin", create_access_token(user.id))

    assert response.status_code == 200
    assert response.json() == {"role": "ADMIN"}


@pytest.mark.parametrize("role", [UserRole.USER, UserRole.VIEWER])
async def test_non_admin_rejected_from_admin_only(rbac_client, make_user, role):
    user = await make_user(role)
    response = await call(rbac_client, "admin", create_access_token(user.id))

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions"


# --------------------------------------------------------------------------
# Write permission
# --------------------------------------------------------------------------


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.USER])
async def test_write_allowed_for_admin_and_user(rbac_client, make_user, role):
    user = await make_user(role)
    response = await call(rbac_client, "write", create_access_token(user.id))

    assert response.status_code == 200
    assert response.json() == {"role": role.value}


async def test_viewer_rejected_from_write(rbac_client, make_user):
    user = await make_user(UserRole.VIEWER)
    response = await call(rbac_client, "write", create_access_token(user.id))

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions"


# --------------------------------------------------------------------------
# Read permission
# --------------------------------------------------------------------------


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.USER, UserRole.VIEWER])
async def test_read_allowed_for_every_role(rbac_client, make_user, role):
    user = await make_user(role)
    response = await call(rbac_client, "read", create_access_token(user.id))

    assert response.status_code == 200
    assert response.json() == {"role": role.value}


# --------------------------------------------------------------------------
# Authentication still precedes authorization
# --------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
async def test_unauthenticated_request_is_401(rbac_client, endpoint):
    response = await call(rbac_client, endpoint)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
async def test_invalid_token_is_401(rbac_client, endpoint):
    response = await call(rbac_client, endpoint, "not-a-jwt")

    assert response.status_code == 401


async def test_inactive_admin_is_rejected_before_authorization(rbac_client, make_user):
    """An inactive Admin fails authentication, so 401 wins over 403."""
    user = await make_user(UserRole.ADMIN, is_active=False)
    response = await call(rbac_client, "admin", create_access_token(user.id))

    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


async def test_inactive_viewer_on_admin_endpoint_is_401_not_403(rbac_client, make_user):
    user = await make_user(UserRole.VIEWER, is_active=False)
    response = await call(rbac_client, "admin", create_access_token(user.id))

    assert response.status_code == 401
