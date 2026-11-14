import pytest

from src.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    password = "Campus2026"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("Wrong2026", hashed)


def test_password_rejects_over_length():
    with pytest.raises(ValueError):
        hash_password("a" * 80)


def test_access_token_roundtrip():
    token, expires_at = create_access_token(42, "jti-demo")
    payload = decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["jti"] == "jti-demo"
    assert expires_at is not None
