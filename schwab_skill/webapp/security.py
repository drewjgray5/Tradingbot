from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from typing import Any

import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .billing_stripe import user_has_paid_entitlement
from .db import SessionLocal
from .models import User


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


def _jwt_secret() -> str:
    secret = (os.getenv("SUPABASE_JWT_SECRET") or "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="SUPABASE_JWT_SECRET is not configured.",
        )
    return secret


def decode_supabase_jwt(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            _jwt_secret(),
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid JWT: {exc}") from exc


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
    authorization: str | None = Header(default=None),
    request: Request | None = None,
    db: Session = Depends(_get_db),
) -> User:
    tokens: list[str] = []
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
        if bearer:
            tokens.append(bearer)
    if request is not None:
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
