from datetime import timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.tokens import create_access_token, decode_access_token

SUBJECT = 42


def test_created_token_can_be_decoded():
    payload = decode_access_token(create_access_token(SUBJECT))

    assert set(payload) >= {"sub", "exp", "iat"}


def test_decoded_subject_matches_original():
    payload = decode_access_token(create_access_token(SUBJECT))

    assert payload["sub"] == "42"


def test_expiry_reflects_configured_lifetime():
    payload = decode_access_token(create_access_token(SUBJECT))
    lifetime = payload["exp"] - payload["iat"]

    assert lifetime == get_settings().access_token_expire_minutes * 60


def test_expired_token_is_rejected():
    token = create_access_token(SUBJECT, expires_delta=timedelta(seconds=-1))

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)


def test_tampered_payload_is_rejected():
    header, payload, signature = create_access_token(SUBJECT).split(".")
    forged = create_access_token(999).split(".")[1]
    token = f"{header}.{forged}.{signature}"

    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(token)


def test_tampered_signature_is_rejected():
    token = create_access_token(SUBJECT)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(jwt.PyJWTError):
        decode_access_token(tampered)


def test_token_signed_with_another_secret_is_rejected():
    settings = get_settings()
    foreign = jwt.encode(
        {"sub": "42", "exp": 9999999999}, "another-secret", algorithm=settings.jwt_algorithm
    )

    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(foreign)


def test_unsigned_token_is_rejected():
    """A token using alg=none must never be accepted."""
    unsigned = jwt.encode({"sub": "42", "exp": 9999999999}, key=None, algorithm="none")

    with pytest.raises(jwt.PyJWTError):
        decode_access_token(unsigned)


def test_malformed_token_is_rejected():
    for token in ["", "not-a-token", "a.b.c"]:
        with pytest.raises(jwt.PyJWTError):
            decode_access_token(token)


def test_token_without_subject_is_rejected():
    settings = get_settings()
    token = jwt.encode(
        {"exp": 9999999999}, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )

    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_access_token(token)
