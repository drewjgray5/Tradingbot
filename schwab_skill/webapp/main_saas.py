from __future__ import annotations

import copy
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import stripe
from celery.result import AsyncResult
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from execution import get_account_status

from .audit import log_audit
from .backtest_queue import create_and_queue_backtest
from .billing_stripe import (
    billing_enforcement_enabled,
    create_billing_portal_session,
    create_subscription_checkout_session,
    handle_stripe_event,
    stripe_event_id,
    stripe_event_type,
    try_claim_stripe_webhook_event,
    user_has_paid_entitlement,
)
from .db import DATABASE_URL, Base, SessionLocal, engine
from .models import AppState, BacktestRun, Order, Position, ScanResult, User, UserCredential
from .saas_redis import acquire_scan_cooldown, fixed_window_rate_limit, redis_ping
from .scan_payload import parse_scan_run_body
from .prometheus_metrics import render_prometheus_text
from .schemas import (
    ApiResponse,
    BillingCheckoutPayload,
    EnableLiveTradingRequest,
    QueueUserBacktestRequest,
    SchwabCredentialUpsert,
    StrategyChatRequest,
    UpdateTradingHaltRequest,
)
from .security import (
    auth_session_cookie_name,
    decode_supabase_jwt,
    encrypt_secret,
    get_current_user,
    parse_json,
    parse_scopes,
    parse_token_expiry,
    require_paid_entitlement,
    utcnow_iso,
)
from .strategy_chat import run_strategy_chat
from .tasks import celery_app, scan_for_user
from .tenant_dashboard import _tenant_api_health_snapshot
from .tenant_dashboard import router as tenant_dashboard_router
from .tenant_runtime import (
    tenant_skill_dir,
    user_can_materialize_for_scan,
    user_has_account_session,
    user_schwab_ready_for_live_trading,
)

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
_ALEMBIC_INI = APP_DIR.parent / "alembic.ini"

if os.getenv("SAAS_BOOTSTRAP_SCHEMA", "").lower() in ("1", "true", "yes"):
    Base.metadata.create_all(bind=engine)
    if _ALEMBIC_INI.is_file():
        from alembic.config import Config

        from alembic import command

        command.stamp(Config(str(_ALEMBIC_INI)), "saas006")
# Production DB migrations: `docker-entrypoint-web.sh` when SAAS_RUN_ALEMBIC is set (not at import).
elif DATABASE_URL.startswith("sqlite"):
    Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="TradingBot SaaS API",
    version="1.0.0",
    description="Multi-tenant TradingBot API with JWT auth, encrypted credentials, and async workers.",
)

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "WEB_ALLOWED_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5173,http://localhost:5173",
    ).split(",")
    if origin.strip()
]
if not allowed_origins:
    allowed_origins = ["http://127.0.0.1:8000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID", "Idempotency-Key"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(tenant_dashboard_router)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next: Any) -> Any:
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = rid
    t0 = time.perf_counter()
    ctx_token = None
    try:
        from logger_setup import request_id_var

        ctx_token = request_id_var.set(rid)
    except Exception:
        pass
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        try:
            from .prometheus_metrics import inc, observe

            inc("http_requests_total")
            observe("http_request_duration", time.perf_counter() - t0)
        except Exception:
            pass
        return response
    finally:
        if ctx_token is not None:
            try:
                from logger_setup import request_id_var

                request_id_var.reset(ctx_token)
            except Exception:
                pass


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err(message: str, data: Any = None) -> ApiResponse:
    return ApiResponse(ok=False, error=message, data=data)


def _db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _auth_cookie_secure() -> bool:
    """Default to secure cookies outside local dev."""
    raw = (os.getenv("AUTH_SESSION_COOKIE_SECURE") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    env = (os.getenv("ENV") or os.getenv("APP_ENV") or "").strip().lower()
    return env not in ("", "dev", "development", "local")


def _set_auth_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=auth_session_cookie_name(),
        value=token,
        httponly=True,
        secure=_auth_cookie_secure(),
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days; token exp still governs auth validity
        path="/",
    )


def _clear_auth_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=auth_session_cookie_name(),
        path="/",
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _save_state(db: Session, user_id: str, key: str, payload: dict[str, Any]) -> None:
    row = db.query(AppState).filter(AppState.user_id == user_id, AppState.key == key).first()
    if not row:
        row = AppState(user_id=user_id, key=key, value_json=json.dumps(payload, default=_json_default))
        db.add(row)
    else:
        row.value_json = json.dumps(payload, default=_json_default)
    db.commit()


def _load_state(db: Session, user_id: str, key: str, default: dict[str, Any]) -> dict[str, Any]:
    row = db.query(AppState).filter(AppState.user_id == user_id, AppState.key == key).first()
    if not row:
        return default
    parsed = parse_json(row.value_json, default)
    return parsed if isinstance(parsed, dict) else default


def _is_schwab_linked(db: Session, user_id: str) -> bool:
    return user_has_account_session(db, user_id)


def _scan_rate_limit(user_id: str) -> None:
    limit = int(os.getenv("SAAS_RATE_SCAN_PER_MIN", "12"))
    window = int(os.getenv("SAAS_RATE_LIMIT_WINDOW_SEC", "60"))
    ok, n = fixed_window_rate_limit(user_id, "scan", limit, window)
    if not ok:
        raise HTTPException(status_code=429, detail=f"Scan rate limit exceeded ({n}/{limit} per {window}s).")


def _scan_daily_limit_for_user(user: User) -> int:
    default = int(os.getenv("SAAS_SCAN_DAILY_LIMIT", "200"))
    trial = int(os.getenv("SAAS_SCAN_DAILY_LIMIT_TRIAL", "30"))
    status = (user.subscription_status or "").strip().lower()
    return trial if status == "trialing" else default


def _scan_daily_limit_check(user_id: str, user: User) -> tuple[int, int]:
    limit = _scan_daily_limit_for_user(user)
    if limit <= 0:
        return limit, 0
    ok, n = fixed_window_rate_limit(user_id, "scan_daily", limit, 86400)
    if not ok:
        raise HTTPException(
            status_code=429,
            detail=f"Daily scan quota exceeded ({n}/{limit} per rolling 24h window).",
        )
    return limit, n


def _celery_queue_estimate() -> dict[str, Any]:
    try:
        insp = celery_app.control.inspect(timeout=0.75)
        if not insp:
            return {"inspect_available": False}
        reserved = insp.reserved() or {}
        scheduled = insp.scheduled() or {}
        active = insp.active() or {}
        return {
            "inspect_available": True,
            "reserved_total": sum(len(v) for v in reserved.values()),
            "scheduled_total": sum(len(v) for v in scheduled.values()),
            "active_total": sum(len(v) for v in active.values()),
        }
    except Exception:
        return {"inspect_available": False}


def _order_rate_limit(user_id: str) -> None:
    limit = int(os.getenv("SAAS_RATE_ORDER_PER_MIN", "30"))
    window = int(os.getenv("SAAS_RATE_LIMIT_WINDOW_SEC", "60"))
    ok, n = fixed_window_rate_limit(user_id, "order", limit, window)
    if not ok:
        raise HTTPException(status_code=429, detail=f"Order rate limit exceeded ({n}/{limit} per {window}s).")


def _backtest_rate_limit(user_id: str) -> None:
    limit = int(os.getenv("SAAS_RATE_BACKTEST_PER_HOUR", "6"))
    window = 3600
    ok, n = fixed_window_rate_limit(user_id, "backtest", limit, window)
    if not ok:
        raise HTTPException(status_code=429, detail=f"Backtest rate limit exceeded ({n}/{limit} per hour).")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/simple")
def simple_dashboard() -> FileResponse:
    """Focused scan + diagnostics UI for external users (see also `/`)."""
    return FileResponse(STATIC_DIR / "simple.html")


@app.get("/login")
def login_page() -> FileResponse:
    """Focused sign-in (same JWT / Supabase session as the main dashboard)."""
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/api/public-config", response_model=ApiResponse)
def public_config() -> ApiResponse:
    """Non-secret client config (e.g. Supabase URL + anon key for browser sign-in)."""
    url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    anon = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
    supabase: dict[str, str] | None = None
    if url and anon:
        supabase = {"url": url, "anon_key": anon}
    schwab_oauth = bool(
        (os.getenv("SCHWAB_ACCOUNT_APP_KEY") or "").strip() and (os.getenv("SCHWAB_CALLBACK_URL") or "").strip()
    )
    schwab_market_oauth = bool(
        (os.getenv("SCHWAB_MARKET_APP_KEY") or "").strip()
        and (os.getenv("SCHWAB_MARKET_CALLBACK_URL") or "").strip()
    )
    plat_kill = (os.getenv("LIVE_TRADING_KILL_SWITCH") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    data: dict[str, Any] = {
        "supabase": supabase,
        "schwab_oauth": schwab_oauth,
        "schwab_market_oauth": schwab_market_oauth,
        "saas_mode": True,
        "platform_live_trading_kill_switch": plat_kill,
    }
    impl = (os.getenv("WEB_IMPLEMENTATION_GUIDE_URL") or "").strip()
    if impl.startswith(("http://", "https://")):
        data["implementation_guide_url"] = impl
    elif schwab_oauth or schwab_market_oauth:
        # Built-in end-user steps when the host does not set WEB_IMPLEMENTATION_GUIDE_URL
        data["implementation_guide_url"] = "/static/connect-schwab-guide.html"
    return _ok(data)


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics() -> PlainTextResponse:
    return PlainTextResponse(
        render_prometheus_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/api/health", response_model=ApiResponse)
def health() -> ApiResponse:
    url = (os.getenv("SUPABASE_URL") or "").strip()
    anon = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
    return _ok(
        {
            "status": "ok",
            "time": datetime.now(timezone.utc).isoformat(),
            "auth_mode": "jwt",
            "supabase_browser_auth": bool(url and anon),
            "queue_backend": celery_app.conf.result_backend,
        }
    )


@app.post("/api/auth/session", response_model=ApiResponse)
def auth_create_session(
    response: Response,
    payload: dict[str, Any] | None = Body(default=None),
) -> ApiResponse:
    """Exchange client JWT for HttpOnly cookie session (JWT remains validation source)."""
    body = payload or {}
    token = str(body.get("access_token") or body.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="access_token is required.")
    claims = decode_supabase_jwt(token)
    if not str(claims.get("sub") or "").strip():
        raise HTTPException(status_code=401, detail="JWT missing subject claim.")
    _set_auth_session_cookie(response, token)
    return _ok({"session_cookie_set": True})


@app.delete("/api/auth/session", response_model=ApiResponse)
def auth_destroy_session(response: Response) -> ApiResponse:
    _clear_auth_session_cookie(response)
    return _ok({"session_cookie_cleared": True})


@app.get("/api/auth/session", response_model=ApiResponse)
def auth_session_status(
    request: Request,
) -> ApiResponse:
    token = (request.cookies.get(auth_session_cookie_name()) or "").strip()
    if not token:
        return _ok({"authenticated": False})
    claims = decode_supabase_jwt(token)
    return _ok({"authenticated": True, "sub": claims.get("sub"), "email": claims.get("email")})


@app.get("/api/health/live", response_model=ApiResponse)
def health_live() -> ApiResponse:
    return _ok({"status": "live", "time": datetime.now(timezone.utc).isoformat()})


@app.get("/api/health/ready", response_model=ApiResponse)
def health_ready() -> ApiResponse:
    db_ok = False
    try:
        s = SessionLocal()
        try:
            s.execute(text("SELECT 1"))
            db_ok = True
        finally:
            s.close()
    except Exception:
        db_ok = False
    redis_ok = redis_ping()
    require_redis = os.getenv("SAAS_HEALTH_REQUIRE_REDIS", "1").lower() in ("1", "true", "yes")
    ready = db_ok and (redis_ok if require_redis else True)
    return _ok(
        {
            "status": "ready" if ready else "not_ready",
            "database": db_ok,
            "redis": redis_ok,
            "time": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.get("/api/me", response_model=ApiResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(_db)) -> ApiResponse:
    linked = _is_schwab_linked(db, user.id)
    period_end = user.subscription_current_period_end.isoformat() if user.subscription_current_period_end else None
    return _ok(
        {
            "id": user.id,
            "email": user.email,
            "provider": user.auth_provider,
            "schwab_linked": linked,
            "onboarding_required": not linked,
            "live_execution_enabled": bool(getattr(user, "live_execution_enabled", False)),
            "trading_halted": bool(getattr(user, "trading_halted", False)),
            "subscription_status": user.subscription_status,
            "subscription_current_period_end": period_end,
            "has_stripe_customer": bool(user.stripe_customer_id),
            "billing_enforced": billing_enforcement_enabled(),
            "subscription_active": user_has_paid_entitlement(user),
        }
    )


@app.post("/api/settings/enable-live-trading", response_model=ApiResponse)
def enable_live_trading(
    request: Request,
    payload: EnableLiveTradingRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    if not payload.risk_acknowledged:
        raise HTTPException(status_code=400, detail="risk_acknowledged must be true.")
    if (payload.typed_phrase or "").strip() != "ENABLE":
        raise HTTPException(
            status_code=400,
            detail="Type the word ENABLE exactly to confirm you understand live orders are irreversible at market.",
        )
    ready, reason = user_schwab_ready_for_live_trading(db, user.id)
    if not ready:
        raise HTTPException(status_code=409, detail=reason)
    row = db.query(User).filter(User.id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")
    if row.live_execution_enabled:
        return _ok({"live_execution_enabled": True, "already_enabled": True})
    row.live_execution_enabled = True
    db.commit()
    db.refresh(row)
    log_audit(
        db,
        action="live_execution_enabled",
        user_id=row.id,
        detail={"source": "settings_enable_live_trading"},
        request_id=_request_id(request),
    )
    return _ok({"live_execution_enabled": True})


@app.patch("/api/settings/trading-halt", response_model=ApiResponse)
def update_trading_halt(
    request: Request,
    payload: UpdateTradingHaltRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    row = db.query(User).filter(User.id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")
    row.trading_halted = bool(payload.halted)
    db.commit()
    db.refresh(row)
    log_audit(
        db,
        action="trading_halt_updated",
        user_id=row.id,
        detail={"halted": row.trading_halted},
        request_id=_request_id(request),
    )
    return _ok({"trading_halted": row.trading_halted})


@app.post("/api/billing/checkout-session", response_model=ApiResponse)
def billing_checkout_session(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
    payload: BillingCheckoutPayload = Body(),
) -> ApiResponse:
    body = payload
    success = str(body.success_url) if body.success_url else (os.getenv("STRIPE_CHECKOUT_SUCCESS_URL") or "").strip()
    cancel = str(body.cancel_url) if body.cancel_url else (os.getenv("STRIPE_CHECKOUT_CANCEL_URL") or "").strip()
    if not success or not cancel:
        raise HTTPException(
            status_code=503,
            detail="Set STRIPE_CHECKOUT_SUCCESS_URL and STRIPE_CHECKOUT_CANCEL_URL or pass success_url and cancel_url in the request body.",
        )
    try:
        url = create_subscription_checkout_session(user, success, cancel)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    log_audit(
        db,
        action="billing_checkout_session_created",
        user_id=user.id,
        detail={},
        request_id=_request_id(request),
    )
    return _ok({"url": url})


@app.post("/api/billing/portal-session", response_model=ApiResponse)
def billing_portal_session(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    return_url = (os.getenv("STRIPE_PORTAL_RETURN_URL") or "").strip()
    if not return_url:
        raise HTTPException(
            status_code=503,
            detail="STRIPE_PORTAL_RETURN_URL is not configured.",
        )
    if not user.stripe_customer_id:
        raise HTTPException(
            status_code=409,
            detail="No Stripe customer on file. Complete checkout first.",
        )
    try:
        url = create_billing_portal_session(user, return_url)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    log_audit(
        db,
        action="billing_portal_session_created",
        user_id=user.id,
        detail={},
        request_id=_request_id(request),
    )
    return _ok({"url": url})


@app.post("/api/billing/webhook/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(_db)) -> Response:
    wh_secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not wh_secret:
        raise HTTPException(status_code=503, detail="STRIPE_WEBHOOK_SECRET is not configured.")

    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    if not sig:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header.")

    try:
        event = stripe.Webhook.construct_event(payload, sig, wh_secret)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload.") from exc
    except stripe.error.SignatureVerificationError as exc:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature.") from exc

    eid = stripe_event_id(event)
    if not eid:
        raise HTTPException(status_code=400, detail="Event missing id.")

    if not try_claim_stripe_webhook_event(db, eid):
        return Response(status_code=200, content="duplicate")

    etype = stripe_event_type(event)
    try:
        handle_stripe_event(db, event)
        db.commit()
    except Exception:
        db.rollback()
        raise
    log_audit(
        db,
        action="billing_stripe_webhook",
        user_id=None,
        detail={"event_id": eid, "type": etype},
        request_id=_request_id(request),
    )
    return Response(status_code=200, content="ok")


@app.post("/api/credentials/schwab", response_model=ApiResponse)
def upsert_schwab_credentials(
    request: Request,
    payload: SchwabCredentialUpsert,
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    row = db.query(UserCredential).filter(UserCredential.user_id == user.id).first()
    if not row:
        row = UserCredential(user_id=user.id)
        db.add(row)

    if payload.access_token:
        row.access_token_enc = encrypt_secret(payload.access_token)
    if payload.refresh_token:
        row.refresh_token_enc = encrypt_secret(payload.refresh_token)
    if payload.token_type is not None:
        row.token_type = payload.token_type
    row.expires_at = parse_token_expiry(payload.expires_at)
    row.scopes = parse_scopes(payload.scopes)

    if payload.account_oauth_json and payload.account_oauth_json.strip():
        row.account_token_payload_enc = encrypt_secret(payload.account_oauth_json.strip())
    if payload.market_oauth_json and payload.market_oauth_json.strip():
        row.market_token_payload_enc = encrypt_secret(payload.market_oauth_json.strip())

    if payload.access_token and payload.refresh_token and not (payload.account_oauth_json or "").strip():
        row.account_token_payload_enc = encrypt_secret(
            json.dumps(
                {
                    "access_token": payload.access_token,
                    "refresh_token": payload.refresh_token,
                    "token_type": (payload.token_type or "Bearer").strip() or "Bearer",
                }
            )
        )

    db.commit()
    db.refresh(row)

    _save_state(
        db,
        user.id,
        "onboarding",
        {
            "linked_at": utcnow_iso(),
            "schwab_linked": True,
            "wizard_required": False,
        },
    )
    log_audit(
        db,
        action="credentials_schwab_upsert",
        user_id=user.id,
        detail={"has_market_blob": bool(row.market_token_payload_enc)},
        request_id=_request_id(request),
    )
    return _ok({"schwab_linked": True, "updated_at": row.updated_at.isoformat() if row.updated_at else None})


@app.get("/api/credentials/status", response_model=ApiResponse)
def credential_status(user: User = Depends(get_current_user), db: Session = Depends(_db)) -> ApiResponse:
    row = db.query(UserCredential).filter(UserCredential.user_id == user.id).first()
    linked = _is_schwab_linked(db, user.id)
    expires_at = row.expires_at.isoformat() if row and row.expires_at else None
    return _ok({"schwab_linked": linked, "expires_at": expires_at, "onboarding_required": not linked})


@app.get("/api/onboarding/status", response_model=ApiResponse)
def onboarding_status(user: User = Depends(get_current_user), db: Session = Depends(_db)) -> ApiResponse:
    """Match local `main.py` onboarding payload shape: steps/start/elapsed at top level (not only under `wizard`)."""
    linked = _is_schwab_linked(db, user.id)
    api_health = _tenant_api_health_snapshot(db, user.id)
    target_mins = 20
    wizard_default: dict[str, Any] = {
        "started_at": None,
        "target_minutes": target_mins,
        "steps": {
            "connect": {"ok": False},
            "verify_token_health": {"ok": False},
            "test_scan": {"ok": False},
            "test_paper_order": {"ok": False},
        },
    }
    wizard = _load_state(db, user.id, "onboarding_wizard", default=copy.deepcopy(wizard_default))
    if not isinstance(wizard.get("steps"), dict):
        wizard["steps"] = dict(wizard_default["steps"])
    meta = _load_state(
        db,
        user.id,
        "onboarding",
        default={
            "linked_at": None,
            "schwab_linked": linked,
            "wizard_required": not linked,
        },
    )
    started_at = wizard.get("started_at")
    elapsed_minutes = None
    if isinstance(started_at, str) and started_at:
        try:
            dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            elapsed_minutes = round((datetime.now(timezone.utc) - dt).total_seconds() / 60.0, 1)
        except Exception:
            elapsed_minutes = None
    _wsteps = wizard.get("steps")
    steps: dict[str, Any] = _wsteps if isinstance(_wsteps, dict) else {}
    completion = (
        bool((steps.get("connect") or {}).get("ok"))
        and bool((steps.get("verify_token_health") or {}).get("ok"))
        and bool((steps.get("test_scan") or {}).get("ok"))
        and bool((steps.get("test_paper_order") or {}).get("ok"))
    )
    tm = int(wizard.get("target_minutes") or target_mins)
    completed_under_target = bool(completion and elapsed_minutes is not None and elapsed_minutes <= tm)
    payload: dict[str, Any] = {
        **meta,
        "started_at": wizard.get("started_at"),
        "target_minutes": tm,
        "steps": steps,
        "elapsed_minutes": elapsed_minutes,
        "completed_under_target": completed_under_target,
        "schwab_linked": linked,
        "onboarding_required": not linked,
        "connection_status": "connected" if linked else "disconnected",
        "api_health": api_health,
        "wizard": wizard,
    }
    return _ok(payload)


@app.post("/api/scan", response_model=ApiResponse)
def run_scan(
    request: Request,
    user: User = Depends(require_paid_entitlement),
    db: Session = Depends(_db),
    body: dict[str, Any] | None = Body(default=None),
) -> ApiResponse:
    if not _is_schwab_linked(db, user.id):
        raise HTTPException(status_code=409, detail="Link Schwab account before running scans.")
    ok_scan, reason = user_can_materialize_for_scan(db, user.id)
    if not ok_scan:
        raise HTTPException(status_code=409, detail=reason)
    try:
        scan_opts = parse_scan_run_body(body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    _scan_rate_limit(user.id)
    _scan_daily_limit_check(user.id, user)
    cooldown = int(os.getenv("SAAS_SCAN_COOLDOWN_SEC", "60"))
    if not acquire_scan_cooldown(user.id, cooldown):
        raise HTTPException(
            status_code=409,
            detail=f"A scan was started recently; wait up to {cooldown}s before retrying.",
        )
    task = scan_for_user.apply_async(args=[user.id, scan_opts], queue="scan")
    log_audit(
        db,
        action="scan_queued",
        user_id=user.id,
        detail={
            "task_id": task.id,
            "scan_universe_mode": scan_opts.get("universe_mode"),
            "scan_custom_ticker_count": len(scan_opts.get("tickers") or []),
            "scan_has_strategy_overrides": bool(scan_opts.get("env_overrides")),
        },
        request_id=_request_id(request),
    )
    return _ok(
        {
            "task_id": task.id,
            "status": "queued",
            "worker_queue": _celery_queue_estimate(),
            "daily_scan_limit": _scan_daily_limit_for_user(user),
        }
    )


@app.get("/api/scan/{task_id}", response_model=ApiResponse)
def scan_task_status(
    task_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    task = AsyncResult(task_id, app=celery_app)
    payload: dict[str, Any] = {
        "task_id": task_id,
        "status": task.status.lower(),
        "worker_queue": _celery_queue_estimate(),
    }
    if task.ready():
        result = task.result if isinstance(task.result, dict) else {"raw_result": str(task.result)}
        payload["result"] = result
    recent = (
        db.query(ScanResult)
        .filter(ScanResult.user_id == user.id)
        .order_by(ScanResult.created_at.desc())
        .limit(25)
        .all()
    )
    payload["recent_results"] = [
        {
            "id": row.id,
            "job_id": row.job_id,
            "ticker": row.ticker,
            "signal_score": row.signal_score,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "payload": parse_json(row.payload_json, {}),
        }
        for row in recent
    ]
    return _ok(payload)


@app.get("/api/scan-results", response_model=ApiResponse)
def list_scan_results(
    limit: int = Query(default=100, ge=1, le=500),
    job_id: str | None = Query(default=None, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    q = db.query(ScanResult).filter(ScanResult.user_id == user.id)
    jid = (job_id or "").strip()
    if jid:
        q = q.filter(ScanResult.job_id == jid)
    rows = q.order_by(ScanResult.created_at.desc()).limit(limit).all()
    return _ok(
        [
            {
                "id": row.id,
                "job_id": row.job_id,
                "ticker": row.ticker,
                "signal_score": row.signal_score,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "payload": parse_json(row.payload_json, {}),
            }
            for row in rows
        ]
    )


@app.post("/api/orders/execute")
def execute_order() -> None:
    """Removed: live orders must be staged (pending) then confirmed in-app with typed ticker."""
    raise HTTPException(
        status_code=410,
        detail={
            "message": "Direct order execution is disabled. Stage a trade with POST /api/pending-trades, then confirm with POST /api/trades/{id}/approve and a JSON body re-typing the ticker.",
            "pending_trades": "POST /api/pending-trades",
            "confirm": "POST /api/trades/{trade_id}/approve",
        },
    )


@app.get("/api/orders/{task_id}", response_model=ApiResponse)
def order_task_status(task_id: str, user: User = Depends(get_current_user), db: Session = Depends(_db)) -> ApiResponse:
    task = AsyncResult(task_id, app=celery_app)
    rows = db.query(Order).filter(Order.user_id == user.id).order_by(Order.created_at.desc()).limit(25).all()
    return _ok(
        {
            "task_id": task_id,
            "task_status": task.status.lower(),
            "task_result": task.result if task.ready() else None,
            "orders": [
                {
                    "id": row.id,
                    "ticker": row.ticker,
                    "qty": row.qty,
                    "side": row.side,
                    "order_type": row.order_type,
                    "status": row.status,
                    "broker_order_id": row.broker_order_id,
                    "result": parse_json(row.result_json, {}),
                    "error_message": row.error_message,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
        }
    )


@app.get("/api/orders", response_model=ApiResponse)
def list_orders(
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    rows = db.query(Order).filter(Order.user_id == user.id).order_by(Order.created_at.desc()).limit(limit).all()
    return _ok(
        [
            {
                "id": row.id,
                "ticker": row.ticker,
                "qty": row.qty,
                "side": row.side,
                "order_type": row.order_type,
                "status": row.status,
                "result": parse_json(row.result_json, {}),
                "error_message": row.error_message,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )


@app.get("/api/positions/sync", response_model=ApiResponse)
def sync_positions(
    user: User = Depends(require_paid_entitlement),
    db: Session = Depends(_db),
) -> ApiResponse:
    if not _is_schwab_linked(db, user.id):
        raise HTTPException(status_code=409, detail="Link Schwab account before syncing positions.")
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            status_data = get_account_status(skill_dir=skill_dir)
    except Exception as exc:
        return _err(str(exc))
    if isinstance(status_data, str):
        return _err(status_data)

    inserted = 0
    accounts = status_data.get("accounts", [])
    for acc in accounts:
        sec = acc.get("securitiesAccount", acc)
        for pos in sec.get("positions", []):
            inst = pos.get("instrument", {})
            symbol = str(inst.get("symbol") or "").upper()
            if not symbol:
                continue
            qty = float(pos.get("longQuantity", 0) or pos.get("shortQuantity", 0) or 0)
            row = Position(
                user_id=user.id,
                symbol=symbol,
                qty=qty,
                avg_cost=float(pos.get("averagePrice", 0) or 0),
                market_value=float(pos.get("marketValue", 0) or 0),
            )
            db.add(row)
            inserted += 1
    db.commit()
    return _ok({"synced_positions": inserted})


@app.get("/api/positions", response_model=ApiResponse)
def list_positions(
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    rows = (
        db.query(Position)
        .filter(Position.user_id == user.id)
        .order_by(Position.as_of.desc(), Position.id.desc())
        .limit(limit)
        .all()
    )
    return _ok(
        [
            {
                "id": row.id,
                "symbol": row.symbol,
                "qty": row.qty,
                "avg_cost": row.avg_cost,
                "market_value": row.market_value,
                "as_of": row.as_of.isoformat() if row.as_of else None,
            }
            for row in rows
        ]
    )


@app.post("/api/backtest-runs", response_model=ApiResponse)
def queue_backtest_run(
    request: Request,
    payload: QueueUserBacktestRequest,
    user: User = Depends(require_paid_entitlement),
    db: Session = Depends(_db),
) -> ApiResponse:
    if not _is_schwab_linked(db, user.id):
        raise HTTPException(status_code=409, detail="Link Schwab account before running backtests.")
    ok_scan, reason = user_can_materialize_for_scan(db, user.id)
    if not ok_scan:
        raise HTTPException(status_code=409, detail=reason)
    _backtest_rate_limit(user.id)
    try:
        out = create_and_queue_backtest(db, user.id, payload.spec)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_audit(
        db,
        action="backtest_queued",
        user_id=user.id,
        detail={"task_id": out.get("task_id"), "run_id": out.get("run_id")},
        request_id=_request_id(request),
    )
    return _ok(out)


def _backtest_result_summary(result: Any) -> dict[str, Any] | None:
    """Short metrics for list UI; full JSON remains on the run row."""
    if not isinstance(result, dict) or "total_trades" not in result:
        return None
    out: dict[str, Any] = {
        "total_trades": result.get("total_trades"),
        "win_rate_net": result.get("win_rate_net"),
        "total_return_net_pct": result.get("total_return_net_pct"),
        "cagr_net_pct": result.get("cagr_net_pct"),
        "max_drawdown_net_pct": result.get("max_drawdown_net_pct"),
    }
    findings = result.get("findings")
    if isinstance(findings, str) and findings.strip():
        snippet = findings.strip()
        out["findings_preview"] = snippet[:280] + ("…" if len(snippet) > 280 else "")
    return out


@app.get("/api/backtest-runs", response_model=ApiResponse)
def list_backtest_runs(
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    rows = (
        db.query(BacktestRun)
        .filter(BacktestRun.user_id == user.id)
        .order_by(BacktestRun.created_at.desc())
        .limit(limit)
        .all()
    )
    payload: list[dict[str, Any]] = []
    for row in rows:
        parsed_result = parse_json(row.result_json, {}) if row.result_json else {}
        item: dict[str, Any] = {
            "id": row.id,
            "celery_task_id": row.celery_task_id,
            "status": row.status,
            "spec": parse_json(row.spec_json, {}),
            "error_message": row.error_message,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "has_result": bool(row.result_json),
        }
        summary = _backtest_result_summary(parsed_result)
        if summary is not None:
            item["result_summary"] = summary
        payload.append(item)
    return _ok(payload)


@app.get("/api/backtest-runs/tasks/{task_id}", response_model=ApiResponse)
def backtest_run_task_status(
    task_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    row = db.query(BacktestRun).filter(BacktestRun.user_id == user.id, BacktestRun.celery_task_id == task_id).first()
    task = AsyncResult(task_id, app=celery_app)
    payload: dict[str, Any] = {"task_id": task_id, "celery_status": task.status.lower()}
    if row:
        payload["run_id"] = row.id
        payload["db_status"] = row.status
        payload["error_message"] = row.error_message
        if row.result_json:
            payload["result"] = parse_json(row.result_json, {})
    if task.ready():
        tr = task.result
        payload["task_result"] = tr if isinstance(tr, dict) else {"raw_result": str(tr)}
    return _ok(payload)


@app.post("/api/strategy-chat", response_model=ApiResponse)
def strategy_chat_endpoint(
    request: Request,
    payload: StrategyChatRequest,
    user: User = Depends(require_paid_entitlement),
    db: Session = Depends(_db),
) -> ApiResponse:
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("MIROFISH_API_KEY") or "").strip():
        raise HTTPException(
            status_code=503,
            detail="Strategy chat requires OPENAI_API_KEY or MIROFISH_API_KEY.",
        )
    if not _is_schwab_linked(db, user.id):
        raise HTTPException(status_code=409, detail="Link Schwab account before using strategy chat.")
    ok_scan, reason = user_can_materialize_for_scan(db, user.id)
    if not ok_scan:
        raise HTTPException(status_code=409, detail=reason)
    _backtest_rate_limit(user.id)
    try:
        out = run_strategy_chat(db, user.id, payload.messages)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        return _err(str(exc))
    log_audit(
        db,
        action="strategy_chat",
        user_id=user.id,
        detail={"model": out.get("model")},
        request_id=_request_id(request),
    )
    return _ok(out)
