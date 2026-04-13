"""Supabase JWT verification with optional legacy signing secret."""

from __future__ import annotations

from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from webapp.security import decode_supabase_jwt


def test_decode_uses_primary_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "primary_only")
    monkeypatch.delenv("SUPABASE_JWT_SECRET_LEGACY", raising=False)
    token = jwt.encode({"sub": "u1"}, "primary_only", algorithm="HS256")
    assert decode_supabase_jwt(token)["sub"] == "u1"


def test_decode_falls_back_to_legacy_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "new_secret")
    monkeypatch.setenv("SUPABASE_JWT_SECRET_LEGACY", "old_secret")
    token = jwt.encode({"sub": "u_legacy"}, "old_secret", algorithm="HS256")
    assert decode_supabase_jwt(token)["sub"] == "u_legacy"


def test_decode_primary_preferred_when_both_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same symmetric key used twice is deduped; one successful decode is enough."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "shared")
    monkeypatch.setenv("SUPABASE_JWT_SECRET_LEGACY", "shared")
    token = jwt.encode({"sub": "u2"}, "shared", algorithm="HS256")
    assert decode_supabase_jwt(token)["sub"] == "u2"


def test_decode_fails_when_neither_secret_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "a")
    monkeypatch.setenv("SUPABASE_JWT_SECRET_LEGACY", "b")
    token = jwt.encode({"sub": "x"}, "other", algorithm="HS256")
    with pytest.raises(HTTPException) as ei:
        decode_supabase_jwt(token)
    assert ei.value.status_code == 401


def test_decode_rejects_unsupported_symmetric_alg(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "secret_at_least_32_bytes_long_ok!!"
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    token = jwt.encode({"sub": "x"}, secret, algorithm="HS512")
    with pytest.raises(HTTPException) as ei:
        decode_supabase_jwt(token)
    assert ei.value.status_code == 401
    assert "Unsupported JWT algorithm" in str(ei.value.detail)


def test_decode_es256_via_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    """New Supabase projects may sign access tokens with ES256; verify via JWKS URL + public key."""
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "symmetric_fallback_secret")
    priv = ec.generate_private_key(ec.SECP256R1(), default_backend())
    pub = priv.public_key()
    token = jwt.encode({"sub": "es_user"}, priv, algorithm="ES256")

    mock_jwks = MagicMock()
    sk = MagicMock()
    sk.key = pub
    mock_jwks.get_signing_key_from_jwt.return_value = sk
    monkeypatch.setattr("webapp.security._jwks_client_for", lambda _url: mock_jwks)

    assert decode_supabase_jwt(token)["sub"] == "es_user"


def test_decode_es256_via_jwks_without_symmetric_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """ES256 path must not require SUPABASE_JWT_SECRET (hosted Supabase often uses asymmetric-only)."""
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET_LEGACY", raising=False)
    priv = ec.generate_private_key(ec.SECP256R1(), default_backend())
    pub = priv.public_key()
    token = jwt.encode({"sub": "es_only"}, priv, algorithm="ES256")

    mock_jwks = MagicMock()
    sk = MagicMock()
    sk.key = pub
    mock_jwks.get_signing_key_from_jwt.return_value = sk
    monkeypatch.setattr("webapp.security._jwks_client_for", lambda _url: mock_jwks)

    assert decode_supabase_jwt(token)["sub"] == "es_only"


def test_hs256_requires_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET_LEGACY", raising=False)
    token = jwt.encode({"sub": "x"}, "any_secret", algorithm="HS256")
    with pytest.raises(HTTPException) as ei:
        decode_supabase_jwt(token)
    assert ei.value.status_code == 503
    assert "SUPABASE_JWT_SECRET" in str(ei.value.detail)
