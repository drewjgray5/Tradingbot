from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from typing import Any

import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, Header, HTTPException, Request
from jwt import PyJWKClient
from sqlalchemy.orm import Session

from .billing_stripe import user_has_paid_entitlement
from .db import SessionLocal
from .models import User


def _is_production_like() -> bool:
    env = (os.getenv("ENV") or os.getenv("APP_ENV") or "").strip().lower()
    if env in ("prod", "production", "staging"):
        return True
    if (os.getenv("RENDER") or "").strip():
        return True
    return False


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _load_encryption_key() -> bytes:
    raw = (os.getenv("CREDENTIAL_ENCRYPTION_KEY") or "").strip()
    if not raw:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is required for encrypted credential storage.")
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("utf-8"))
    except Exception as exc:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must be urlsafe base64 for a 32-byte key.") from exc
    if len(decoded) != 32:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must decode to exactly 32 bytes (AES-256).")
    return decoded


def encrypt_secret(plaintext: str) -> str:
    key = _load_encryption_key()
    aes = AESGCM(key)
    nonce = os.urandom(12)
    encrypted = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    payload = nonce + encrypted
    return base64.urlsafe_b64encode(payload).decode("utf-8")


def decrypt_secret(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    key = _load_encryption_key()
    aes = AESGCM(key)
    packed = base64.urlsafe_b64decode(ciphertext.encode("utf-8"))
    nonce = packed[:12]
    blob = packed[12:]
    out = aes.decrypt(nonce, blob, None)
    return out.decode("utf-8")


def _jwt_secrets_for_decode() -> list[str]:
    """Primary JWT secret first, then optional legacy (Supabase secret rotation / migration).

    May be empty when the host relies only on asymmetric (JWKS) verification — see decode_supabase_jwt.
    """
    primary = (os.getenv("SUPABASE_JWT_SECRET") or "").strip()
    if not primary:
        return []
    out: list[str] = [primary]
    legacy = (os.getenv("SUPABASE_JWT_SECRET_LEGACY") or "").strip()
    if legacy and legacy not in out:
        out.append(legacy)
    return out


def _jwt_leeway_seconds() -> int:
    raw = (os.getenv("SUPABASE_JWT_LEEWAY_SECONDS") or "120").strip()
    try:
        return max(0, min(int(raw), 86_400))
    except ValueError:
        return 120


def _jwt_verify_audience() -> str | None:
    """If set, jwt.decode verifies the aud claim (Supabase user access tokens often use 'authenticated')."""
    aud = (os.getenv("SUPABASE_JWT_AUDIENCE") or "").strip()
    return aud or None


def _jwt_verify_issuer() -> str | None:
    """If set, jwt.decode verifies iss (e.g. https://<ref>.supabase.co/auth/v1)."""
    iss = (os.getenv("SUPABASE_JWT_ISSUER") or "").strip()
    return iss or None


def _enforce_production_claim_config(aud: str | None, iss: str | None) -> None:
    strict_raw = (os.getenv("SUPABASE_JWT_STRICT_CLAIMS") or "").strip().lower()
    strict = strict_raw in ("1", "true", "yes", "on") or (strict_raw == "" and _is_production_like())
    if strict and (not aud or not iss):
        raise HTTPException(
            status_code=503,
            detail="Set SUPABASE_JWT_AUDIENCE and SUPABASE_JWT_ISSUER for production JWT validation.",
        )


def _supabase_jwks_url() -> str:
    base = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    if not base:
        raise HTTPException(
            status_code=503,
            detail=(
                "SUPABASE_URL is not configured; it is required to verify asymmetric "
                "(ES256/RS256) Supabase JWTs via JWKS."
            ),
        )
    return f"{base}/auth/v1/.well-known/jwks.json"


# One client per JWKS URL; PyJWKClient caches keys internally.
_jwks_clients: dict[str, PyJWKClient] = {}


def _jwks_client_for(url: str) -> PyJWKClient:
    client = _jwks_clients.get(url)
    if client is None:
        client = PyJWKClient(url, cache_keys=True)
        _jwks_clients[url] = client
    return client


_ASYMMETRIC_ALGS = frozenset({"ES256", "ES384", "RS256", "RS384", "RS512"})


def _decode_supabase_jwt_symmetric(token: str) -> dict[str, Any]:
    secrets = _jwt_secrets_for_decode()
    if not secrets:
        raise HTTPException(
            status_code=503,
            detail=(
                "SUPABASE_JWT_SECRET is not configured. "
                "In Supabase: Project Settings → API → copy JWT Secret into SUPABASE_JWT_SECRET on your host. "
                "Asymmetric access tokens (ES256) only need SUPABASE_URL for JWKS; HS256 tokens always need the JWT secret."
            ),
        )
    aud = _jwt_verify_audience()
    iss = _jwt_verify_issuer()
    _enforce_production_claim_config(aud, iss)
    leeway = _jwt_leeway_seconds()
    opts: dict[str, Any] = {"verify_aud": bool(aud)}
    last_exc: jwt.PyJWTError | None = None
    for secret in secrets:
        try:
            kwargs: dict[str, Any] = {
                "algorithms": ["HS256"],
                "options": opts,
                "leeway": leeway,
            }
            if aud:
                kwargs["audience"] = aud
            if iss:
                kwargs["issuer"] = iss
            return jwt.decode(token, secret, **kwargs)
        except jwt.PyJWTError as exc:
            last_exc = exc
            continue
    assert last_exc is not None
    msg = str(last_exc).lower()
    if "not enough segments" in msg or "segments" in msg:
        detail = (
            "Invalid JWT: expected a Supabase access token (three dot-separated parts), "
            "not a refresh token or API key."
        )
    else:
        detail = "Invalid JWT."
    raise HTTPException(status_code=401, detail=detail) from last_exc


def _decode_supabase_jwt_asymmetric(token: str, alg: str) -> dict[str, Any]:
    jwks_url = _supabase_jwks_url()
    aud = _jwt_verify_audience()
    iss = _jwt_verify_issuer()
    _enforce_production_claim_config(aud, iss)
    leeway = _jwt_leeway_seconds()
    opts: dict[str, Any] = {"verify_aud": bool(aud)}
    try:
        jwks = _jwks_client_for(jwks_url)
        signing_key = jwks.get_signing_key_from_jwt(token)
        kwargs: dict[str, Any] = {
            "algorithms": [alg],
            "options": opts,
            "leeway": leeway,
        }
        if aud:
            kwargs["audience"] = aud
        if iss:
            kwargs["issuer"] = iss
        return jwt.decode(token, signing_key.key, **kwargs)
    except HTTPException:
        raise
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid JWT.") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not verify JWT against Supabase JWKS. Confirm SUPABASE_URL and outbound internet access."
            ),
        ) from exc


def decode_supabase_jwt(token: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        msg = str(exc).lower()
        if "not enough segments" in msg or "segments" in msg:
            detail = (
                "Invalid JWT: expected a Supabase access token (three dot-separated parts), "
                "not a refresh token or API key."
            )
        else:
            detail = "Invalid JWT."
        raise HTTPException(status_code=401, detail=detail) from exc

    alg = str(header.get("alg") or "").upper()
    if alg in _ASYMMETRIC_ALGS:
        return _decode_supabase_jwt_asymmetric(token, alg)
    if alg == "HS256" or not alg:
        return _decode_supabase_jwt_symmetric(token)
    raise HTTPException(
        status_code=401,
        detail=f"Unsupported JWT algorithm {alg!r}; expected HS256 or Supabase asymmetric (e.g. ES256).",
    )


def auth_session_cookie_name() -> str:
    name = (os.getenv("AUTH_SESSION_COOKIE_NAME") or "tradingbot_session").strip()
    return name or "tradingbot_session"


def _get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(_get_db),
) -> User:
    tokens: list[str] = []
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
        if bearer:
            tokens.append(bearer)
    cookie_token = (request.cookies.get(auth_session_cookie_name()) or "").strip()
    if cookie_token:
        tokens.append(cookie_token)
    if not tokens:
        raise HTTPException(
            status_code=401,
            detail="Missing authentication. Provide Authorization: Bearer <jwt> or a valid auth session cookie.",
        )

    last_error: HTTPException | None = None
    claims: dict[str, Any] | None = None
    for token in tokens:
        try:
            claims = decode_supabase_jwt(token)
            break
        except HTTPException as exc:
            last_error = exc
            continue
    if claims is None:
        if last_error is not None:
            raise last_error
        raise HTTPException(status_code=401, detail="Invalid JWT.")

    user_id = str(claims.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="JWT missing subject claim.")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(
            id=user_id,
            email=claims.get("email"),
            auth_provider="supabase",
            live_execution_enabled=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        maybe_email = claims.get("email")
        if maybe_email and maybe_email != user.email:
            user.email = maybe_email
            db.commit()
            db.refresh(user)
    return user


def require_paid_entitlement(user: User = Depends(get_current_user)) -> User:
    if not user_has_paid_entitlement(user):
        raise HTTPException(
            status_code=402,
            detail=(
                "Active subscription required. "
                "Subscribe via POST /api/billing/checkout-session or manage billing via POST /api/billing/portal-session."
            ),
        )
    return user


def parse_json(raw: str | None, fallback: dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]:
    if not raw:
        return fallback
    try:
        out = json.loads(raw)
        if isinstance(fallback, list):
            return out if isinstance(out, list) else fallback
        return out if isinstance(out, dict) else fallback
    except Exception:
        return fallback


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_scopes(scopes: list[str] | None) -> str | None:
    if not scopes:
        return None
    cleaned = [scope.strip() for scope in scopes if scope and scope.strip()]
    if not cleaned:
        return None
    return ",".join(cleaned)


def parse_token_expiry(expires_at: str | None) -> datetime | None:
    return _parse_datetime(expires_at)
