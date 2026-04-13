from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from celery import Celery

from execution import place_order
from signal_scanner import scan_for_signals_detailed

from .billing_stripe import user_has_paid_entitlement
from .calibration_snapshot import build_calibration_snapshot
from .db import SessionLocal
from .models import AppState, BacktestRun, Order, ScanResult, User
from .scan_payload import scan_runtime_kwargs
from .tenant_runtime import tenant_skill_dir

LOG = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "tradingbot_webapp",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

_celery_conf: dict[str, Any] = {
    "task_serializer": "json",
    "accept_content": ["json"],
    "result_serializer": "json",
    "timezone": "UTC",
    "task_routes": {
        "webapp.scan_for_user": {"queue": "scan"},
        "webapp.execute_order_for_user": {"queue": "orders"},
        "webapp.backtest_for_user": {"queue": "scan"},
    },
    "task_default_queue": "celery",
}
# Low-memory hosts (e.g. 512MB): set CELERY_WORKER_POOL=solo so one process loads pandas/scanner.
_pool = (os.getenv("CELERY_WORKER_POOL") or "").strip().lower()
if _pool:
    _celery_conf["worker_pool"] = _pool
_conc = (os.getenv("CELERY_WORKER_CONCURRENCY") or "").strip()
if _conc:
    try:
        _celery_conf["worker_concurrency"] = max(1, int(_conc))
    except ValueError:
        pass
celery_app.conf.update(**_celery_conf)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _strategy_summary_from_signals(signals: list[dict[str, Any]]) -> dict[str, Any]:
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


def _persist_calibration_snapshot(db: Any, user_id: str, skill_dir: Any) -> None:
    try:
        snap = build_calibration_snapshot(skill_dir)
        if not snap.get("self_study") and not snap.get("hypothesis_ledger"):
            return
        row = (
            db.query(AppState)
            .filter(AppState.user_id == user_id, AppState.key == "calibration_snapshot")
            .first()
        )
        blob = json.dumps(snap, default=_json_default)
        if not row:
            db.add(AppState(user_id=user_id, key="calibration_snapshot", value_json=blob))
        else:
            row.value_json = blob
        db.commit()
    except Exception as exc:
        LOG.debug("calibration snapshot persist skipped: %s", exc)
        db.rollback()


def _persist_user_last_scan(
    db: Any,
    user_id: str,
    job_id: str,
    signals_found: int,
    diagnostics: dict[str, Any],
    strategy_summary: dict[str, Any] | None = None,
) -> None:
    import json

    summary_keys = (
        "watchlist_size",
        "stage2_fail",
        "vcp_fail",
        "exceptions",
        "scan_blocked",
    )
    diagnostics_summary = {k: diagnostics.get(k) for k in summary_keys}
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "signals_found": signals_found,
        "diagnostics": diagnostics,
        "diagnostics_summary": diagnostics_summary,
        "strategy_summary": strategy_summary,
    }
    row = db.query(AppState).filter(AppState.user_id == user_id, AppState.key == "last_scan").first()
    blob = json.dumps(payload, default=_json_default)
    if not row:
        db.add(AppState(user_id=user_id, key="last_scan", value_json=blob))
    else:
        row.value_json = blob
    db.commit()


@celery_app.task(name="webapp.scan_for_user")
def scan_for_user(user_id: str, scan_options: dict[str, Any] | None = None) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user_has_paid_entitlement(user):
            return {"ok": False, "job_id": job_id, "error": "Active subscription required."}
        opts = scan_options if isinstance(scan_options, dict) else {}
        skw = scan_runtime_kwargs(opts)
        with tenant_skill_dir(db, user_id) as skill_dir:
            signals, diagnostics = scan_for_signals_detailed(skill_dir=skill_dir, **skw)
            inserted = 0
            for sig in signals:
                row = ScanResult(
                    user_id=user_id,
                    job_id=job_id,
                    ticker=str(sig.get("ticker") or sig.get("symbol") or "").upper(),
                    signal_score=(float(sig.get("signal_score")) if sig.get("signal_score") is not None else None),
                    payload_json=json.dumps(sig, default=_json_default),
                )
                db.add(row)
                inserted += 1
            db.commit()
            strat = _strategy_summary_from_signals(signals)
            try:
                _persist_user_last_scan(
                    db,
                    user_id,
                    job_id,
                    inserted,
                    diagnostics if isinstance(diagnostics, dict) else {},
                    strategy_summary=strat,
                )
            except Exception as persist_exc:
                LOG.exception(
                    "last_scan persist failed user_id=%s job_id=%s: %s",
                    user_id,
                    job_id,
                    persist_exc,
                )
            try:
                _persist_calibration_snapshot(db, user_id, skill_dir)
            except Exception as cal_exc:
                LOG.debug("calibration snapshot after scan: %s", cal_exc)
            # Celery JSON backend requires a JSON-serializable payload (no numpy, etc.).
            out = {
                "ok": True,
                "job_id": job_id,
                "signals_found": inserted,
                "diagnostics": diagnostics if isinstance(diagnostics, dict) else {},
                "strategy_summary": strat,
            }
            try:
                return json.loads(json.dumps(out, default=str))
            except Exception:
                return {
                    "ok": True,
                    "job_id": job_id,
                    "signals_found": inserted,
                    "diagnostics": {},
                }
    except Exception as exc:
        db.rollback()
        return {"ok": False, "job_id": job_id, "error": str(exc)}
    finally:
        db.close()


@celery_app.task(name="webapp.backtest_for_user")
def backtest_for_user(run_id: str, user_id: str) -> dict[str, Any]:
    from .backtest_spec import parse_strategy_spec, run_strategy_backtest

    db = SessionLocal()
    try:
        row = db.query(BacktestRun).filter(BacktestRun.id == run_id, BacktestRun.user_id == user_id).first()
        if not row:
            return {"ok": False, "error": "run not found"}
        row.status = "running"
        db.commit()
        try:
            spec = parse_strategy_spec(json.loads(row.spec_json))
        except Exception as exc:
            row.status = "failed"
            row.error_message = str(exc)
            db.commit()
            return {"ok": False, "error": str(exc), "run_id": run_id}
        try:
            with tenant_skill_dir(db, user_id) as skill_dir:
                result = run_strategy_backtest(skill_dir, spec)
            row.status = "success"
            row.result_json = json.dumps(result, default=_json_default)
            row.error_message = None
            db.commit()
            summary = {
                "total_trades": result.get("total_trades"),
                "win_rate_net": result.get("win_rate_net"),
                "total_return_net_pct": result.get("total_return_net_pct"),
                "cagr_net_pct": result.get("cagr_net_pct"),
                "max_drawdown_net_pct": result.get("max_drawdown_net_pct"),
                "findings": result.get("findings"),
            }
            return {"ok": True, "run_id": run_id, "summary": summary}
        except Exception as exc:
            row.status = "failed"
            row.error_message = str(exc)
            row.result_json = None
            db.commit()
            return {"ok": False, "error": str(exc), "run_id": run_id}
    finally:
        db.close()


@celery_app.task(name="webapp.execute_order_for_user")
def execute_order_for_user(
    user_id: str,
    ticker: str,
    qty: int,
    side: str = "BUY",
    order_type: str = "MARKET",
    price: float | None = None,
) -> dict[str, Any]:
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    if not user_has_paid_entitlement(user):
        return {"ok": False, "error": "Active subscription required."}
    if not user or not getattr(user, "live_execution_enabled", False):
        return {"ok": False, "error": "Live execution is disabled for this account."}
    order_id = uuid.uuid4().hex[:12]
    row = Order(
        id=order_id,
        user_id=user_id,
        ticker=ticker.upper().strip(),
        qty=qty,
        side=side.upper().strip(),
        order_type=order_type.upper().strip(),
        price=price,
        status="queued",
        result_json="{}",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        with tenant_skill_dir(db, user_id) as skill_dir:
            result = place_order(
                ticker=row.ticker,
                qty=row.qty,
                side=row.side,
                order_type=row.order_type,
                price_hint=row.price,
                skill_dir=skill_dir,
            )
        if isinstance(result, str):
            row.status = "failed"
            row.error_message = result
            row.result_json = json.dumps({"ok": False, "error": result})
            db.commit()
            return {"ok": False, "order_id": row.id, "error": result}

        row.status = "executed"
        row.result_json = json.dumps(result, default=_json_default)
        db.commit()
        return {"ok": True, "order_id": row.id, "result": result}
    except Exception as exc:
        db.rollback()
        row = db.query(Order).filter(Order.id == order_id).first()
        if row:
            row.status = "failed"
            row.error_message = str(exc)
            row.result_json = json.dumps({"ok": False, "error": str(exc)})
            db.commit()
        return {"ok": False, "order_id": order_id, "error": str(exc)}
    finally:
        db.close()
