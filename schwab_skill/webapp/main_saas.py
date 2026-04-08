from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from celery.result import AsyncResult
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from execution import get_account_status

from .db import Base, SessionLocal, engine
from .models import AppState, Order, Position, ScanResult, User, UserCredential
from .schemas import ApiResponse, ExecuteOrderRequest, SchwabCredentialUpsert
from .security import (
    encrypt_secret,
    get_current_user,
    parse_json,
    parse_scopes,
    parse_token_expiry,
    utcnow_iso,
)
from .tasks import celery_app, execute_order_for_user, scan_for_user

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

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
    allow_headers=["Content-Type", "Authorization"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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
    cred = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
    if not cred:
        return False
    return bool(cred.access_token_enc and cred.refresh_token_enc)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", response_model=ApiResponse)
def health() -> ApiResponse:
    return _ok(
        {
            "status": "ok",
            "time": datetime.now(timezone.utc).isoformat(),
            "auth_mode": "jwt",
            "queue_backend": celery_app.conf.result_backend,
        }
    )


@app.get("/api/me", response_model=ApiResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(_db)) -> ApiResponse:
    linked = _is_schwab_linked(db, user.id)
    return _ok(
        {
            "id": user.id,
            "email": user.email,
            "provider": user.auth_provider,
            "schwab_linked": linked,
            "onboarding_required": not linked,
        }
    )


@app.post("/api/credentials/schwab", response_model=ApiResponse)
def upsert_schwab_credentials(
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
    return _ok({"schwab_linked": True, "updated_at": row.updated_at.isoformat() if row.updated_at else None})


@app.get("/api/credentials/status", response_model=ApiResponse)
def credential_status(user: User = Depends(get_current_user), db: Session = Depends(_db)) -> ApiResponse:
    row = db.query(UserCredential).filter(UserCredential.user_id == user.id).first()
    linked = _is_schwab_linked(db, user.id)
    expires_at = row.expires_at.isoformat() if row and row.expires_at else None
    return _ok({"schwab_linked": linked, "expires_at": expires_at, "onboarding_required": not linked})


@app.get("/api/onboarding/status", response_model=ApiResponse)
def onboarding_status(user: User = Depends(get_current_user), db: Session = Depends(_db)) -> ApiResponse:
    linked = _is_schwab_linked(db, user.id)
    state = _load_state(
        db,
        user.id,
        "onboarding",
        default={
            "linked_at": None,
            "schwab_linked": linked,
            "wizard_required": not linked,
        },
    )
    state["schwab_linked"] = linked
    state["onboarding_required"] = not linked
    return _ok(state)


@app.post("/api/scan", response_model=ApiResponse)
def run_scan(user: User = Depends(get_current_user), db: Session = Depends(_db)) -> ApiResponse:
    if not _is_schwab_linked(db, user.id):
        raise HTTPException(status_code=409, detail="Link Schwab account before running scans.")
    task = scan_for_user.delay(user.id)
    return _ok({"task_id": task.id, "status": "queued"})


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
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    rows = (
        db.query(ScanResult)
        .filter(ScanResult.user_id == user.id)
        .order_by(ScanResult.created_at.desc())
        .limit(limit)
        .all()
    )
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


@app.post("/api/orders/execute", response_model=ApiResponse)
def execute_order(
    payload: ExecuteOrderRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    if not _is_schwab_linked(db, user.id):
        raise HTTPException(status_code=409, detail="Link Schwab account before executing orders.")
    task = execute_order_for_user.delay(
        user.id,
        payload.ticker,
        payload.qty,
        payload.side,
        payload.order_type,
        payload.price,
    )
    return _ok({"task_id": task.id, "status": "queued"})


@app.get("/api/orders/{task_id}", response_model=ApiResponse)
def order_task_status(task_id: str, user: User = Depends(get_current_user), db: Session = Depends(_db)) -> ApiResponse:
    task = AsyncResult(task_id, app=celery_app)
    rows = (
        db.query(Order)
        .filter(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(25)
        .all()
    )
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
    rows = (
        db.query(Order)
        .filter(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(limit)
        .all()
    )
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
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    # For production, use per-user broker tokens from encrypted vault.
    # Existing execution module still expects local session files; we preserve behavior while storing per-user snapshots.
    status_data = get_account_status(skill_dir=APP_DIR.parent)
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
