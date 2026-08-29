from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.tokens import create_access_token
from app.models.user import User
from tests.test_auth_api import CREDENTIALS, REGISTRATION, register
from tests.test_tokens import tamper_signature


async def login(client) -> str:
    response = await client.post("/auth/login", json=CREDENTIALS)
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_valid_token_returns_current_user(client):
    await register(client)
    response = await client.get("/auth/me", headers=auth(await login(client)))

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == REGISTRATION["username"]
    assert body["email"] == REGISTRATION["email"]
    assert body["role"] == "USER"
    assert body["is_active"] is True


async def test_response_never_exposes_password(client):
    await register(client)
    response = await client.get("/auth/me", headers=auth(await login(client)))

    assert "hashed_password" not in response.json()
    assert "$2b$" not in response.text
    assert REGISTRATION["password"] not in response.text


async def test_missing_authorization_header_is_rejected(client):
    response = await client.get("/auth/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    [
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic abcdef"},
        {"Authorization": "token abcdef"},
    ],
)
async def test_malformed_credentials_are_rejected(client, header):
    response = await client.get("/auth/me", headers=header)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_tampered_token_is_rejected(client):
    await register(client)
    token = tamper_signature(await login(client))

    response = await client.get("/auth/me", headers=auth(token))

    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


async def test_expired_token_is_rejected(client):
    await register(client)
    expired = create_access_token(1, expires_delta=timedelta(seconds=-1))

    response = await client.get("/auth/me", headers=auth(expired))

    assert response.status_code == 401


async def test_token_for_nonexistent_user_is_rejected(client):
    response = await client.get("/auth/me", headers=auth(create_access_token(9999)))

    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


async def test_non_numeric_subject_is_rejected(client):
    response = await client.get("/auth/me", headers=auth(create_access_token("alice")))

    assert response.status_code == 401


async def test_inactive_user_is_rejected(client, db_session):
    await register(client)
    token = await login(client)

    user = (await db_session.execute(select(User))).scalar_one()
    user.is_active = False
    await db_session.commit()

    response = await client.get("/auth/me", headers=auth(token))

    assert response.status_code == 401
