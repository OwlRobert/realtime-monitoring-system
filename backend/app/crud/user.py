from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole


async def get_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def create(
    session: AsyncSession,
    *,
    username: str,
    email: str,
    hashed_password: str,
    role: UserRole = UserRole.USER,
) -> User:
    """Persist a new user. The caller supplies an already-hashed password."""
    user = User(
        username=username,
        email=email,
        hashed_password=hashed_password,
        role=role,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
