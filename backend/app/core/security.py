"""Password hashing utilities.

bcrypt is used directly: it is the de-facto standard for password storage,
it salts every hash for us, and it needs no wrapper library.
"""

import re

import bcrypt

# Cost factor. Each increment doubles the work required to compute a hash.
BCRYPT_ROUNDS = 12

# bcrypt only considers the first 72 bytes of a password.
_MAX_PASSWORD_BYTES = 72

# $<variant>$<cost>$<22-char salt><31-char digest> — always 60 characters.
_BCRYPT_HASH_RE = re.compile(r"^\$2[abxy]\$\d{2}\$[./A-Za-z0-9]{53}$")


def _encode(password: str) -> bytes:
    """Encode a password for bcrypt, truncated to the bytes bcrypt reads."""
    return password.encode("utf-8")[:_MAX_PASSWORD_BYTES]


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password with a freshly generated salt."""
    hashed = bcrypt.hashpw(_encode(plain_password), bcrypt.gensalt(rounds=BCRYPT_ROUNDS))
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash.

    Returns False rather than raising when the stored value is not a valid
    bcrypt hash, so callers can treat verification as a simple boolean. The
    shape of the stored hash is checked first: bcrypt's Rust extension panics
    on a malformed hash, and that panic is not a normal Python exception.
    """
    if not _BCRYPT_HASH_RE.match(hashed_password or ""):
        return False

    try:
        return bcrypt.checkpw(_encode(plain_password), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False
