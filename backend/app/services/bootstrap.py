"""Seed the demo accounts a reviewer needs to exercise all three roles.

Runs once at container start, after Alembic and before Uvicorn. It uses the
application's own async ORM stack and password hashing — no raw SQL, and no
credentials in source: every value comes from the environment.

The operation is idempotent. An account that already exists is left exactly
as it is: its role is not changed and its password is not reset, so restarts
never clobber changes an administrator made through the API.
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.security import hash_password
from app.crud import user as user_crud
from app.db.session import SessionLocal, engine
from app.models.user import UserRole

logger = logging.getLogger(__name__)

CREATED = "created"
EXISTS = "already exists"
SKIPPED = "not configured"


def _demo_accounts(settings: Settings) -> list[tuple[UserRole, str | None, str | None, str | None]]:
    return [
        (UserRole.ADMIN, settings.admin_username, settings.admin_email, settings.admin_password),
        (UserRole.USER, settings.user_username, settings.user_email, settings.user_password),
        (UserRole.VIEWER, settings.viewer_username, settings.viewer_email, settings.viewer_password),
    ]


async def seed_demo_accounts(
    session: AsyncSession, settings: Settings | None = None
) -> list[tuple[str, str]]:
    """Create any configured demo account that does not exist yet.

    Returns one (label, outcome) pair per role for logging and tests.
    """
    settings = settings or get_settings()
    outcomes: list[tuple[str, str]] = []

    for role, username, email, password in _demo_accounts(settings):
        label = role.value

        if not (username and email and password):
            outcomes.append((label, SKIPPED))
            continue

        # Match on either identity, since both carry a unique constraint.
        existing = await user_crud.get_by_username(session, username)
        if existing is None:
            existing = await user_crud.get_by_email(session, email.lower())
        if existing is not None:
            outcomes.append((label, EXISTS))
            continue

        await user_crud.create(
            session,
            username=username,
            email=email.lower(),
            hashed_password=hash_password(password),
            role=role,
        )
        outcomes.append((label, CREATED))

    return outcomes


async def run() -> list[tuple[str, str]]:
    """Entry point used by the container entrypoint."""
    settings = get_settings()
    configure_logging(settings.log_level)

    async with SessionLocal() as session:
        outcomes = await seed_demo_accounts(session, settings)

    for label, outcome in outcomes:
        # Usernames and roles only — passwords are never logged.
        logger.info("Demo account %s: %s", label, outcome)

    await engine.dispose()
    return outcomes


if __name__ == "__main__":
    asyncio.run(run())
