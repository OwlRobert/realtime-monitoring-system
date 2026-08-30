"""Demo-account seeding that runs after migrations, before the API starts."""

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.security import verify_password
from app.models.user import User, UserRole
from app.services.bootstrap import CREATED, EXISTS, SKIPPED, seed_demo_accounts

ADMIN_PASSWORD = "demo-admin-pw"
USER_PASSWORD = "demo-user-pw"
VIEWER_PASSWORD = "demo-viewer-pw"


def settings(**overrides) -> Settings:
    """Settings with all three demo accounts configured, unless overridden."""
    values = {
        "jwt_secret_key": "test-secret",
        "admin_username": "admin",
        "admin_email": "admin@example.com",
        "admin_password": ADMIN_PASSWORD,
        "user_username": "user",
        "user_email": "user@example.com",
        "user_password": USER_PASSWORD,
        "viewer_username": "viewer",
        "viewer_email": "viewer@example.com",
        "viewer_password": VIEWER_PASSWORD,
    }
    values.update(overrides)
    return Settings(**values)


async def stored(db_session) -> list[User]:
    return list((await db_session.execute(select(User).order_by(User.id))).scalars().all())


# --------------------------------------------------------------------------
# Seeding an empty database
# --------------------------------------------------------------------------


async def test_all_three_demo_accounts_are_created(db_session):
    outcomes = await seed_demo_accounts(db_session, settings())

    assert outcomes == [("ADMIN", CREATED), ("USER", CREATED), ("VIEWER", CREATED)]
    users = await stored(db_session)
    assert [user.username for user in users] == ["admin", "user", "viewer"]


async def test_roles_are_assigned_correctly(db_session):
    await seed_demo_accounts(db_session, settings())

    roles = {user.username: user.role for user in await stored(db_session)}
    assert roles == {
        "admin": UserRole.ADMIN,
        "user": UserRole.USER,
        "viewer": UserRole.VIEWER,
    }


async def test_seeded_accounts_are_active_and_can_authenticate(db_session):
    await seed_demo_accounts(db_session, settings())

    for user, password in zip(
        await stored(db_session), [ADMIN_PASSWORD, USER_PASSWORD, VIEWER_PASSWORD]
    ):
        assert user.is_active is True
        assert verify_password(password, user.hashed_password) is True


async def test_passwords_are_hashed_never_stored_in_plain_text(db_session):
    await seed_demo_accounts(db_session, settings())

    for user in await stored(db_session):
        assert user.hashed_password.startswith("$2b$")
        assert user.hashed_password not in (ADMIN_PASSWORD, USER_PASSWORD, VIEWER_PASSWORD)
    dumped = str([vars(user) for user in await stored(db_session)])
    for password in (ADMIN_PASSWORD, USER_PASSWORD, VIEWER_PASSWORD):
        assert password not in dumped


async def test_emails_are_normalised(db_session):
    await seed_demo_accounts(db_session, settings(admin_email="ADMIN@Example.COM"))

    assert (await stored(db_session))[0].email == "admin@example.com"


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


async def test_second_run_creates_nothing(db_session):
    await seed_demo_accounts(db_session, settings())

    outcomes = await seed_demo_accounts(db_session, settings())

    assert outcomes == [("ADMIN", EXISTS), ("USER", EXISTS), ("VIEWER", EXISTS)]
    assert len(await stored(db_session)) == 3


async def test_existing_account_is_never_modified(db_session):
    """A restart must not undo a role change or reset a chosen password."""
    await seed_demo_accounts(db_session, settings())
    admin = (await stored(db_session))[0]
    admin.role = UserRole.VIEWER            # an administrator demoted it later
    admin.hashed_password = "$2b$12$" + "x" * 53
    admin.is_active = False
    await db_session.commit()

    outcomes = await seed_demo_accounts(db_session, settings())

    await db_session.refresh(admin)
    assert outcomes[0] == ("ADMIN", EXISTS)
    assert admin.role is UserRole.VIEWER     # not restored to ADMIN
    assert admin.hashed_password.endswith("x" * 53)   # password not reset
    assert admin.is_active is False


async def test_matching_by_email_also_counts_as_existing(db_session):
    """Re-seeding under a new username must not violate the email constraint."""
    await seed_demo_accounts(db_session, settings())

    outcomes = await seed_demo_accounts(db_session, settings(admin_username="different"))

    assert outcomes[0] == ("ADMIN", EXISTS)
    assert len(await stored(db_session)) == 3


# --------------------------------------------------------------------------
# Missing configuration
# --------------------------------------------------------------------------


async def test_no_configuration_seeds_nothing_and_does_not_raise(db_session):
    outcomes = await seed_demo_accounts(
        db_session,
        Settings(jwt_secret_key="test-secret"),   # no demo variables at all
    )

    assert outcomes == [("ADMIN", SKIPPED), ("USER", SKIPPED), ("VIEWER", SKIPPED)]
    assert await stored(db_session) == []


@pytest.mark.parametrize(
    "missing", ["admin_username", "admin_email", "admin_password"]
)
async def test_a_partially_configured_role_is_skipped(db_session, missing):
    outcomes = await seed_demo_accounts(db_session, settings(**{missing: None}))

    assert outcomes[0] == ("ADMIN", SKIPPED)
    assert [user.username for user in await stored(db_session)] == ["user", "viewer"]


async def test_roles_can_be_seeded_independently(db_session):
    outcomes = await seed_demo_accounts(
        db_session, settings(user_password=None, viewer_password=None)
    )

    assert outcomes == [("ADMIN", CREATED), ("USER", SKIPPED), ("VIEWER", SKIPPED)]
    assert [user.username for user in await stored(db_session)] == ["admin"]


# --------------------------------------------------------------------------
# Registration is unaffected
# --------------------------------------------------------------------------


async def test_public_registration_still_creates_a_plain_user(client, db_session):
    """Seeding adds no path for a client to choose its own role."""
    response = await client.post(
        "/auth/register",
        json={
            "username": "newcomer",
            "email": "newcomer@example.com",
            "password": "a-strong-password",
            "role": "ADMIN",
        },
    )

    assert response.status_code == 201
    assert response.json()["role"] == "USER"
    created = (await stored(db_session))[0]
    assert created.role is UserRole.USER
