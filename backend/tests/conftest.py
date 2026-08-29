import os

# Settings require a JWT secret; supply a throwaway one before app modules load.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-used-outside-tests")
# Keep the realtime loop fast so tests never wait a real second.
os.environ.setdefault("REALTIME_INTERVAL_SECONDS", "0.01")
# The app-wide buffer must never reach a flush trigger during WebSocket
# tests; buffer behaviour is tested directly with its own configuration.
os.environ.setdefault("BATCH_SIZE", "1000000")
os.environ.setdefault("BATCH_INTERVAL_SECONDS", "3600")

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402  (registers the table on Base.metadata)


@pytest.fixture
async def session_factory(tmp_path):
    """A fresh database per test, created from the ORM metadata.

    File-backed rather than in-memory: a connection invalidated mid-test
    (for instance by cancelling a flush) would otherwise reopen an empty
    in-memory database and lose the schema.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)

    await engine.dispose()


@pytest.fixture
async def db_session(session_factory):
    async with session_factory() as session:
        yield session


@pytest.fixture
async def make_user(db_session):
    """Create a user with a given role, straight through the ORM."""
    from app.core.security import hash_password
    from app.crud import user as user_crud
    from app.models.user import UserRole

    async def _make_user(
        role: UserRole = UserRole.USER, *, username: str | None = None
    ) -> User:
        name = username or f"{role.value.lower()}_account"
        return await user_crud.create(
            db_session,
            username=name,
            email=f"{name}@example.com",
            hashed_password=hash_password("a-strong-password"),
            role=role,
        )

    return _make_user


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def client(session_factory):
    """HTTP client bound to the app, with the database dependency overridden."""

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()
