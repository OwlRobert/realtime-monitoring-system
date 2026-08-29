import pytest

from app.core.security import hash_password, verify_password

PASSWORD = "correct horse battery staple"


def test_hash_differs_from_plaintext():
    hashed = hash_password(PASSWORD)

    assert hashed != PASSWORD
    assert PASSWORD not in hashed
    assert hashed.startswith("$2b$")


def test_correct_password_verifies():
    assert verify_password(PASSWORD, hash_password(PASSWORD)) is True


@pytest.mark.parametrize(
    "wrong",
    ["wrong password", PASSWORD.upper(), PASSWORD + " ", "", " " + PASSWORD],
)
def test_incorrect_password_fails(wrong):
    assert verify_password(wrong, hash_password(PASSWORD)) is False


def test_same_password_hashes_differently():
    """bcrypt salts every hash, so two hashes of one password must differ."""
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)

    assert first != second
    assert verify_password(PASSWORD, first) is True
    assert verify_password(PASSWORD, second) is True


def test_unicode_password_round_trips():
    password = "pässwörd-密碼-🔐"

    assert verify_password(password, hash_password(password)) is True


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "not-a-hash",
        "$2b$12$tooshort",
        hash_password(PASSWORD)[:-1],
        PASSWORD,
    ],
)
def test_verify_returns_false_for_malformed_hash(stored):
    """A corrupt stored hash must return False, never raise or panic."""
    assert verify_password(PASSWORD, stored) is False
