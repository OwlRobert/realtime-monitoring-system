"""WebSocket endpoint tests.

These use Starlette's synchronous TestClient, the only client here that
speaks WebSocket. It runs the app (and therefore the lifespan-managed
generator) in its own event loop, so the database lives in a temporary
SQLite file that both loops can open.
"""

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.websockets import WebSocketDisconnect

from app.core.security import hash_password
from app.core.tokens import create_access_token
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.user import User, UserRole

PASSWORD_HASH = hash_password("a-strong-password")


@pytest.fixture
def database_url(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/realtime.db"

    async def create_tables() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create_tables())
    return url


def add_user(url: str, role: UserRole, *, is_active: bool = True) -> int:
    """Insert a user from outside the app's event loop."""

    async def create() -> int:
        engine = create_async_engine(url)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            user = User(
                username=f"{role.value.lower()}_ws",
                email=f"{role.value.lower()}_ws@example.com",
                hashed_password=PASSWORD_HASH,
                role=role,
                is_active=is_active,
            )
            session.add(user)
            await session.commit()
            user_id = user.id
        await engine.dispose()
        return user_id

    return asyncio.run(create())


@pytest.fixture
def ws_client(database_url):
    engine_holder: list = []

    async def override_get_session():
        if not engine_holder:
            engine_holder.append(create_async_engine(database_url))
        factory = async_sessionmaker(engine_holder[0], expire_on_commit=False)
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def url_for(token: str | None) -> str:
    return "/ws/realtime" if token is None else f"/ws/realtime?token={token}"


# --------------------------------------------------------------------------
# Successful connections
# --------------------------------------------------------------------------


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.USER, UserRole.VIEWER])
def test_every_role_can_connect_and_receive(ws_client, database_url, role):
    token = create_access_token(add_user(database_url, role))

    with ws_client.websocket_connect(url_for(token)) as websocket:
        payload = websocket.receive_json()

    assert payload["source"] == "REALTIME"


def test_payload_contains_the_expected_fields(ws_client, database_url):
    token = create_access_token(add_user(database_url, UserRole.USER))

    with ws_client.websocket_connect(url_for(token)) as websocket:
        payload = websocket.receive_json()

    assert set(payload) == {
        "title",
        "value",
        "category",
        "timestamp",
        "source",
        "is_anomaly",
    }
    assert payload["category"] in {"cpu", "memory", "temperature"}
    assert isinstance(payload["value"], float)
    assert isinstance(payload["is_anomaly"], bool)


def test_client_receives_a_continuous_stream(ws_client, database_url):
    token = create_access_token(add_user(database_url, UserRole.USER))

    with ws_client.websocket_connect(url_for(token)) as websocket:
        payloads = [websocket.receive_json() for _ in range(3)]

    assert len(payloads) == 3
    assert all(payload["source"] == "REALTIME" for payload in payloads)


def test_anomaly_flag_matches_the_configured_threshold(ws_client, database_url):
    from app.core.config import get_settings

    threshold = get_settings().anomaly_threshold
    token = create_access_token(add_user(database_url, UserRole.USER))

    with ws_client.websocket_connect(url_for(token)) as websocket:
        payloads = [websocket.receive_json() for _ in range(20)]

    for payload in payloads:
        assert payload["is_anomaly"] is (payload["value"] > threshold)


# --------------------------------------------------------------------------
# Rejected connections
# --------------------------------------------------------------------------


def test_missing_token_is_rejected(ws_client):
    with pytest.raises(WebSocketDisconnect) as rejected:
        with ws_client.websocket_connect(url_for(None)):
            pass

    assert rejected.value.code == 1008


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c"])
def test_invalid_token_is_rejected(ws_client, token):
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect(url_for(token)):
            pass


def test_expired_token_is_rejected(ws_client, database_url):
    user_id = add_user(database_url, UserRole.USER)
    expired = create_access_token(user_id, expires_delta=timedelta(seconds=-1))

    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect(url_for(expired)):
            pass


def test_token_for_unknown_user_is_rejected(ws_client):
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect(url_for(create_access_token(9999))):
            pass


def test_inactive_user_is_rejected(ws_client, database_url):
    token = create_access_token(add_user(database_url, UserRole.USER, is_active=False))

    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect(url_for(token)):
            pass


# --------------------------------------------------------------------------
# Fan-out and disconnects
# --------------------------------------------------------------------------


def test_multiple_clients_receive_the_broadcast(ws_client, database_url):
    first = create_access_token(add_user(database_url, UserRole.USER))
    second = create_access_token(add_user(database_url, UserRole.VIEWER))

    with ws_client.websocket_connect(url_for(first)) as one:
        with ws_client.websocket_connect(url_for(second)) as two:
            assert one.receive_json()["source"] == "REALTIME"
            assert two.receive_json()["source"] == "REALTIME"


def test_one_client_leaving_does_not_disturb_another(ws_client, database_url):
    first = create_access_token(add_user(database_url, UserRole.USER))
    second = create_access_token(add_user(database_url, UserRole.VIEWER))

    with ws_client.websocket_connect(url_for(first)) as survivor:
        with ws_client.websocket_connect(url_for(second)) as leaving:
            leaving.receive_json()
        # The second client has now disconnected; the stream must continue.
        assert survivor.receive_json()["source"] == "REALTIME"
        assert survivor.receive_json()["source"] == "REALTIME"
