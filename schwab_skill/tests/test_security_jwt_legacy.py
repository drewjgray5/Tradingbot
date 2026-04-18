"""Supabase JWT verification with optional legacy signing secret."""

from __future__ import annotations

from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from webapp.db import Base
from webapp.models import User
from webapp.security import _jwt_secrets_for_decode, decode_supabase_jwt, get_current_user


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


def test_jwt_secret_decode_order_keeps_primary_then_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "new_secret")
    monkeypatch.setenv("SUPABASE_JWT_SECRET_LEGACY", "old_secret")
    assert _jwt_secrets_for_decode() == ["new_secret", "old_secret"]


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


def test_production_requires_audience_and_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "prod_secret")
    monkeypatch.delenv("SUPABASE_JWT_AUDIENCE", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_ISSUER", raising=False)
    token = jwt.encode({"sub": "prod_user"}, "prod_secret", algorithm="HS256")
    with pytest.raises(HTTPException) as ei:
        decode_supabase_jwt(token)
    assert ei.value.status_code == 503
    assert "SUPABASE_JWT_AUDIENCE" in str(ei.value.detail)


def test_decode_rejects_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "exp_secret")
    monkeypatch.setenv("SUPABASE_JWT_LEEWAY_SECONDS", "0")
    token = jwt.encode({"sub": "u_exp", "exp": 1}, "exp_secret", algorithm="HS256")
    with pytest.raises(HTTPException) as ei:
        decode_supabase_jwt(token)
    assert ei.value.status_code == 401


def test_decode_validates_audience_and_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "claims_secret")
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", "authenticated")
    monkeypatch.setenv("SUPABASE_JWT_ISSUER", "https://proj.supabase.co/auth/v1")
    good = jwt.encode(
        {"sub": "u_claims", "aud": "authenticated", "iss": "https://proj.supabase.co/auth/v1"},
        "claims_secret",
        algorithm="HS256",
    )
    bad_aud = jwt.encode(
        {"sub": "u_claims", "aud": "anon", "iss": "https://proj.supabase.co/auth/v1"},
        "claims_secret",
        algorithm="HS256",
    )
    assert decode_supabase_jwt(good)["sub"] == "u_claims"
    with pytest.raises(HTTPException) as ei:
        decode_supabase_jwt(bad_aud)
    assert ei.value.status_code == 401


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return factory()


def _request_with_cookie(cookie_header: str | None) -> Request:
    headers = []
    if cookie_header:
        headers.append((b"cookie", cookie_header.encode("utf-8")))
    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers}
    return Request(scope)


def test_get_current_user_prefers_authorization_then_cookie(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(User(id="u_bearer", email="bearer@example.com", auth_provider="supabase"))
    db_session.add(User(id="u_cookie", email="cookie@example.com", auth_provider="supabase"))
    db_session.commit()

    def _decode(token: str) -> dict[str, str]:
        if token == "bearer-token":
            return {"sub": "u_bearer"}
        if token == "cookie-token":
            return {"sub": "u_cookie"}
        raise HTTPException(status_code=401, detail="invalid")

    monkeypatch.setattr("webapp.security.decode_supabase_jwt", _decode)
    req = _request_with_cookie("tradingbot_session=cookie-token")
    user = get_current_user(req, authorization="Bearer bearer-token", db=db_session)
    assert user.id == "u_bearer"


def test_get_current_user_falls_back_to_cookie_when_authorization_invalid(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(User(id="u_cookie_only", email="cookie@example.com", auth_provider="supabase"))
    db_session.commit()

    def _decode(token: str) -> dict[str, str]:
        if token == "bad-bearer":
            raise HTTPException(status_code=401, detail="bad bearer")
        if token == "cookie-token":
            return {"sub": "u_cookie_only"}
        raise HTTPException(status_code=401, detail="invalid")

    monkeypatch.setattr("webapp.security.decode_supabase_jwt", _decode)
    req = _request_with_cookie("tradingbot_session=cookie-token")
    user = get_current_user(req, authorization="Bearer bad-bearer", db=db_session)
    assert user.id == "u_cookie_only"
