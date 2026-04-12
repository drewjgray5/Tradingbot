"""
Tenant-scoped dashboard routes for SaaS (status, portfolio, pending trades, onboarding, OAuth).

Registered only from main_saas to avoid widening the local single-user attack surface.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as OrmSession

from execution import get_account_status, get_position_size_usd, place_order
from market_data import extract_schwab_last_price, get_current_quote, get_current_quote_with_status
from schwab_auth import DualSchwabAuth
from sector_strength import get_sector_heatmap
from signal_scanner import scan_for_signals_detailed

from .audit import log_audit
from .billing_stripe import user_has_paid_entitlement
from .checklist_language import with_plain_language
from .models import AppState, PendingTrade, User, UserCredential
from .oauth_schwab import (
    SCHWAB_OAUTH_KIND_ACCOUNT,
    SCHWAB_OAUTH_KIND_MARKET,
    exchange_schwab_code_for_tokens,
    schwab_authorize_url,
    sign_schwab_oauth_state,
    verify_schwab_oauth_state,
)
from .preset_catalog import PRESET_PROFILES, build_preset_catalog_payload
from .recovery_map import map_failure
from .schemas import ApiResponse, ApproveTradeRequest, CreatePendingTrade
from .security import (
    encrypt_secret,
    get_current_user,
    parse_json,
    parse_scopes,
    require_paid_entitlement,
    utcnow_iso,
)
from .tenant_runtime import tenant_skill_dir, user_has_account_session

router = APIRouter()

ONBOARDING_TARGET_MINUTES = 20
DEFAULT_AUTOMATION_OPT_IN = False
DEFAULT_UI_MODE = "standard"
DEFAULT_PROFILE = "balanced"


def _db() -> OrmSession:
    from .db import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err(message: str, data: Any = None) -> ApiResponse:
    return ApiResponse(ok=False, error=message, data=data)


def _save_state(db: OrmSession, user_id: str, key: str, payload: dict[str, Any]) -> None:
    row = db.query(AppState).filter(AppState.user_id == user_id, AppState.key == key).first()
    if not row:
        row = AppState(user_id=user_id, key=key, value_json=json.dumps(payload, default=_json_default))
        db.add(row)
    else:
        row.value_json = json.dumps(payload, default=_json_default)
    db.commit()


def _load_state(db: OrmSession, user_id: str, key: str, default: dict[str, Any]) -> dict[str, Any]:
    row = db.query(AppState).filter(AppState.user_id == user_id, AppState.key == key).first()
    if not row:
        return default
    parsed = parse_json(row.value_json, default)
    return parsed if isinstance(parsed, dict) else default


def _trade_to_dict(row: PendingTrade) -> dict[str, Any]:
    return {
        "id": row.id,
        "ticker": row.ticker,
        "qty": row.qty,
        "price": row.price,
        "status": row.status,
        "note": row.note,
        "signal": json.loads(row.signal_json or "{}"),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _build_portfolio_summary(account_status: dict[str, Any]) -> dict[str, Any]:
    accounts = account_status.get("accounts", [])
    positions: list[dict[str, Any]] = []
    total_value = 0.0

    for acc in accounts:
        sec = acc.get("securitiesAccount", acc)
        for pos in sec.get("positions", []):
            inst = pos.get("instrument", {})
            sym = inst.get("symbol", "?")
            qty = pos.get("longQuantity", 0) or pos.get("shortQuantity", 0) or 0
            if not qty:
                continue
            mkt_val = float(pos.get("marketValue", 0) or 0)
            day_pl = float(pos.get("currentDayProfitLoss", 0) or 0)
            avg_cost = float(pos.get("averagePrice", 0) or 0)
            last = (mkt_val / qty) if qty else 0.0
            pl_pct = ((last - avg_cost) / avg_cost * 100.0) if avg_cost else 0.0
            total_value += mkt_val
            positions.append(
                {
                    "symbol": sym,
                    "qty": int(qty),
                    "market_value": round(mkt_val, 2),
                    "day_pl": round(day_pl, 2),
                    "avg_cost": round(avg_cost, 4),
                    "last": round(last, 4),
                    "pl_pct": round(pl_pct, 2),
                }
            )

    positions.sort(key=lambda row: abs(float(row.get("market_value", 0))), reverse=True)
    return {
        "account_count": len(accounts),
        "positions_count": len(positions),
        "total_market_value": round(total_value, 2),
        "positions": positions,
    }


def _quote_health_hint(meta: dict[str, Any], quote_ok: bool) -> str | None:
    if quote_ok:
        return None
    reason = str(meta.get("reason") or "")
    if reason == "http_error":
        return "Schwab returned an error for the market-data quotes request. Re-authenticate the market app if it keeps failing."
    if reason == "no_matching_symbol_in_response":
        return "The quotes response did not contain the probe symbol. Confirm your market token has quotes access."
    if reason:
        return f"Quote check failed ({reason})."
    return "Quote check failed for an unknown reason."


def _apply_profile_to_runtime(profile: str) -> dict[str, str]:
    active = PRESET_PROFILES.get(profile, PRESET_PROFILES[DEFAULT_PROFILE])
    for k, v in active.items():
        os.environ[k] = str(v)
    return dict(active)


def _saas_pretrade_checklist(trade: PendingTrade, signal: dict[str, Any]) -> dict[str, Any]:
    max_trades = int(os.getenv("MAX_TRADES_PER_DAY", "20") or 20)
    max_total_account = float(os.getenv("MAX_TOTAL_ACCOUNT_VALUE", "500000") or 500000)
    est_value = float((trade.price or 0) * (trade.qty or 0))
    est_risk_pct = (
        round((est_value / max_total_account) * 100.0, 2) if max_total_account > 0 and est_value > 0 else None
    )
    event_risk = signal.get("event_risk") if isinstance(signal, dict) else {}
    regime = signal.get("regime_v2") if isinstance(signal, dict) else {}
    blocked: list[str] = []
    if isinstance(event_risk, dict) and event_risk.get("mode") == "live" and event_risk.get("flagged") and event_risk.get("action") == "block":
        blocked.append("event_risk_block")
    if isinstance(regime, dict) and str(regime.get("mode", "off")) == "live":
        score = float(regime.get("score", 100) or 100)
        gate = float(os.getenv("REGIME_V2_ENTRY_MIN_SCORE", "55") or 55)
        if score < gate:
            blocked.append("regime_v2_block")

    return with_plain_language(
        {
            "risk_percent_estimate": est_risk_pct,
            "max_daily_trades": max_trades,
            "live_trades_today": 0,
            "shadow_trades_today": 0,
            "event_risk": event_risk if isinstance(event_risk, dict) else {},
            "regime_status": regime if isinstance(regime, dict) else {},
            "blocked": bool(blocked),
            "block_reasons": blocked,
            "requires_explicit_approval": True,
        }
    )


def _tenant_api_health_snapshot(db: OrmSession, user_id: str) -> dict[str, Any]:
    linked = user_has_account_session(db, user_id)
    market_ok = account_ok = quote_ok = False
    if linked:
        try:
            with tenant_skill_dir(db, user_id) as skill_dir:
                auth = DualSchwabAuth(skill_dir=skill_dir)
                market_ok = bool(auth.get_market_token())
                account_ok = bool(auth.get_account_token())
                quote, qmeta = get_current_quote_with_status("AAPL", auth=auth, skill_dir=skill_dir)
                quote_ok = extract_schwab_last_price(quote) is not None
        except Exception as exc:
            return {
                "schwab_linked": True,
                "market_token_ok": False,
                "account_token_ok": False,
                "quote_ok": False,
                "error": str(exc)[:200],
            }
    return {
        "schwab_linked": linked,
        "market_token_ok": market_ok,
        "account_token_ok": account_ok,
        "quote_ok": quote_ok,
    }


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("/api/status", response_model=ApiResponse)
def tenant_status(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    try:
        checked_at = datetime.now(timezone.utc).isoformat()
        snap = _tenant_api_health_snapshot(db, user.id)
        market_token_ok = bool(snap.get("market_token_ok"))
        account_token_ok = bool(snap.get("account_token_ok"))
        last_scan = _load_state(
            db,
            user.id,
            "last_scan",
            default={
                "at": None,
                "signals_found": None,
                "diagnostics": None,
                "diagnostics_summary": None,
                "strategy_summary": None,
            },
        )
        return _ok(
            {
                "market_token_ok": market_token_ok,
                "account_token_ok": account_token_ok,
                "market_state": "Connected" if market_token_ok else "Disconnected",
                "account_state": "Connected" if account_token_ok else "Disconnected",
                "checked_at": checked_at,
                "last_scan": last_scan,
                "validation_status": {"exists": False, "run_status": "idle", "source": "saas"},
                "connection_status": "connected" if snap.get("schwab_linked") else "disconnected",
                "api_health": snap,
            }
        )
    except Exception as exc:
        return _err("status", str(exc))


@router.get("/api/health/deep", response_model=ApiResponse)
def tenant_health_deep(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    try:
        db_ok = True
        snap = _tenant_api_health_snapshot(db, user.id)
        market_token_ok = bool(snap.get("market_token_ok"))
        account_token_ok = bool(snap.get("account_token_ok"))
        quote_ok = bool(snap.get("quote_ok"))
        qh: dict[str, Any] = {
            "symbol": "AAPL",
            "ok": quote_ok,
            "reason": None if quote_ok else (snap.get("error") or "not_linked_or_probe_failed"),
            "operator_hint": None,
        }
        if not quote_ok and snap.get("schwab_linked"):
            try:
                with tenant_skill_dir(db, user.id) as skill_dir:
                    auth = DualSchwabAuth(skill_dir=skill_dir)
                    _quote, qmeta = get_current_quote_with_status("AAPL", auth=auth, skill_dir=skill_dir)
                    qh["operator_hint"] = _quote_health_hint(qmeta, quote_ok)
                    qh["reason"] = qmeta.get("reason")
            except Exception:
                pass
        return _ok(
            {
                "db_ok": db_ok,
                "market_token_ok": market_token_ok,
                "account_token_ok": account_token_ok,
                "quote_ok": quote_ok,
                "quote_health": qh,
                "metrics": {"requests_total": 0, "errors_total": 0},
            }
        )
    except Exception as exc:
        return _err("health_deep", str(exc))


@router.get("/api/recovery/map", response_model=ApiResponse)
def tenant_recovery_map(error: str, source: str = "unknown") -> ApiResponse:
    return _ok(map_failure(error, source=source))


@router.get("/api/portfolio", response_model=ApiResponse)
def tenant_portfolio(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before loading portfolio.")
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            status_data = get_account_status(skill_dir=skill_dir)
        if isinstance(status_data, str):
            return _err(status_data)
        return _ok(_build_portfolio_summary(status_data))
    except Exception as exc:
        return _err("portfolio", str(exc))


@router.get("/api/sectors", response_model=ApiResponse)
def tenant_sectors(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before loading sectors.")
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            heatmap = get_sector_heatmap(auth=DualSchwabAuth(skill_dir=skill_dir), skill_dir=skill_dir)
        return _ok(heatmap)
    except Exception as exc:
        return _err("sectors", str(exc))


@router.get("/api/pending-trades", response_model=ApiResponse)
def tenant_list_pending(
    status: str | None = None,
    sort: str = "newest",
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    rows_query = db.query(PendingTrade).filter(PendingTrade.user_id == user.id)
    if status and status.lower() != "all":
        rows_query = rows_query.filter(PendingTrade.status == status.lower().strip())
    if sort == "oldest":
        rows_query = rows_query.order_by(PendingTrade.created_at.asc())
    else:
        rows_query = rows_query.order_by(PendingTrade.created_at.desc())
    rows = rows_query.all()
    return _ok([_trade_to_dict(r) for r in rows])


@router.post("/api/pending-trades", response_model=ApiResponse)
def tenant_create_pending(
    payload: CreatePendingTrade,
    user: User = Depends(require_paid_entitlement),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before creating pending trades.")
    try:
        ticker = payload.ticker.upper().strip()
        signal = payload.signal or {}
        with tenant_skill_dir(db, user.id) as skill_dir:
            auth = DualSchwabAuth(skill_dir=skill_dir)
            quote = get_current_quote(ticker, auth=auth, skill_dir=skill_dir)
            last_price = payload.price or extract_schwab_last_price(quote) or float(signal.get("price", 0) or 0)

            qty = payload.qty
            if qty is None:
                usd_size = get_position_size_usd(
                    ticker=ticker,
                    price=last_price if last_price > 0 else None,
                    skill_dir=skill_dir,
                )
                qty = max(1, int(usd_size / last_price)) if last_price > 0 else 1

        trade_id = uuid.uuid4().hex[:8]
        row = PendingTrade(
            id=trade_id,
            user_id=user.id,
            ticker=ticker,
            qty=qty,
            price=last_price if last_price > 0 else None,
            status="pending",
            signal_json=json.dumps(signal, default=_json_default),
            note=payload.note,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _ok(_trade_to_dict(row))
    except Exception as exc:
        return _err("create_pending_trade", str(exc))


@router.post("/api/pending-trades/clear-pending", response_model=ApiResponse)
def tenant_clear_all_pending(
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    rows = db.query(PendingTrade).filter(PendingTrade.user_id == user.id, PendingTrade.status == "pending").all()
    for row in rows:
        row.status = "rejected"
    db.commit()
    return _ok({"cleared": len(rows)})


@router.get("/api/calibration/summary", response_model=ApiResponse)
def tenant_calibration_summary(
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    row = (
        db.query(AppState)
        .filter(AppState.user_id == user.id, AppState.key == "calibration_snapshot")
        .first()
    )
    if not row:
        return _ok(
            {
                "empty": True,
                "hint": "Populated when a scan finds .self_study.json or .hypothesis_ledger.json in the worker session. "
                "Set HYPOTHESIS_LEDGER_ENABLED on API/workers to forward into tenant env.",
            }
        )
    data = parse_json(row.value_json, {})
    return _ok(data if isinstance(data, dict) else {"raw": data})


@router.post("/api/trades/{trade_id}/approve", response_model=ApiResponse)
def tenant_approve_trade(
    request: Request,
    trade_id: str,
    payload: ApproveTradeRequest,
    confirm_live: bool = False,
    user: User = Depends(require_paid_entitlement),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return _err("Link Schwab account before approving trades.")
    db_user = db.query(User).filter(User.id == user.id).first()
    if not db_user:
        return _err("User not found.")
    if getattr(db_user, "trading_halted", False):
        raise HTTPException(
            status_code=403,
            detail="Trading is paused for this account. Resume under account settings before approving live orders.",
        )
    if not db_user.live_execution_enabled:
        raise HTTPException(
            status_code=403,
            detail="Live trading is off. Enable it under Strategy Presets after reviewing risk, then approve again.",
        )
    row = db.query(PendingTrade).filter(PendingTrade.id == trade_id, PendingTrade.user_id == user.id).first()
    if not row:
        return ApiResponse(ok=False, error="Trade not found.")
    if row.status != "pending":
        return ApiResponse(ok=False, error=f"Trade already {row.status}.")

    typed = (payload.typed_ticker or "").strip().upper()
    if typed != row.ticker.upper():
        return ApiResponse(
            ok=False,
            error="typed_ticker must exactly match the staged trade ticker (re-type to confirm the live order).",
        )

    signal = json.loads(row.signal_json or "{}")
    settings = _load_state(db, user.id, "ui_settings", {})
    automation_opt_in = bool(settings.get("automation_opt_in", DEFAULT_AUTOMATION_OPT_IN))
    if not automation_opt_in and not confirm_live:
        checklist = _saas_pretrade_checklist(row, signal if isinstance(signal, dict) else {})
        return ApiResponse(
            ok=False,
            error="Explicit live confirmation required. Review checklist and retry with confirm_live=true.",
            data={"checklist": checklist, "automation_opt_in": automation_opt_in},
        )

    with tenant_skill_dir(db, user.id) as skill_dir:
        result = place_order(
            ticker=row.ticker,
            qty=row.qty,
            side="BUY",
            order_type="MARKET",
            price_hint=row.price,
            mirofish_conviction=signal.get("mirofish_conviction"),
            sector_etf=signal.get("sector_etf"),
            skill_dir=skill_dir,
        )

    if isinstance(result, str):
        row.status = "failed"
        row.note = (row.note or "") + f" | {result}" if row.note else result
        db.commit()
        db.refresh(row)
        log_audit(
            db,
            action="trade_approve_failed",
            user_id=user.id,
            detail={
                "trade_id": trade_id,
                "ticker": row.ticker,
                "error_excerpt": result[:240],
            },
            request_id=_request_id(request),
        )
        return ApiResponse(
            ok=False,
            error=result,
            data={
                "trade": _trade_to_dict(row),
                "recovery": map_failure(result, source="execution"),
            },
        )

    row.status = "executed"
    db.commit()
    db.refresh(row)
    log_audit(
        db,
        action="trade_approved_executed",
        user_id=user.id,
        detail={"trade_id": trade_id, "ticker": row.ticker, "qty": row.qty},
        request_id=_request_id(request),
    )
    return _ok({"trade": _trade_to_dict(row), "result": result})


@router.post("/api/trades/{trade_id}/reject", response_model=ApiResponse)
def tenant_reject_trade(
    trade_id: str,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    row = db.query(PendingTrade).filter(PendingTrade.id == trade_id, PendingTrade.user_id == user.id).first()
    if not row:
        return ApiResponse(ok=False, error="Trade not found.")
    if row.status != "pending":
        return ApiResponse(ok=False, error=f"Trade already {row.status}.")
    row.status = "rejected"
    db.commit()
    db.refresh(row)
    return _ok(_trade_to_dict(row))


@router.get("/api/trades/{trade_id}/preflight", response_model=ApiResponse)
def tenant_preflight_trade(
    trade_id: str,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    row = db.query(PendingTrade).filter(PendingTrade.id == trade_id, PendingTrade.user_id == user.id).first()
    if not row:
        return ApiResponse(ok=False, error="Trade not found.")
    signal = json.loads(row.signal_json or "{}")
    return _ok(
        {
            "trade": _trade_to_dict(row),
            "checklist": _saas_pretrade_checklist(row, signal if isinstance(signal, dict) else {}),
        }
    )


@router.get("/api/settings/profiles", response_model=ApiResponse)
def tenant_get_profiles(expert: bool = False, user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    settings = _load_state(db, user.id, "ui_settings", {})
    profile = str(settings.get("profile", DEFAULT_PROFILE)).strip().lower()
    profile = profile if profile in PRESET_PROFILES else DEFAULT_PROFILE
    active = dict(PRESET_PROFILES.get(profile, {}))
    payload: dict[str, Any] = {
        "mode": settings.get("mode", DEFAULT_UI_MODE),
        "profile": profile,
        "automation_opt_in": bool(settings.get("automation_opt_in", DEFAULT_AUTOMATION_OPT_IN)),
        "profiles": sorted(PRESET_PROFILES.keys()),
        "active_profile_settings": active,
    }
    if expert:
        payload["expert_runtime_overrides"] = {k: os.environ.get(k) for k in sorted(active.keys())}
    payload["preset_catalog"] = build_preset_catalog_payload()
    return _ok(payload)


@router.post("/api/settings/profile", response_model=ApiResponse)
def tenant_set_profile(
    profile: str = DEFAULT_PROFILE,
    mode: str = DEFAULT_UI_MODE,
    automation_opt_in: bool = False,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    p = str(profile or DEFAULT_PROFILE).strip().lower()
    if p not in PRESET_PROFILES:
        return ApiResponse(ok=False, error=f"Invalid profile '{profile}'.")
    mode_n = str(mode or DEFAULT_UI_MODE).strip().lower()
    if mode_n not in {"standard", "expert"}:
        return ApiResponse(ok=False, error="Invalid mode. Use standard or expert.")
    runtime = _apply_profile_to_runtime(p)
    settings = {
        "mode": mode_n,
        "profile": p,
        "automation_opt_in": bool(automation_opt_in),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(db, user.id, "ui_settings", settings)
    return _ok({"settings": settings, "runtime_overrides": runtime})


@router.post("/api/onboarding/start", response_model=ApiResponse)
def tenant_onboarding_start(user: User = Depends(get_current_user), db: OrmSession = Depends(_db)) -> ApiResponse:
    state = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "target_minutes": ONBOARDING_TARGET_MINUTES,
        "steps": {
            "connect": {"ok": False, "at": None},
            "verify_token_health": {"ok": False, "at": None},
            "test_scan": {"ok": False, "at": None},
            "test_paper_order": {"ok": False, "at": None},
        },
    }
    _save_state(db, user.id, "onboarding_wizard", state)
    return _ok(state)


@router.post("/api/onboarding/step/{step}", response_model=ApiResponse)
def tenant_onboarding_step(
    step: str,
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(_db),
) -> ApiResponse:
    current = _load_state(
        db,
        user.id,
        "onboarding_wizard",
        default={
            "started_at": datetime.now(timezone.utc).isoformat(),
            "target_minutes": ONBOARDING_TARGET_MINUTES,
            "steps": {},
        },
    )
    steps = current.setdefault("steps", {})
    step_key = str(step or "").strip().lower()
    now_iso = datetime.now(timezone.utc).isoformat()

    if step_key == "connect":
        linked = user_has_account_session(db, user.id)
        steps["connect"] = {
            "ok": linked,
            "at": now_iso,
            "details": {"schwab_linked": linked},
            "fix_path": "Use Connect Schwab (account) and Connect Schwab (market) on the dashboard, or paste tokens via API if your host allows it.",
        }
    elif step_key == "verify_token_health":
        snap = _tenant_api_health_snapshot(db, user.id)
        ok = bool(snap.get("schwab_linked") and snap.get("market_token_ok") and snap.get("account_token_ok") and snap.get("quote_ok"))
        steps["verify_token_health"] = {
            "ok": ok,
            "at": now_iso,
            "details": snap,
            "fix_path": "Finish both Schwab connect buttons (account and market), then refresh this page.",
        }
    elif step_key == "test_scan":
        if not user_has_paid_entitlement(user):
            return ApiResponse(ok=False, error="Active subscription required for test scan.")
        if not user_has_account_session(db, user.id):
            steps["test_scan"] = {"ok": False, "at": now_iso, "details": {"error": "Schwab not linked"}}
        else:
            try:
                with tenant_skill_dir(db, user.id) as skill_dir:
                    signals, diagnostics = scan_for_signals_detailed(skill_dir=skill_dir)
                scan_ok = diagnostics.get("scan_blocked", 0) == 0 and diagnostics.get("exceptions", 0) == 0
                steps["test_scan"] = {
                    "ok": bool(scan_ok),
                    "at": now_iso,
                    "details": {
                        "signals_found": len(signals),
                        "diagnostics_summary": {k: diagnostics.get(k) for k in ("watchlist_size", "exceptions", "scan_blocked")},
                    },
                }
            except Exception as e:
                steps["test_scan"] = {
                    "ok": False,
                    "at": now_iso,
                    "details": {"error": str(e)},
                    "recovery": map_failure(str(e), source="signal_scanner"),
                }
    elif step_key == "test_paper_order":
        if not user_has_paid_entitlement(user):
            return ApiResponse(ok=False, error="Active subscription required for paper order test.")
        if not user_has_account_session(db, user.id):
            steps["test_paper_order"] = {"ok": False, "at": now_iso, "details": {"error": "Schwab not linked"}}
        else:
            previous_shadow = os.environ.get("EXECUTION_SHADOW_MODE")
            os.environ["EXECUTION_SHADOW_MODE"] = "1"
            try:
                with tenant_skill_dir(db, user.id) as skill_dir:
                    auth = DualSchwabAuth(skill_dir=skill_dir)
                    quote = get_current_quote("AAPL", auth=auth, skill_dir=skill_dir)
                    price = extract_schwab_last_price(quote) or 100.0
                    result = place_order(
                        ticker="AAPL",
                        qty=1,
                        side="BUY",
                        order_type="MARKET",
                        price_hint=price,
                        skill_dir=skill_dir,
                    )
                ok = isinstance(result, dict) and bool(result.get("shadow_mode"))
                steps["test_paper_order"] = {
                    "ok": ok,
                    "at": now_iso,
                    "details": result if isinstance(result, dict) else {"result": result},
                }
            except Exception as e:
                steps["test_paper_order"] = {
                    "ok": False,
                    "at": now_iso,
                    "details": {"error": str(e)},
                    "recovery": map_failure(str(e), source="execution"),
                }
            finally:
                if previous_shadow is None:
                    os.environ.pop("EXECUTION_SHADOW_MODE", None)
                else:
                    os.environ["EXECUTION_SHADOW_MODE"] = previous_shadow
    else:
        return ApiResponse(ok=False, error="Unknown onboarding step.")

    _save_state(db, user.id, "onboarding_wizard", current)
    return _ok(current)


@router.get("/api/oauth/schwab/authorize-url", response_model=ApiResponse)
def schwab_authorize_url_endpoint(user: User = Depends(get_current_user)) -> ApiResponse:
    client_id = (os.getenv("SCHWAB_ACCOUNT_APP_KEY") or "").strip()
    redirect_uri = (os.getenv("SCHWAB_CALLBACK_URL") or "").strip()
    if not client_id or not redirect_uri:
        raise HTTPException(
            status_code=503,
            detail="Configure SCHWAB_ACCOUNT_APP_KEY and SCHWAB_CALLBACK_URL for OAuth.",
        )
    try:
        state = sign_schwab_oauth_state(user.id, SCHWAB_OAUTH_KIND_ACCOUNT)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    url = schwab_authorize_url(client_id, redirect_uri, state)
    return _ok({"url": url, "state": state})


@router.get("/api/oauth/schwab/market/authorize-url", response_model=ApiResponse)
def schwab_market_authorize_url_endpoint(user: User = Depends(get_current_user)) -> ApiResponse:
    client_id = (os.getenv("SCHWAB_MARKET_APP_KEY") or "").strip()
    redirect_uri = (os.getenv("SCHWAB_MARKET_CALLBACK_URL") or "").strip()
    if not client_id or not redirect_uri:
        raise HTTPException(
            status_code=503,
            detail="Configure SCHWAB_MARKET_APP_KEY and SCHWAB_MARKET_CALLBACK_URL for market OAuth.",
        )
    try:
        state = sign_schwab_oauth_state(user.id, SCHWAB_OAUTH_KIND_MARKET)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    url = schwab_authorize_url(client_id, redirect_uri, state)
    return _ok({"url": url, "state": state})


@router.get("/api/oauth/schwab/callback")
def schwab_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: OrmSession = Depends(_db),
):
    front = (os.getenv("SAAS_FRONTEND_URL") or "http://127.0.0.1:8000").rstrip("/")

    def red(qs: str) -> RedirectResponse:
        return RedirectResponse(f"{front}/?{qs}", status_code=302)

    if error:
        return red(f"schwab_oauth=error&message={urllib.parse.quote(error)}")
    verified = verify_schwab_oauth_state(state)
    if not verified or not code.strip():
        return red("schwab_oauth=error&message=" + urllib.parse.quote("invalid_or_expired_state"))
    user_id, kind = verified
    if kind != SCHWAB_OAUTH_KIND_ACCOUNT:
        return red(
            "schwab_oauth=error&message="
            + urllib.parse.quote("wrong_oauth_flow_use_account_authorize_link")
        )

    client_id = (os.getenv("SCHWAB_ACCOUNT_APP_KEY") or "").strip()
    client_secret = (os.getenv("SCHWAB_ACCOUNT_APP_SECRET") or "").strip()
    redirect_uri = (os.getenv("SCHWAB_CALLBACK_URL") or "").strip()
    if not client_id or not client_secret or not redirect_uri:
        return red("schwab_oauth=error&message=" + urllib.parse.quote("server_oauth_not_configured"))

    try:
        tok = exchange_schwab_code_for_tokens(client_id, client_secret, code, redirect_uri)
    except Exception as exc:
        return red("schwab_oauth=error&message=" + urllib.parse.quote(str(exc)[:180]))

    access = str(tok.get("access_token") or "").strip()
    refresh = str(tok.get("refresh_token") or "").strip()
    if not access or not refresh:
        return red("schwab_oauth=error&message=" + urllib.parse.quote("token_response_missing_tokens"))

    row = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
    if not row:
        row = UserCredential(user_id=user_id)
        db.add(row)

    row.access_token_enc = encrypt_secret(access)
    row.refresh_token_enc = encrypt_secret(refresh)
    row.token_type = (str(tok.get("token_type") or "Bearer").strip() or "Bearer")
    exp_in = tok.get("expires_in")
    if exp_in is not None:
        try:
            row.expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(exp_in))
        except Exception:
            row.expires_at = None
    scope_raw = tok.get("scope")
    if isinstance(scope_raw, str) and scope_raw.strip():
        parts = [p.strip() for p in scope_raw.replace(",", " ").split() if p.strip()]
        row.scopes = parse_scopes(parts)
    else:
        row.scopes = parse_scopes(None)
    row.account_token_payload_enc = encrypt_secret(json.dumps(tok, default=_json_default))

    db.commit()
    db.refresh(row)

    _save_state(
        db,
        user_id,
        "onboarding",
        {
            "linked_at": utcnow_iso(),
            "schwab_linked": True,
            "wizard_required": False,
        },
    )
    log_audit(
        db,
        action="oauth_schwab_callback",
        user_id=user_id,
        detail={},
        request_id=_request_id(request),
    )
    return red("schwab_oauth=ok")


@router.get("/api/oauth/schwab/market/callback")
def schwab_market_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: OrmSession = Depends(_db),
):
    front = (os.getenv("SAAS_FRONTEND_URL") or "http://127.0.0.1:8000").rstrip("/")

    def red(qs: str) -> RedirectResponse:
        return RedirectResponse(f"{front}/?{qs}", status_code=302)

    if error:
        return red(f"schwab_market_oauth=error&message={urllib.parse.quote(error)}")
    verified = verify_schwab_oauth_state(state)
    if not verified or not code.strip():
        return red(
            "schwab_market_oauth=error&message=" + urllib.parse.quote("invalid_or_expired_state")
        )
    user_id, kind = verified
    if kind != SCHWAB_OAUTH_KIND_MARKET:
        return red(
            "schwab_market_oauth=error&message="
            + urllib.parse.quote("wrong_oauth_flow_use_market_authorize_link")
        )

    client_id = (os.getenv("SCHWAB_MARKET_APP_KEY") or "").strip()
    client_secret = (os.getenv("SCHWAB_MARKET_APP_SECRET") or "").strip()
    redirect_uri = (os.getenv("SCHWAB_MARKET_CALLBACK_URL") or "").strip()
    if not client_id or not client_secret or not redirect_uri:
        return red(
            "schwab_market_oauth=error&message="
            + urllib.parse.quote("server_market_oauth_not_configured")
        )

    try:
        tok = exchange_schwab_code_for_tokens(client_id, client_secret, code, redirect_uri)
    except Exception as exc:
        return red(
            "schwab_market_oauth=error&message=" + urllib.parse.quote(str(exc)[:180])
        )

    access = str(tok.get("access_token") or "").strip()
    refresh = str(tok.get("refresh_token") or "").strip()
    if not access or not refresh:
        return red(
            "schwab_market_oauth=error&message="
            + urllib.parse.quote("token_response_missing_tokens")
        )

    row = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
    if not row:
        row = UserCredential(user_id=user_id)
        db.add(row)

    row.market_token_payload_enc = encrypt_secret(json.dumps(tok, default=_json_default))

    db.commit()
    db.refresh(row)

    log_audit(
        db,
        action="oauth_schwab_market_callback",
        user_id=user_id,
        detail={},
        request_id=_request_id(request),
    )
    return red("schwab_market_oauth=ok")
