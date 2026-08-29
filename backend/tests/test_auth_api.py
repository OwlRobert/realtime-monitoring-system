import jwt
import pytest
from sqlalchemy import select

from app.core.tokens import decode_access_token
from app.models.user import User, UserRole
from tests.test_tokens import tamper_signature

REGISTRATION = {
    "username": "alice",
    "email": "alice@example.com",
    "password": "correct horse battery staple",
}
CREDENTIALS = {"username": REGISTRATION["username"], "password": REGISTRATION["password"]}


async def register(client, **overrides):
    return await client.post("/auth/register", json={**REGISTRATION, **overrides})


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


async def test_registration_succeeds(client):
    response = await register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "alice"
    assert body["email"] == "alice@example.com"
    assert body["role"] == "USER"
    assert body["is_active"] is True
    assert isinstance(body["id"], int)


async def test_registration_response_never_exposes_password(client):
    response = await register(client)
    body = response.json()

    assert "hashed_password" not in body
    assert "password" not in body
    assert REGISTRATION["password"] not in response.text


async def test_password_is_stored_hashed(client, db_session):
    await register(client)

    user = (await db_session.execute(select(User))).scalar_one()
    assert user.hashed_password != REGISTRATION["password"]
    assert user.hashed_password.startswith("$2b$")


async def test_duplicate_username_is_rejected(client):
    await register(client)
    response = await register(client, email="someone.else@example.com")

    assert response.status_code == 409
    assert response.json()["detail"] == "Username already registered"


async def test_duplicate_email_is_rejected(client):
    await register(client)
    response = await register(client, username="bob")

    assert response.status_code == 409
    assert response.json()["detail"] == "Email already registered"


@pytest.mark.parametrize("requested_role", ["ADMIN", "VIEWER"])
async def test_self_registration_cannot_choose_role(client, db_session, requested_role):
    response = await client.post(
        "/auth/register", json={**REGISTRATION, "role": requested_role}
    )

    assert response.status_code == 201
    assert response.json()["role"] == "USER"
    user = (await db_session.execute(select(User))).scalar_one()
    assert user.role is UserRole.USER


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "ab"},
        {"email": "not-an-email"},
        {"password": "short"},
        {"password": "x" * 73},
    ],
)
async def test_invalid_registration_is_rejected(client, payload):
    response = await register(client, **payload)

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------


async def test_login_returns_bearer_jwt(client):
    await register(client)
    response = await client.post("/auth/login", json=CREDENTIALS)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"

    claims = decode_access_token(body["access_token"])
    assert claims["sub"] == "1"
    assert "exp" in claims


async def test_login_with_wrong_password_is_rejected(client):
    await register(client)
    response = await client.post(
        "/auth/login", json={**CREDENTIALS, "password": "wrong password"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_login_with_unknown_user_is_rejected(client):
    response = await client.post(
        "/auth/login", json={"username": "nobody", "password": "whatever password"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password"


async def test_inactive_user_cannot_log_in(client, db_session):
    await register(client)
    user = (await db_session.execute(select(User))).scalar_one()
    user.is_active = False
    await db_session.commit()

    response = await client.post("/auth/login", json=CREDENTIALS)

    assert response.status_code == 401


async def test_failed_logins_do_not_reveal_which_field_was_wrong(client):
    await register(client)
    unknown = await client.post(
        "/auth/login", json={"username": "nobody", "password": "whatever password"}
    )
    wrong_password = await client.post(
        "/auth/login", json={**CREDENTIALS, "password": "wrong password"}
    )

    assert unknown.status_code == wrong_password.status_code
    assert unknown.json() == wrong_password.json()


async def test_login_response_carries_no_user_details(client):
    await register(client)
    response = await client.post("/auth/login", json=CREDENTIALS)

    assert set(response.json()) == {"access_token", "token_type"}


async def test_issued_token_is_valid_and_untampered(client):
    await register(client)
    token = (await client.post("/auth/login", json=CREDENTIALS)).json()["access_token"]

    assert decode_access_token(token)["sub"] == "1"
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(tamper_signature(token))
