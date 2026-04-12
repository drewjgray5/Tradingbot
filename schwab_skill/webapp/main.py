from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from execution import get_account_status, get_position_size_usd, place_order
from full_report import REPORT_SECTION_MAP, generate_full_report, quick_check, report_to_json
from market_data import extract_schwab_last_price, get_current_quote, get_current_quote_with_status
from schwab_auth import DualSchwabAuth
from sec_filing_compare import (
    analyze_latest_filing_for_ticker,
    compare_ticker_over_time,
    compare_ticker_vs_ticker,
)
from sector_strength import get_sector_heatmap
from signal_scanner import scan_for_signals_detailed

from .calibration_snapshot import build_calibration_snapshot
from .checklist_language import with_plain_language
from .db import DATABASE_URL, Base, SessionLocal, engine
from .models import AppState, PendingTrade, User
from .preset_catalog import PRESET_PROFILES, build_preset_catalog_payload
from .recovery_map import map_failure as _map_failure
from .scan_payload import parse_scan_run_body, scan_runtime_kwargs
from .schemas import ApiResponse, ApproveTradeRequest, CreatePendingTrade

LOCAL_DASHBOARD_USER_ID = (os.getenv("WEB_LOCAL_USER_ID", "local") or "local").strip() or "local"

LOG = logging.getLogger("webapp")
if not LOG.handlers:
    logging.basicConfig(level=logging.INFO)

APP_DIR = Path(__file__).resolve().parent
SKILL_DIR = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"
AUDIT_LOG_PATH = APP_DIR / "audit.log"
VALIDATION_ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
BACKTEST_RESULTS_PATH = SKILL_DIR / ".backtest_results.json"
TRADE_OUTCOMES_PATH = SKILL_DIR / ".trade_outcomes.json"
EXECUTION_METRICS_PATH = SKILL_DIR / "execution_safety_metrics.json"
TOKENS_MARKET_PATH = SKILL_DIR / "tokens_market.enc"
TOKENS_ACCOUNT_PATH = SKILL_DIR / "tokens_account.enc"
ONBOARDING_TARGET_MINUTES = 20
DEFAULT_AUTOMATION_OPT_IN = False
DEFAULT_UI_MODE = "standard"
DEFAULT_PROFILE = "balanced"


def _ensure_local_dashboard_user() -> None:
    db = SessionLocal()
    try:
        if db.get(User, LOCAL_DASHBOARD_USER_ID) is None:
            db.add(
                User(
                    id=LOCAL_DASHBOARD_USER_ID,
                    email=None,
                    auth_provider="local_dashboard",
                )
            )
            db.commit()
    finally:
        db.close()


def _run_alembic_upgrade_head_for_sqlite() -> None:
    """Apply Alembic revisions so existing SQLite files gain new columns (e.g. Stripe billing)."""
    if not DATABASE_URL.startswith("sqlite"):
        return
    alembic_ini = APP_DIR.parent / "alembic.ini"
    if not alembic_ini.is_file():
        return
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config(str(alembic_ini)), "head")


Base.metadata.create_all(bind=engine)
_run_alembic_upgrade_head_for_sqlite()
_ensure_local_dashboard_user()

app = FastAPI(
    title="TradingBot Web Dashboard API",
    version="0.2.0",
    description="Web API for scanning, approvals, portfolio, and sector health.",
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
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "X-User"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_metrics_lock = threading.Lock()
_request_metrics: dict[str, Any] = {
    "requests_total": 0,
    # Counts HTTP 5xx only (plus worker `_record_endpoint_error`). Client 4xx live in `client_errors_total`.
    "errors_total": 0,
    "client_errors_total": 0,
    "by_path": {},
    "endpoint_errors": {},
}

# Cap persisted scan payloads so AppState rows stay reasonable.
_LAST_SCAN_SIGNALS_CAP = min(200, int(os.getenv("WEB_LAST_SCAN_SIGNALS_CAP", "120") or 120))
_scan_lock = threading.Lock()
_scan_job: dict[str, Any] = {
    "job_id": None,
    "status": "idle",  # idle | running | completed | failed
    "started_at": None,
    "finished_at": None,
    "signals_found": None,
    "diagnostics": None,
    "diagnostics_summary": None,
    "strategy_summary": None,
    "signals": [],
    "error": None,
}


def _record_endpoint_error(endpoint: str) -> None:
    with _metrics_lock:
        bucket = _request_metrics.setdefault("endpoint_errors", {})
        bucket[endpoint] = int(bucket.get(endpoint, 0) or 0) + 1
        _request_metrics["errors_total"] = int(_request_metrics.get("errors_total", 0) or 0) + 1


def _record_request(path: str, method: str, status_code: int, latency_ms: float) -> None:
    key = f"{method} {path}"
    with _metrics_lock:
        _request_metrics["requests_total"] = int(_request_metrics.get("requests_total", 0) or 0) + 1
        bucket = _request_metrics.setdefault("by_path", {}).setdefault(
            key,
            {
                "count": 0,
                "errors": 0,
                "client_errors": 0,
                "server_errors": 0,
                "last_status": 0,
                "last_latency_ms": 0.0,
            },
        )
        bucket["count"] = int(bucket.get("count", 0) or 0) + 1
        bucket["last_status"] = status_code
        bucket["last_latency_ms"] = round(latency_ms, 2)
        if status_code >= 500:
            bucket["server_errors"] = int(bucket.get("server_errors", 0) or 0) + 1
            bucket["errors"] = int(bucket.get("errors", 0) or 0) + 1
            _request_metrics["errors_total"] = int(_request_metrics.get("errors_total", 0) or 0) + 1
        elif status_code >= 400:
            bucket["client_errors"] = int(bucket.get("client_errors", 0) or 0) + 1
            bucket["errors"] = int(bucket.get("errors", 0) or 0) + 1
            _request_metrics["client_errors_total"] = int(
                _request_metrics.get("client_errors_total", 0) or 0
            ) + 1


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    _record_request(request.url.path, request.method, response.status_code, elapsed_ms)
    LOG.info("%s %s -> %s (%.1f ms)", request.method, request.url.path, response.status_code, elapsed_ms)
    return response


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err(endpoint: str, exc: Exception) -> ApiResponse:
    _record_endpoint_error(endpoint)
    mapped = _map_failure(str(exc), source=endpoint)
    raw = str(mapped.get("raw_error") or "").strip()
    headline = f"{mapped.get('title', 'Error')}: {mapped.get('summary', 'Something went wrong.')}"
    summary = str(mapped.get("summary") or "")
    err_out = headline
    if raw and raw.lower() not in summary.lower():
        err_out = f"{headline} — {raw[:220]}"
    return ApiResponse(ok=False, error=err_out, data={"recovery": mapped})


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _load_ui_settings(db: Session) -> dict[str, Any]:
    return _load_state(
        db,
        key="ui_settings",
        default={
            "mode": DEFAULT_UI_MODE,
            "profile": DEFAULT_PROFILE,
            "automation_opt_in": DEFAULT_AUTOMATION_OPT_IN,
        },
    )


def _apply_profile_to_runtime(profile: str) -> dict[str, str]:
    payload = PRESET_PROFILES.get(profile, PRESET_PROFILES[DEFAULT_PROFILE])
    for key, value in payload.items():
        os.environ[key] = value
    return payload


def _token_health() -> dict[str, Any]:
    return {
        "market_token_file": TOKENS_MARKET_PATH.exists(),
        "account_token_file": TOKENS_ACCOUNT_PATH.exists(),
    }


def _build_pretrade_checklist(trade: PendingTrade, signal: dict[str, Any]) -> dict[str, Any]:
    env = _read_json_file(EXECUTION_METRICS_PATH, {"days": {}})
    days = env.get("days", {}) if isinstance(env, dict) else {}
    today = datetime.now(timezone.utc).date().isoformat()
    todays_events = ((days.get(today) or {}).get("events") or {}) if isinstance(days, dict) else {}
    live_trades_today = int(todays_events.get("action_live", 0) or 0)
    shadow_trades_today = int(todays_events.get("action_shadow", 0) or 0)

    max_trades = int(os.getenv("MAX_TRADES_PER_DAY", "20") or 20)
    max_total_account = float(os.getenv("MAX_TOTAL_ACCOUNT_VALUE", "500000") or 500000)
    est_value = float((trade.price or 0) * (trade.qty or 0))
    est_risk_pct = round((est_value / max_total_account) * 100.0, 2) if max_total_account > 0 and est_value > 0 else None
    event_risk = signal.get("event_risk") if isinstance(signal, dict) else {}
    regime = signal.get("regime_v2") if isinstance(signal, dict) else {}
    blocked = []
    if live_trades_today >= max_trades:
        blocked.append("max_daily_trades_reached")
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
            "live_trades_today": live_trades_today,
            "shadow_trades_today": shadow_trades_today,
            "event_risk": event_risk if isinstance(event_risk, dict) else {},
            "regime_status": regime if isinstance(regime, dict) else {},
            "blocked": bool(blocked),
            "block_reasons": blocked,
            "requires_explicit_approval": True,
        }
    )


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


def _quote_health_hint(meta: dict[str, Any], quote_ok: bool) -> str | None:
    if quote_ok:
        return None
    reason = str(meta.get("reason") or "")
    detail = str(meta.get("error_detail") or "")
    if reason == "http_error":
        return (
            "Schwab returned an error for the market-data quotes request. "
            "Run `python healthcheck.py` and re-authenticate the market app if it keeps failing."
        )
    if reason == "no_matching_symbol_in_response":
        return (
            "The quotes response did not contain the probe symbol. "
            "Confirm the Schwab API is up and your market token has quotes access."
        )
    if reason == "last_price_not_parseable":
        return (
            "Quote JSON was received but no usable last/mark/close price was found. "
            "If this persists after a Schwab API update, extend extract_schwab_last_price in market_data.py."
        )
    if "circuit" in detail.lower() or reason == "RuntimeError":
        return (
            "Repeated connection failures may have opened the Schwab circuit breaker. "
            "Wait a minute, check network/DNS, then retry."
        )
    if reason:
        return f"Quote check failed ({reason}). See trading_bot.log for details."
    return "Quote check failed for an unknown reason. See trading_bot.log for details."


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


def _scan_snapshot() -> dict[str, Any]:
    with _scan_lock:
        elapsed_seconds: int | None = None
        started_at = _scan_job.get("started_at")
        if isinstance(started_at, str):
            try:
                started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                elapsed_seconds = max(0, int((datetime.now(timezone.utc) - started_dt).total_seconds()))
            except Exception:
                elapsed_seconds = None
        return {
            "job_id": _scan_job.get("job_id"),
            "status": _scan_job.get("status"),
            "started_at": started_at,
            "finished_at": _scan_job.get("finished_at"),
            "elapsed_seconds": elapsed_seconds,
            "signals_found": _scan_job.get("signals_found"),
            "diagnostics": _scan_job.get("diagnostics"),
            "diagnostics_summary": _scan_job.get("diagnostics_summary"),
            "strategy_summary": _scan_job.get("strategy_summary"),
            "signals": _scan_job.get("signals") or [],
            "error": _scan_job.get("error"),
        }


def _latest_validation_status() -> dict[str, Any]:
    status_file = VALIDATION_ARTIFACT_DIR / "continuous_validation_status.json"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                latest_artifacts = data.get("latest_artifacts") or {}
                return {
                    "source": "continuous_validation_status",
                    "exists": True,
                    "run_status": data.get("run_status"),
                    "passed": bool(data.get("passed")) if data.get("passed") is not None else None,
                    "started_at": data.get("started_at"),
                    "finished_at": data.get("finished_at"),
                    "generated_at": data.get("generated_at"),
                    "current_step": data.get("current_step"),
                    "current_step_index": data.get("current_step_index"),
                    "completed_steps": data.get("completed_steps"),
                    "total_steps": data.get("total_steps"),
                    "progress_pct": data.get("progress_pct"),
                    "failed_steps": list(data.get("failed_steps") or []),
                    "latest_artifacts": latest_artifacts if isinstance(latest_artifacts, dict) else {},
                }
        except Exception:
            pass

    validate_runs = sorted(VALIDATION_ARTIFACT_DIR.glob("validate_all_*.json"))
    if not validate_runs:
        return {
            "source": "none",
            "exists": False,
            "run_status": "idle",
            "passed": None,
            "started_at": None,
            "finished_at": None,
            "generated_at": None,
            "current_step": None,
            "current_step_index": 0,
            "completed_steps": 0,
            "total_steps": 0,
            "progress_pct": 0,
            "failed_steps": [],
            "latest_artifacts": {},
        }
    latest = validate_runs[-1]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    failed_steps = list(payload.get("failed_steps") or [])
    generated_at = payload.get("generated_at")
    if not generated_at:
        try:
            generated_at = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            generated_at = None
    try:
        rel_path = str(latest.relative_to(SKILL_DIR))
    except ValueError:
        rel_path = str(latest)
    return {
        "source": "validate_all_summary",
        "exists": True,
        "run_status": "completed",
        "passed": bool(payload.get("passed")) if "passed" in payload else None,
        "started_at": None,
        "finished_at": generated_at,
        "generated_at": generated_at,
        "current_step": None,
        "current_step_index": 0,
        "completed_steps": 0,
        "total_steps": 0,
        "progress_pct": 100,
        "failed_steps": failed_steps,
        "latest_artifacts": {"validate_all": rel_path},
    }


def _strategy_summary(signals: list[dict[str, Any]] | None) -> dict[str, Any]:
    rows = signals or []
    counts: dict[str, int] = {}
    for sig in rows:
        attr = sig.get("strategy_attribution") if isinstance(sig, dict) else None
        name = str((attr or {}).get("top_live") or "unknown")
        counts[name] = int(counts.get(name, 0) or 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    dominant = ranked[0][0] if ranked else None
    dominant_count = ranked[0][1] if ranked else 0
    return {
        "dominant_live_strategy": dominant,
        "dominant_count": dominant_count,
        "total_ranked": len(rows),
        "counts": {k: v for k, v in ranked},
    }


def _diagnostics_summary(diag: dict[str, Any] | None, signals: list[dict[str, Any]] | None) -> dict[str, Any]:
    diagnostics = diag or {}
    blocked_reason_raw = diagnostics.get("scan_blocked_reason")
    blocked_reason = str(blocked_reason_raw).strip() if blocked_reason_raw else None
    blocked_human = None
    if blocked_reason == "bear_regime_spy_below_200sma":
        blocked_human = "Scan blocked by regime gate: SPY is below 200 SMA."

    ranked = []
    for key, raw in diagnostics.items():
        try:
            value = int(raw)
        except Exception:
            continue
        if value <= 0 or key == "watchlist_size":
            continue
        ranked.append(
            {
                "key": key,
                "value": value,
                "severity": "error" if key in {"exceptions", "df_empty"} else "warn",
            }
        )
    ranked.sort(key=lambda x: int(x["value"]), reverse=True)
    watchlist = int(diagnostics.get("watchlist_size", 0) or 0)
    stage2_fail = int(diagnostics.get("stage2_fail", 0) or 0)
    vcp_fail = int(diagnostics.get("vcp_fail", 0) or 0)
    final_count = len(signals or [])
    return {
        "scan_blocked": bool(diagnostics.get("scan_blocked")),
        "scan_blocked_reason": blocked_reason,
        "headline": blocked_human,
        "top_blockers": ranked[:5],
        "data_quality": diagnostics.get("data_quality"),
        "data_quality_reasons": list(diagnostics.get("data_quality_reasons") or []),
        "funnel": {
            "watchlist": watchlist,
            "stage2_pass": max(0, watchlist - stage2_fail),
            "vcp_pass": max(0, watchlist - stage2_fail - vcp_fail),
            "final": final_count,
        },
    }


def _build_report_verdicts(report: dict[str, Any]) -> dict[str, Any]:
    technical = report.get("technical") or {}
    dcf = report.get("dcf") or {}
    health = report.get("health") or {}
    miro = report.get("mirofish") or {}
    signal_score = float(technical.get("signal_score", 0) or 0)
    mos = float(dcf.get("margin_of_safety", 0) or 0)
    health_flags = health.get("flags") or []
    conviction = float(miro.get("conviction_score", 0) or 0)

    def bucket(score: float, high: float, low: float) -> str:
        if score >= high:
            return "bullish"
        if score <= low:
            return "bearish"
        return "neutral"

    return {
        "technical": {
            "verdict": bucket(signal_score, 65.0, 45.0),
            "takeaway": "Trend setup aligned." if technical.get("stage_2") and technical.get("vcp") else "Setup quality is mixed.",
        },
        "dcf": {
            "verdict": bucket(mos, 10.0, -10.0),
            "takeaway": "Valuation supports upside." if mos >= 0 else "Valuation indicates premium pricing.",
        },
        "health": {
            "verdict": "bullish" if len(health_flags) == 0 else ("bearish" if len(health_flags) >= 3 else "neutral"),
            "takeaway": "Balance sheet and margins are stable." if len(health_flags) == 0 else "Review flagged financial risks.",
        },
        "mirofish": {
            "verdict": bucket(conviction, 30.0, -30.0),
            "takeaway": (miro.get("summary") or "No sentiment synthesis available.")[:220],
        },
    }


def _sec_analysis_settings() -> dict[str, Any]:
    from config import (
        get_edgar_user_agent,
        get_sec_filing_analysis_enabled,
        get_sec_filing_cache_hours,
        get_sec_filing_compare_enabled,
        get_sec_filing_llm_summary_enabled,
        get_sec_filing_max_chars,
        get_sec_filing_max_compare_items,
    )

    return {
        "analysis_enabled": bool(get_sec_filing_analysis_enabled(SKILL_DIR)),
        "compare_enabled": bool(get_sec_filing_compare_enabled(SKILL_DIR)),
        "user_agent": get_edgar_user_agent(SKILL_DIR),
        "cache_hours": float(get_sec_filing_cache_hours(SKILL_DIR)),
        "max_chars": int(get_sec_filing_max_chars(SKILL_DIR)),
        "max_compare_items": int(get_sec_filing_max_compare_items(SKILL_DIR)),
        "llm_enabled": bool(get_sec_filing_llm_summary_enabled(SKILL_DIR)),
    }


def _scan_worker(job_id: str, scan_kwargs: dict[str, Any] | None = None) -> None:
    try:
        skw = scan_kwargs or {}
        signals, diagnostics = scan_for_signals_detailed(skill_dir=SKILL_DIR, **skw)
        diagnostics_summary = _diagnostics_summary(diagnostics, signals)
        strategy_summary = _strategy_summary(signals)
        finished_at = datetime.now(timezone.utc).isoformat()
        signals_persist = signals[:_LAST_SCAN_SIGNALS_CAP]
        last_scan = {
            "at": finished_at,
            "signals_found": len(signals),
            "signals": signals_persist,
            "diagnostics": diagnostics,
            "diagnostics_summary": diagnostics_summary,
            "strategy_summary": strategy_summary,
        }
        db = SessionLocal()
        try:
            _save_state(db, "last_scan", last_scan)
        finally:
            db.close()
        with _scan_lock:
            if _scan_job.get("job_id") == job_id:
                _scan_job.update(
                    {
                        "status": "completed",
                        "finished_at": finished_at,
                        "signals_found": len(signals),
                        "diagnostics": diagnostics,
                        "diagnostics_summary": diagnostics_summary,
                        "strategy_summary": strategy_summary,
                        "signals": signals,
                        "error": None,
                    }
                )
    except Exception as e:
        with _scan_lock:
            if _scan_job.get("job_id") == job_id:
                _scan_job.update(
                    {
                        "status": "failed",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "error": str(e),
                    }
                )
        _record_endpoint_error("scan_worker")


def _load_state(db: Session, key: str, default: dict[str, Any]) -> dict[str, Any]:
    row = (
        db.query(AppState)
        .filter(AppState.user_id == LOCAL_DASHBOARD_USER_ID, AppState.key == key)
        .first()
    )
    if not row:
        return default
    try:
        data = json.loads(row.value_json or "{}")
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def _save_state(db: Session, key: str, value: dict[str, Any]) -> None:
    row = (
        db.query(AppState)
        .filter(AppState.user_id == LOCAL_DASHBOARD_USER_ID, AppState.key == key)
        .first()
    )
    if not row:
        row = AppState(
            user_id=LOCAL_DASHBOARD_USER_ID,
            key=key,
            value_json=json.dumps(value, default=_json_default),
        )
        db.add(row)
    else:
        row.value_json = json.dumps(value, default=_json_default)
    db.commit()


def _audit_event(
    event: str,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "actor": actor,
        "payload": payload or {},
    }
    try:
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=_json_default) + "\n")
    except Exception as e:
        LOG.warning("Audit write failed: %s", e)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_trade_api_key(
    x_api_key: str | None = Header(default=None),
    x_user: str | None = Header(default=None),
) -> dict[str, str]:
    configured = os.getenv("WEB_API_KEY", "").strip()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="WEB_API_KEY is not configured on the server.",
        )
    if not x_api_key or x_api_key != configured:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")
    return {"actor": (x_user or "web-user").strip() or "web-user"}


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


@app.get("/api/health", response_model=ApiResponse)
def health() -> ApiResponse:
    return _ok({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})


@app.get("/api/public-config", response_model=ApiResponse)
def public_config() -> ApiResponse:
    """Non-secret client config (optional Supabase browser sign-in)."""
    url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    anon = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
    supabase: dict[str, str] | None = None
    if url and anon:
        supabase = {"url": url, "anon_key": anon}
    plat_kill = (os.getenv("LIVE_TRADING_KILL_SWITCH") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    data: dict[str, Any] = {
        "supabase": supabase,
        "saas_mode": False,
        "schwab_oauth": False,
        "platform_live_trading_kill_switch": plat_kill,
    }
    impl = (os.getenv("WEB_IMPLEMENTATION_GUIDE_URL") or "").strip()
    if impl.startswith(("http://", "https://")):
        data["implementation_guide_url"] = impl
    return _ok(data)


@app.get("/api/health/deep", response_model=ApiResponse)
def health_deep(db: Session = Depends(get_db)) -> ApiResponse:
    try:
        db_ok = bool(db.query(PendingTrade).limit(1).all() is not None)
        auth = DualSchwabAuth(skill_dir=SKILL_DIR)
        market_token_ok = bool(auth.get_market_token())
        account_token_ok = bool(auth.get_account_token())
        quote, qmeta = get_current_quote_with_status("AAPL", auth=auth, skill_dir=SKILL_DIR)
        quote_ok = extract_schwab_last_price(quote) is not None
        with _metrics_lock:
            metrics = json.loads(json.dumps(_request_metrics))
        qh = {
            "symbol": qmeta.get("symbol"),
            "ok": quote_ok,
            "reason": None if quote_ok else (qmeta.get("reason") or "unknown"),
            "operator_hint": _quote_health_hint(qmeta, quote_ok),
            "http_status": qmeta.get("http_status"),
            "top_level_keys": qmeta.get("top_level_keys"),
            "quote_keys": qmeta.get("quote_keys"),
        }
        if not quote_ok and qmeta.get("error_detail"):
            qh["error_detail"] = str(qmeta["error_detail"])[:400]
        return _ok(
            {
                "db_ok": db_ok,
                "market_token_ok": market_token_ok,
                "account_token_ok": account_token_ok,
                "quote_ok": quote_ok,
                "quote_health": qh,
                "metrics": metrics,
            }
        )
    except Exception as e:
        return _err("health_deep", e)


@app.get("/api/config", response_model=ApiResponse)
def config() -> ApiResponse:
    return _ok(
        {
            "trade_api_key_required": bool(os.getenv("WEB_API_KEY", "").strip()),
            "allowed_origins": allowed_origins,
        }
    )


@app.get("/api/status", response_model=ApiResponse)
def status(db: Session = Depends(get_db)) -> ApiResponse:
    try:
        auth = DualSchwabAuth(skill_dir=SKILL_DIR)
        checked_at = datetime.now(timezone.utc).isoformat()
        market_token_ok = bool(auth.get_market_token())
        account_token_ok = bool(auth.get_account_token())
        market_state = "Connected" if market_token_ok else "Disconnected"
        account_state = "Connected" if account_token_ok else "Disconnected"
        last_scan = _load_state(
            db,
            key="last_scan",
            default={
                "at": None,
                "signals_found": None,
                "signals": [],
                "diagnostics": None,
                "diagnostics_summary": None,
                "strategy_summary": None,
            },
        )
        return _ok(
            {
                "market_token_ok": market_token_ok,
                "account_token_ok": account_token_ok,
                "market_state": market_state,
                "account_state": account_state,
                "checked_at": checked_at,
                "last_scan": last_scan,
                "validation_status": _latest_validation_status(),
            }
        )
    except Exception as e:
        return _err("status", e)


@app.get("/api/validation/status", response_model=ApiResponse)
def validation_status() -> ApiResponse:
    try:
        return _ok(_latest_validation_status())
    except Exception as e:
        return _err("validation_status", e)


@app.post("/api/scan", response_model=ApiResponse)
def scan(
    async_mode: bool = True,
    db: Session = Depends(get_db),
    body: dict[str, Any] | None = Body(default=None),
) -> ApiResponse:
    try:
        try:
            parsed_scan = parse_scan_run_body(body)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        skw = scan_runtime_kwargs(parsed_scan)

        if async_mode:
            started = False
            with _scan_lock:
                if _scan_job.get("status") == "running":
                    pass
                else:
                    job_id = uuid.uuid4().hex[:10]
                    _scan_job.update(
                        {
                            "job_id": job_id,
                            "status": "running",
                            "started_at": datetime.now(timezone.utc).isoformat(),
                            "finished_at": None,
                            "signals_found": None,
                            "diagnostics": None,
                            "diagnostics_summary": None,
                            "strategy_summary": None,
                            "signals": [],
                            "error": None,
                        }
                    )
                    started = True
            if started:
                thread = threading.Thread(
                    target=_scan_worker,
                    args=(job_id, skw),
                    daemon=True,
                    name=f"scan-{job_id}",
                )
                thread.start()
            snapshot = _scan_snapshot()
            return _ok({"started": started, **snapshot})

        signals, diagnostics = scan_for_signals_detailed(skill_dir=SKILL_DIR, **skw)
        diagnostics_summary = _diagnostics_summary(diagnostics, signals)
        strategy_summary = _strategy_summary(signals)
        now_iso = datetime.now(timezone.utc).isoformat()
        signals_persist = signals[:_LAST_SCAN_SIGNALS_CAP]
        last_scan = {
            "at": now_iso,
            "signals_found": len(signals),
            "signals": signals_persist,
            "diagnostics": diagnostics,
            "diagnostics_summary": diagnostics_summary,
            "strategy_summary": strategy_summary,
        }
        _save_state(db, "last_scan", last_scan)
        return _ok(
            {
                "signals_found": len(signals),
                "signals": signals,
                "diagnostics": diagnostics,
                "diagnostics_summary": diagnostics_summary,
                "strategy_summary": strategy_summary,
            }
        )
    except Exception as e:
        return _err("scan", e)


@app.get("/api/scan/status", response_model=ApiResponse)
def scan_status(db: Session = Depends(get_db)) -> ApiResponse:
    snapshot = _scan_snapshot()
    if snapshot.get("status") == "idle":
        last_scan = _load_state(
            db,
            key="last_scan",
            default={
                "at": None,
                "signals_found": None,
                "signals": [],
                "diagnostics": None,
                "diagnostics_summary": None,
                "strategy_summary": None,
            },
        )
        return _ok({"status": "idle", "last_scan": last_scan})
    return _ok(snapshot)


@app.get("/api/check/{ticker}", response_model=ApiResponse)
def check_ticker(ticker: str) -> ApiResponse:
    try:
        data = quick_check(ticker.upper().strip())
        return _ok(data)
    except Exception as e:
        return _err("check", e)


@app.get("/api/report/{ticker}", response_model=ApiResponse)
def report_ticker(
    ticker: str,
    section: str | None = None,
    skip_mirofish: bool = False,
    skip_edgar: bool = False,
) -> ApiResponse:
    try:
        section_key = None
        if section:
            section_key = REPORT_SECTION_MAP.get(section.lower().strip())
            if not section_key:
                _record_endpoint_error("report")
                return ApiResponse(ok=False, error=f"Invalid section '{section}'. Use: tech, dcf, comps, health, edgar, mirofish.")

        report = generate_full_report(
            ticker=ticker.upper().strip(),
            skip_mirofish=skip_mirofish,
            skip_edgar=skip_edgar,
        )
        data = json.loads(report_to_json(report))
        section_verdicts = _build_report_verdicts(data)
        if section_key:
            section_data = data.get(section_key)
            return _ok(
                {
                    "ticker": data.get("ticker"),
                    "generated_at": data.get("generated_at"),
                    "section": section_key,
                    "data": section_data,
                    "section_verdicts": section_verdicts,
                    "section_quick_verdict": section_verdicts.get(section_key, {}),
                }
            )
        data["section_verdicts"] = section_verdicts
        return _ok(data)
    except Exception as e:
        return _err("report", e)


@app.get("/api/sec/analyze/{ticker}", response_model=ApiResponse)
def sec_analyze_ticker(
    ticker: str,
    form_type: str = "10-K",
) -> ApiResponse:
    try:
        cfg = _sec_analysis_settings()
        if not cfg["analysis_enabled"]:
            return ApiResponse(ok=False, error="SEC filing analysis is disabled by configuration.")
        out = analyze_latest_filing_for_ticker(
            ticker=ticker.upper().strip(),
            form_type=form_type.upper().strip(),
            user_agent=cfg["user_agent"],
            skill_dir=SKILL_DIR,
            cache_hours=cfg["cache_hours"],
            max_chars=cfg["max_chars"],
            enable_llm=cfg["llm_enabled"],
        )
        if not out.get("ok"):
            _record_endpoint_error("sec_analyze")
            return ApiResponse(ok=False, error=str(out.get("error", "SEC analysis failed")))
        return _ok(out)
    except Exception as e:
        return _err("sec_analyze", e)


@app.get("/sec/analyze/{ticker}", response_model=ApiResponse)
def sec_analyze_ticker_alias(
    ticker: str,
    form_type: str = "10-K",
) -> ApiResponse:
    return sec_analyze_ticker(ticker=ticker, form_type=form_type)


@app.get("/api/sec/compare", response_model=ApiResponse)
def sec_compare(
    mode: str = "ticker_vs_ticker",
    ticker: str = "",
    ticker_b: str = "",
    form_type: str = "10-K",
    highlight_changes_only: bool = False,
) -> ApiResponse:
    try:
        cfg = _sec_analysis_settings()
        if not cfg["analysis_enabled"]:
            return ApiResponse(ok=False, error="SEC filing analysis is disabled by configuration.")
        if not cfg["compare_enabled"]:
            return ApiResponse(ok=False, error="SEC filing compare is disabled by configuration.")
        safe_mode = mode.strip().lower()
        safe_form = form_type.upper().strip()
        safe_ticker = ticker.upper().strip()
        safe_ticker_b = ticker_b.upper().strip()
        if cfg["max_compare_items"] < 2:
            return ApiResponse(ok=False, error="SEC compare limit is below required minimum.")

        if safe_mode == "ticker_vs_ticker":
            if not safe_ticker or not safe_ticker_b:
                return ApiResponse(ok=False, error="ticker and ticker_b are required for ticker_vs_ticker mode.")
            out = compare_ticker_vs_ticker(
                safe_ticker,
                safe_ticker_b,
                form_type=safe_form,
                user_agent=cfg["user_agent"],
                skill_dir=SKILL_DIR,
                cache_hours=cfg["cache_hours"],
                max_chars=cfg["max_chars"],
                enable_llm=cfg["llm_enabled"],
                highlight_changes_only=bool(highlight_changes_only),
            )
        elif safe_mode == "ticker_over_time":
            if not safe_ticker:
                return ApiResponse(ok=False, error="ticker is required for ticker_over_time mode.")
            out = compare_ticker_over_time(
                safe_ticker,
                form_type=safe_form,
                user_agent=cfg["user_agent"],
                skill_dir=SKILL_DIR,
                cache_hours=cfg["cache_hours"],
                max_chars=cfg["max_chars"],
                enable_llm=cfg["llm_enabled"],
                highlight_changes_only=bool(highlight_changes_only),
            )
        else:
            return ApiResponse(ok=False, error="Invalid mode. Use ticker_vs_ticker or ticker_over_time.")

        if not out.get("ok"):
            _record_endpoint_error("sec_compare")
            return ApiResponse(ok=False, error=str(out.get("error", "SEC compare failed")))
        compare_data = out.get("compare")
        if isinstance(compare_data, dict):
            similarities = compare_data.get("similarities") or []
            differences = compare_data.get("differences") or []
            investor_takeaway = str(compare_data.get("investor_takeaway") or "").strip()
            compare_data.setdefault(
                "summary_headline",
                "SEC compare completed with meaningful differences." if differences else "SEC compare completed with broad alignment.",
            )
            compare_data.setdefault(
                "narrative_summary",
                (
                    f"{investor_takeaway} "
                    f"Shared signal: {(similarities[0] if similarities else 'limited overlap noted.')}. "
                    f"Key difference: {(differences[0] if differences else 'no major contrast highlighted.')}."
                ).strip(),
            )
            compare_data.setdefault("top_differences", differences[:3])
            compare_data.setdefault("top_commonalities", similarities[:3])
        return _ok(out)
    except Exception as e:
        return _err("sec_compare", e)


@app.get("/sec/compare", response_model=ApiResponse)
def sec_compare_alias(
    mode: str = "ticker_vs_ticker",
    ticker: str = "",
    ticker_b: str = "",
    form_type: str = "10-K",
    highlight_changes_only: bool = False,
) -> ApiResponse:
    return sec_compare(
        mode=mode,
        ticker=ticker,
        ticker_b=ticker_b,
        form_type=form_type,
        highlight_changes_only=highlight_changes_only,
    )


@app.get("/api/portfolio", response_model=ApiResponse)
def portfolio() -> ApiResponse:
    try:
        auth = DualSchwabAuth(skill_dir=SKILL_DIR)
        status_data = get_account_status(auth=auth, skill_dir=SKILL_DIR)
        if isinstance(status_data, str):
            _record_endpoint_error("portfolio")
            return ApiResponse(ok=False, error=status_data)
        return _ok(_build_portfolio_summary(status_data))
    except Exception as e:
        return _err("portfolio", e)


@app.get("/api/sectors", response_model=ApiResponse)
def sectors() -> ApiResponse:
    try:
        auth = DualSchwabAuth(skill_dir=SKILL_DIR)
        heatmap = get_sector_heatmap(auth=auth, skill_dir=SKILL_DIR)
        return _ok(heatmap)
    except Exception as e:
        return _err("sectors", e)


@app.get("/api/pending-trades", response_model=ApiResponse)
def list_pending_trades(
    status: str | None = None,
    sort: str = "newest",
    db: Session = Depends(get_db),
) -> ApiResponse:
    rows_query = db.query(PendingTrade).filter(PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID)
    if status and status.lower() != "all":
        rows_query = rows_query.filter(PendingTrade.status == status.lower().strip())
    if sort == "oldest":
        rows_query = rows_query.order_by(PendingTrade.created_at.asc())
    else:
        rows_query = rows_query.order_by(PendingTrade.created_at.desc())
    rows = rows_query.all()
    return _ok([_trade_to_dict(r) for r in rows])


@app.post("/api/pending-trades", response_model=ApiResponse)
def create_pending_trade(payload: CreatePendingTrade, db: Session = Depends(get_db)) -> ApiResponse:
    try:
        ticker = payload.ticker.upper().strip()
        signal = payload.signal or {}

        auth = DualSchwabAuth(skill_dir=SKILL_DIR)
        quote = get_current_quote(ticker, auth=auth, skill_dir=SKILL_DIR)
        last_price = payload.price or extract_schwab_last_price(quote) or float(signal.get("price", 0) or 0)

        qty = payload.qty
        if qty is None:
            usd_size = get_position_size_usd(
                ticker=ticker,
                price=last_price if last_price > 0 else None,
                skill_dir=SKILL_DIR,
            )
            qty = max(1, int(usd_size / last_price)) if last_price > 0 else 1

        trade_id = uuid.uuid4().hex[:8]
        row = PendingTrade(
            id=trade_id,
            user_id=LOCAL_DASHBOARD_USER_ID,
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
        _audit_event("pending_trade_created", "system", {"trade_id": trade_id, "ticker": ticker, "qty": qty})
        return _ok(_trade_to_dict(row))
    except Exception as e:
        return _err("create_pending_trade", e)


@app.post("/api/pending-trades/clear-pending", response_model=ApiResponse)
def clear_all_pending_trades(
    auth_ctx: dict[str, str] = Depends(require_trade_api_key),
    db: Session = Depends(get_db),
) -> ApiResponse:
    rows = (
        db.query(PendingTrade)
        .filter(PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID, PendingTrade.status == "pending")
        .all()
    )
    cleared_ids = [r.id for r in rows]
    for row in rows:
        row.status = "rejected"
    db.commit()
    actor = auth_ctx.get("actor", "web-user")
    if cleared_ids:
        _audit_event(
            "pending_trades_cleared",
            actor,
            {"cleared": len(cleared_ids), "trade_ids": cleared_ids},
        )
    return _ok({"cleared": len(cleared_ids)})


@app.post("/api/trades/{trade_id}/approve", response_model=ApiResponse)
def approve_trade(
    trade_id: str,
    payload: ApproveTradeRequest,
    confirm_live: bool = False,
    auth_ctx: dict[str, str] = Depends(require_trade_api_key),
    db: Session = Depends(get_db),
) -> ApiResponse:
    row = (
        db.query(PendingTrade)
        .filter(PendingTrade.id == trade_id, PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID)
        .first()
    )
    if not row:
        _record_endpoint_error("approve_trade")
        return ApiResponse(ok=False, error="Trade not found.")
    if row.status != "pending":
        _record_endpoint_error("approve_trade")
        return ApiResponse(ok=False, error=f"Trade already {row.status}.")

    typed = (payload.typed_ticker or "").strip().upper()
    if typed != row.ticker.upper():
        _record_endpoint_error("approve_trade")
        return ApiResponse(
            ok=False,
            error="typed_ticker must exactly match the staged trade ticker (re-type to confirm the live order).",
        )

    signal = json.loads(row.signal_json or "{}")
    ui_settings = _load_ui_settings(db)
    automation_opt_in = bool(ui_settings.get("automation_opt_in", DEFAULT_AUTOMATION_OPT_IN))
    if not automation_opt_in and not confirm_live:
        checklist = _build_pretrade_checklist(row, signal)
        return ApiResponse(
            ok=False,
            error="Explicit live confirmation required. Review checklist and retry with confirm_live=true.",
            data={"checklist": checklist, "automation_opt_in": automation_opt_in},
        )

    result = place_order(
        ticker=row.ticker,
        qty=row.qty,
        side="BUY",
        order_type="MARKET",
        price_hint=row.price,
        mirofish_conviction=signal.get("mirofish_conviction"),
        sector_etf=signal.get("sector_etf"),
        skill_dir=SKILL_DIR,
    )

    actor = auth_ctx.get("actor", "web-user")
    if isinstance(result, str):
        row.status = "failed"
        row.note = (row.note or "") + f" | {result}" if row.note else result
        db.commit()
        db.refresh(row)
        _audit_event(
            "trade_approve_failed",
            actor,
            {"trade": _trade_to_dict(row), "error": result},
        )
        _record_endpoint_error("approve_trade")
        return ApiResponse(
            ok=False,
            error=result,
            data={
                "trade": _trade_to_dict(row),
                "recovery": _map_failure(result, source="execution"),
            },
        )

    row.status = "executed"
    db.commit()
    db.refresh(row)
    _audit_event(
        "trade_approved",
        actor,
        {"trade": _trade_to_dict(row), "result": result},
    )
    return _ok({"trade": _trade_to_dict(row), "result": result})


@app.post("/api/trades/{trade_id}/reject", response_model=ApiResponse)
def reject_trade(
    trade_id: str,
    auth_ctx: dict[str, str] = Depends(require_trade_api_key),
    db: Session = Depends(get_db),
) -> ApiResponse:
    row = (
        db.query(PendingTrade)
        .filter(PendingTrade.id == trade_id, PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID)
        .first()
    )
    if not row:
        _record_endpoint_error("reject_trade")
        return ApiResponse(ok=False, error="Trade not found.")
    if row.status != "pending":
        _record_endpoint_error("reject_trade")
        return ApiResponse(ok=False, error=f"Trade already {row.status}.")
    row.status = "rejected"
    db.commit()
    db.refresh(row)
    _audit_event("trade_rejected", auth_ctx.get("actor", "web-user"), {"trade": _trade_to_dict(row)})
    return _ok(_trade_to_dict(row))


@app.get("/api/trades/{trade_id}/preflight", response_model=ApiResponse)
def preflight_trade(trade_id: str, db: Session = Depends(get_db)) -> ApiResponse:
    row = (
        db.query(PendingTrade)
        .filter(PendingTrade.id == trade_id, PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID)
        .first()
    )
    if not row:
        return ApiResponse(ok=False, error="Trade not found.")
    signal = json.loads(row.signal_json or "{}")
    return _ok(
        {
            "trade": _trade_to_dict(row),
            "checklist": _build_pretrade_checklist(row, signal if isinstance(signal, dict) else {}),
        }
    )


@app.get("/api/recovery/map", response_model=ApiResponse)
def map_recovery(error: str, source: str = "unknown") -> ApiResponse:
    return _ok(_map_failure(error, source=source))


@app.get("/api/settings/profiles", response_model=ApiResponse)
def get_profiles(expert: bool = False, db: Session = Depends(get_db)) -> ApiResponse:
    settings = _load_ui_settings(db)
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


@app.post("/api/settings/profile", response_model=ApiResponse)
def set_profile(
    profile: str = DEFAULT_PROFILE,
    mode: str = DEFAULT_UI_MODE,
    automation_opt_in: bool = False,
    db: Session = Depends(get_db),
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
    _save_state(db, "ui_settings", settings)
    _audit_event("settings_profile_applied", "web-user", {"profile": p, "mode": mode_n, "automation_opt_in": bool(automation_opt_in)})
    return _ok({"settings": settings, "runtime_overrides": runtime})


@app.post("/api/onboarding/start", response_model=ApiResponse)
def onboarding_start(db: Session = Depends(get_db)) -> ApiResponse:
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
    _save_state(db, "onboarding", state)
    return _ok(state)


@app.post("/api/onboarding/step/{step}", response_model=ApiResponse)
def onboarding_step(step: str, db: Session = Depends(get_db)) -> ApiResponse:
    current = _load_state(
        db,
        key="onboarding",
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
        health = _token_health()
        ok = bool(health["market_token_file"] and health["account_token_file"])
        steps["connect"] = {
            "ok": ok,
            "at": now_iso,
            "details": health,
            "fix_path": "Run `python run_auth.py` (or dual auth flow), then rerun this step.",
        }
    elif step_key == "verify_token_health":
        try:
            auth = DualSchwabAuth(skill_dir=SKILL_DIR)
            market_ok = bool(auth.get_market_token())
            account_ok = bool(auth.get_account_token())
            quote = get_current_quote("AAPL", auth=auth, skill_dir=SKILL_DIR)
            quote_ok = extract_schwab_last_price(quote) is not None
            ok = market_ok and account_ok and quote_ok
            steps["verify_token_health"] = {
                "ok": ok,
                "at": now_iso,
                "details": {
                    "market_token_ok": market_ok,
                    "account_token_ok": account_ok,
                    "quote_ok": quote_ok,
                },
                "fix_path": "Run `python healthcheck.py` and follow repair steps if checks fail.",
            }
        except Exception as e:
            steps["verify_token_health"] = {
                "ok": False,
                "at": now_iso,
                "details": {"error": str(e)},
                "recovery": _map_failure(str(e), source="schwab_auth"),
            }
    elif step_key == "test_scan":
        try:
            signals, diagnostics = scan_for_signals_detailed(skill_dir=SKILL_DIR)
            ok = diagnostics.get("scan_blocked", 0) == 0 and diagnostics.get("exceptions", 0) == 0
            steps["test_scan"] = {
                "ok": bool(ok),
                "at": now_iso,
                "details": {
                    "signals_found": len(signals),
                    "diagnostics_summary": _diagnostics_summary(diagnostics, signals),
                },
                "fix_path": "Retry scan and review blockers list if no signals are produced.",
            }
        except Exception as e:
            steps["test_scan"] = {
                "ok": False,
                "at": now_iso,
                "details": {"error": str(e)},
                "recovery": _map_failure(str(e), source="signal_scanner"),
            }
    elif step_key == "test_paper_order":
        previous_shadow = os.environ.get("EXECUTION_SHADOW_MODE")
        os.environ["EXECUTION_SHADOW_MODE"] = "1"
        try:
            auth = DualSchwabAuth(skill_dir=SKILL_DIR)
            quote = get_current_quote("AAPL", auth=auth, skill_dir=SKILL_DIR)
            price = extract_schwab_last_price(quote) or 100.0
            result = place_order(
                ticker="AAPL",
                qty=1,
                side="BUY",
                order_type="MARKET",
                price_hint=price,
                skill_dir=SKILL_DIR,
            )
            ok = isinstance(result, dict) and bool(result.get("shadow_mode"))
            steps["test_paper_order"] = {
                "ok": ok,
                "at": now_iso,
                "details": result if isinstance(result, dict) else {"result": result},
                "fix_path": "Keep execution in shadow mode and retry the paper-order test.",
            }
        except Exception as e:
            steps["test_paper_order"] = {
                "ok": False,
                "at": now_iso,
                "details": {"error": str(e)},
                "recovery": _map_failure(str(e), source="execution"),
            }
        finally:
            if previous_shadow is None:
                os.environ.pop("EXECUTION_SHADOW_MODE", None)
            else:
                os.environ["EXECUTION_SHADOW_MODE"] = previous_shadow
    else:
        return ApiResponse(ok=False, error="Unknown onboarding step.")

    _save_state(db, "onboarding", current)
    return _ok(current)


@app.get("/api/onboarding/status", response_model=ApiResponse)
def onboarding_status(db: Session = Depends(get_db)) -> ApiResponse:
    current = _load_state(
        db,
        key="onboarding",
        default={
            "started_at": None,
            "target_minutes": ONBOARDING_TARGET_MINUTES,
            "steps": {
                "connect": {"ok": False},
                "verify_token_health": {"ok": False},
                "test_scan": {"ok": False},
                "test_paper_order": {"ok": False},
            },
        },
    )
    started_at = current.get("started_at")
    elapsed_minutes = None
    if isinstance(started_at, str) and started_at:
        try:
            dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            elapsed_minutes = round((datetime.now(timezone.utc) - dt).total_seconds() / 60.0, 1)
        except Exception:
            elapsed_minutes = None
    steps = current.get("steps") if isinstance(current.get("steps"), dict) else {}
    completion = (
        bool((steps.get("connect") or {}).get("ok"))
        and bool((steps.get("verify_token_health") or {}).get("ok"))
        and bool((steps.get("test_scan") or {}).get("ok"))
        and bool((steps.get("test_paper_order") or {}).get("ok"))
    )
    return _ok(
        {
            **current,
            "elapsed_minutes": elapsed_minutes,
            "target_minutes": current.get("target_minutes", ONBOARDING_TARGET_MINUTES),
            "completed_under_target": bool(completion and elapsed_minutes is not None and elapsed_minutes <= ONBOARDING_TARGET_MINUTES),
        }
    )


@app.get("/api/decision-card/{ticker}", response_model=ApiResponse)
def decision_card(ticker: str, db: Session = Depends(get_db)) -> ApiResponse:
    symbol = ticker.upper().strip()
    signal = None
    with _scan_lock:
        for row in _scan_job.get("signals") or []:
            if str((row or {}).get("ticker", "")).upper() == symbol:
                signal = row
                break
    if signal is None:
        return ApiResponse(ok=False, error=f"{symbol} is not in current scan results. Run scan first.")

    price = float(signal.get("price", 0) or 0)
    size_usd = get_position_size_usd(ticker=symbol, price=price if price > 0 else None, skill_dir=SKILL_DIR)
    qty = max(1, int(size_usd / price)) if price > 0 else 1
    stop_pct = max(0.03, min(0.15, 0.07))
    stop_level = round(price * (1.0 - stop_pct), 2) if price > 0 else None
    entry_zone = (
        {"low": round(price * 0.995, 2), "high": round(price * 1.005, 2)}
        if price > 0
        else {"low": None, "high": None}
    )
    confidence_bucket = str(((signal.get("advisory") or {}).get("confidence_bucket") or "unknown")).lower()
    score = float(signal.get("signal_score", 0) or 0)
    conviction = signal.get("mirofish_conviction")
    reasons = [
        f"signal_score={score:.1f}",
        f"confidence={confidence_bucket}",
        f"strategy={((signal.get('strategy_attribution') or {}).get('top_live') or 'unknown')}",
    ]
    if conviction is not None:
        reasons.append(f"mirofish_conviction={conviction}")
    if signal.get("event_risk", {}).get("flagged"):
        reasons.append(f"event_risk={','.join(signal.get('event_risk', {}).get('reasons', []))}")

    mock_trade = PendingTrade(id="preview", ticker=symbol, qty=qty, price=price, status="pending", signal_json=json.dumps(signal), note=None)
    checklist = _build_pretrade_checklist(mock_trade, signal)

    return _ok(
        {
            "ticker": symbol,
            "entry_zone": entry_zone,
            "stop_invalidation": stop_level,
            "size": {"qty": qty, "usd": size_usd},
            "confidence": {
                "bucket": confidence_bucket,
                "signal_score": score,
                "mirofish_conviction": conviction,
            },
            "key_reasons": reasons[:6],
            "block_reason": (checklist.get("block_reasons") or [None])[0],
            "checklist": checklist,
        }
    )


@app.get("/api/performance", response_model=ApiResponse)
def performance() -> ApiResponse:
    backtest = _read_json_file(BACKTEST_RESULTS_PATH, {})
    outcomes = _read_json_file(TRADE_OUTCOMES_PATH, [])
    metrics = _read_json_file(EXECUTION_METRICS_PATH, {"days": {}})
    days = metrics.get("days", {}) if isinstance(metrics, dict) else {}

    shadow_actions = 0
    live_actions = 0
    for bucket in days.values() if isinstance(days, dict) else []:
        events = (bucket or {}).get("events", {}) if isinstance(bucket, dict) else {}
        shadow_actions += int(events.get("action_shadow", 0) or 0)
        live_actions += int(events.get("action_live", 0) or 0)

    total_outcomes = len(outcomes) if isinstance(outcomes, list) else 0
    return _ok(
        {
            "backtest": {
                "source": str(BACKTEST_RESULTS_PATH.name),
                "run_at": backtest.get("run_at") if isinstance(backtest, dict) else None,
                "total_trades": backtest.get("total_trades") if isinstance(backtest, dict) else None,
                "win_rate": backtest.get("win_rate_net") if isinstance(backtest, dict) else None,
                "avg_return_pct": backtest.get("avg_return_net_pct") if isinstance(backtest, dict) else None,
                "max_drawdown_pct": backtest.get("max_drawdown_net_pct") if isinstance(backtest, dict) else None,
            },
            "shadow_paper": {
                "source": "execution_safety_metrics.json",
                "shadow_actions": shadow_actions,
                "notes": "Derived from shadow execution event counters.",
            },
            "live": {
                "source": ".trade_outcomes.json",
                "live_actions": live_actions,
                "recorded_outcomes": total_outcomes,
                "latest_outcomes": (outcomes[-5:] if isinstance(outcomes, list) else []),
            },
            "validation": {
                "status": _latest_validation_status(),
                "artifacts_present": VALIDATION_ARTIFACT_DIR.exists(),
            },
            "separation_guard": {
                "commingled_metric_allowed": False,
                "message": "Backtest, shadow/paper, and live are reported as separate buckets only.",
            },
        }
    )


@app.get("/api/calibration/summary", response_model=ApiResponse)
def api_calibration_summary() -> ApiResponse:
    return _ok(build_calibration_snapshot(SKILL_DIR))

