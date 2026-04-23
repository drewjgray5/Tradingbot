from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from celery import Celery

from execution import place_order
from signal_scanner import scan_for_signals_detailed

from .billing_stripe import user_has_paid_entitlement
from .calibration_snapshot import build_calibration_snapshot
from .db import SessionLocal
from .learning_state import upsert_trade_outcome
from .models import AppState, BacktestRun, Order, ScanResult, User
from .redaction import safe_exception_message
from .scan_payload import scan_runtime_kwargs
from .tenant_runtime import scan_runtime_prerequisite_errors, tenant_skill_dir

LOG = logging.getLogger(__name__)

# Celery worker uses ephemeral per-tenant DualSchwabAuth (built inside scanner /
# execution call sites) — disable the SchwabSession background refresh thread
# globally to prevent orphan threads racing on Schwab's single-use refresh
# tokens (root cause of `400 unsupported_token_type` storms that degrade
# market quotes on the dashboard).
os.environ.setdefault("SCHWAB_AUTO_REFRESH", "0")

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
    # Keep broker pressure lower on small/free Redis footprints.
    "worker_prefetch_multiplier": 1,
    "broker_pool_limit": int(os.getenv("CELERY_BROKER_POOL_LIMIT", "5")),
    "result_expires": int(os.getenv("CELERY_RESULT_EXPIRES_SEC", "3600")),
    "task_routes": {
        "webapp.scan_for_user": {"queue": "scan"},
        "webapp.execute_order_for_user": {"queue": "orders"},
        "webapp.backtest_for_user": {"queue": "scan"},
        "webapp.phase2_stage1_for_user": {"queue": "phase2"},
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


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        "scan_id",
        "data_quality",
        "data_quality_reasons",
        "data_provider_primary_count",
        "data_provider_fallback_count",
        "primary_provider_filtered",
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


def _build_trade_outcome_payload(
    *,
    user_id: str,
    order_row: Order,
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    now_iso = datetime.now(timezone.utc).date().isoformat()
    safe_result = result if isinstance(result, dict) else {}
    fill_price = (
        safe_result.get("fill_price")
        or safe_result.get("average_price")
        or safe_result.get("avg_fill_price")
        or order_row.price
    )
    return {
        "source": "saas_order_execution",
        "user_id": user_id,
        "order_id": str(
            safe_result.get("orderId")
            or safe_result.get("order_id")
            or order_row.broker_order_id
            or order_row.id
        ),
        "ticker": str(order_row.ticker or "").upper(),
        "side": str(order_row.side or "BUY").upper(),
        "qty": int(order_row.qty or 0),
        "fill_price": float(fill_price) if fill_price is not None else None,
        "date": now_iso,
        "return_pct": safe_result.get("return_pct"),
        "pnl_pct": safe_result.get("pnl_pct"),
        "sector_etf": safe_result.get("sector_etf"),
        "mirofish_conviction": safe_result.get("mirofish_conviction"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def _phase2_status_key() -> str:
    return "phase2_stage1_status"


def _phase2_artifact_dir(user_id: str) -> Path:
    root_raw = (os.getenv("SAAS_PHASE2_ARTIFACT_ROOT") or "").strip()
    skill_root = Path(__file__).resolve().parent.parent
    root = Path(root_raw) if root_raw else (skill_root / "validation_artifacts" / "saas_phase2")
    safe_user = "".join(c if c.isalnum() or c in "-._" else "_" for c in str(user_id))
    return root / safe_user


def _upsert_phase2_status(db: Any, user_id: str, payload: dict[str, Any]) -> None:
    row = db.query(AppState).filter(AppState.user_id == user_id, AppState.key == _phase2_status_key()).first()
    blob = json.dumps(payload, default=_json_default)
    if not row:
        db.add(AppState(user_id=user_id, key=_phase2_status_key(), value_json=blob))
    else:
        row.value_json = blob
    db.commit()


def _phase2_stage1_overrides() -> dict[str, dict[str, str]]:
    # Mirrors the manually-vetted local settings used for Stage 1 reruns.
    return {
        "stage2_only_aug": {
            "META_POLICY_MODE": "off",
            "UNCERTAINTY_MODE": "off",
            "EVENT_RISK_MODE": "off",
            "EXIT_MANAGER_MODE": "off",
            "EXEC_QUALITY_MODE": "off",
            "QUALITY_GATES_ENABLED": "false",
            "FORENSIC_ENABLED": "false",
            "PEAD_ENABLED": "false",
            "ADVISORY_MODEL_ENABLED": "false",
            "SCAN_VCP_GATE_MODE": "shadow",
            "SCAN_SECTOR_GATE_MODE": "shadow",
            "SCAN_VCP_PENALTY_POINTS": "0",
            "SCAN_SECTOR_PENALTY_POINTS": "0",
            "SCAN_SECTOR_UNRESOLVED_PENALTY_POINTS": "0",
            "BACKTEST_AUGMENTED_LOGGING": "true",
            "BACKTEST_OHLC_PATH": "true",
        },
        "control_legacy_aug": {
            "META_POLICY_MODE": "off",
            "UNCERTAINTY_MODE": "off",
            "EVENT_RISK_MODE": "off",
            "EXIT_MANAGER_MODE": "off",
            "EXEC_QUALITY_MODE": "off",
            "BACKTEST_AUGMENTED_LOGGING": "true",
            "BACKTEST_OHLC_PATH": "true",
        },
        "control_prod_default_aug": {
            "META_POLICY_MODE": "off",
            "UNCERTAINTY_MODE": "off",
            "EVENT_RISK_MODE": "live",
            "EVENT_BLOCK_EARNINGS_DAYS": "2",
            "EVENT_ACTION": "block",
            "EXIT_MANAGER_MODE": "off",
            "EXEC_QUALITY_MODE": "live",
            "BACKTEST_AUGMENTED_LOGGING": "true",
            "BACKTEST_OHLC_PATH": "true",
        },
    }


def _write_phase2_override_files(target_dir: Path) -> dict[str, Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for run_id, payload in _phase2_stage1_overrides().items():
        p = target_dir / f"{run_id}.json"
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        out[run_id] = p
    return out


def _collect_poisoned_chunks(chunks_root: Path, run_ids: list[str]) -> list[Path]:
    bad: list[Path] = []
    for run_id in run_ids:
        base = chunks_root / run_id
        if not base.exists():
            continue
        for chunk_path in base.glob("**/chunk_[0-9]*.json"):
            if chunk_path.name.endswith("_tickers.json"):
                continue
            try:
                payload = json.loads(chunk_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            trades = payload.get("trades") or []
            excluded = int(payload.get("excluded_count", 0) or 0)
            size = int(chunk_path.stat().st_size)
            if len(trades) == 0 and excluded == 0 and size < 1024:
                bad.append(chunk_path)
    return bad


def _run_logged_subprocess(*, cmd: list[str], env: dict[str, str], cwd: Path, timeout_seconds: int) -> dict[str, Any]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=max(120, int(timeout_seconds)),
    )
    return {
        "returncode": int(proc.returncode),
        "stdout_tail": (proc.stdout or "").strip()[-4000:],
        "stderr_tail": (proc.stderr or "").strip()[-4000:],
    }


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
        runtime_errors = scan_runtime_prerequisite_errors()
        scan_env = skw.get("env_overrides")
        if isinstance(scan_env, dict) and scan_env:
            try:
                from env_overrides import temporary_env

                with temporary_env(scan_env):
                    runtime_errors = scan_runtime_prerequisite_errors()
            except Exception:
                pass
        if runtime_errors:
            return {"ok": False, "job_id": job_id, "error": "; ".join(runtime_errors)}
        with tenant_skill_dir(db, user_id) as skill_dir:
            signals, diagnostics = scan_for_signals_detailed(skill_dir=skill_dir, **skw)
            inserted = 0
            for sig in signals:
                row = ScanResult(
                    user_id=user_id,
                    job_id=job_id,
                    ticker=str(sig.get("ticker") or sig.get("symbol") or "").upper(),
                    signal_score=_optional_float(sig.get("signal_score")),
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
        return {"ok": False, "job_id": job_id, "error": safe_exception_message(exc, fallback="scan_failed")}
    finally:
        db.close()


@celery_app.task(name="webapp.phase2_stage1_for_user")
def phase2_stage1_for_user(user_id: str) -> dict[str, Any]:
    """
    Hosted replay bootstrap: regenerate augmented multi-era chunks + edge audit.

    Uses tenant materialized OAuth tokens (no local token files required) and
    writes artifacts to a persistent SaaS artifact root, isolated per user.
    """
    db = SessionLocal()
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        artifact_dir = _phase2_artifact_dir(user_id)
        chunks_root = artifact_dir / "multi_era_chunks"
        scripts_root = Path(__file__).resolve().parent.parent / "scripts"
        run_script = scripts_root / "run_multi_era_backtest_schwab_only.py"
        audit_script = scripts_root / "phase2_edge_audit.py"
        chunk_size = max(20, int(os.getenv("SAAS_PHASE2_CHUNK_SIZE", "120")))
        max_workers = max(1, int(os.getenv("SAAS_PHASE2_MAX_WORKERS", "2")))
        timeout_seconds = max(1800, int(os.getenv("SAAS_PHASE2_TIMEOUT_SECONDS", "14400")))
        retry_on_fail = max(0, int(os.getenv("SAAS_PHASE2_RETRY_ON_FAIL", "1")))

        _upsert_phase2_status(
            db,
            user_id,
            {
                "status": "running",
                "stage": "materializing_tenant_runtime",
                "started_at": started_at,
                "artifact_dir": str(artifact_dir),
            },
        )

        artifact_dir.mkdir(parents=True, exist_ok=True)
        override_paths = _write_phase2_override_files(artifact_dir / "phase1_env_overrides")
        run_order = ["stage2_only_aug", "control_legacy_aug", "control_prod_default_aug"]

        with tenant_skill_dir(db, user_id) as tenant_dir:
            env = os.environ.copy()
            env["TB_RUNTIME_SKILL_DIR"] = str(tenant_dir)
            env["BACKTEST_ARTIFACT_DIR"] = str(artifact_dir)
            env["SCHWAB_ONLY_DATA"] = "true"

            poisoned = _collect_poisoned_chunks(chunks_root, run_order)
            for p in poisoned:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass

            _upsert_phase2_status(
                db,
                user_id,
                {
                    "status": "running",
                    "stage": "chunk_regeneration",
                    "started_at": started_at,
                    "artifact_dir": str(artifact_dir),
                    "deleted_poisoned_chunks": len(poisoned),
                    "runs": [],
                },
            )

            run_logs: list[dict[str, Any]] = []
            for run_id in run_order:
                cmd = [
                    os.getenv("PYTHON", "python"),
                    str(run_script),
                    "--run-tag",
                    run_id,
                    "--env-overrides",
                    str(override_paths[run_id]),
                    "--chunk-size",
                    str(chunk_size),
                    "--max-workers",
                    str(max_workers),
                    "--timeout-seconds",
                    str(timeout_seconds),
                    "--retry-on-fail",
                    str(retry_on_fail),
                ]
                result = _run_logged_subprocess(
                    cmd=cmd,
                    env=env,
                    cwd=Path(__file__).resolve().parent.parent,
                    timeout_seconds=timeout_seconds * 2,
                )
                result["run_id"] = run_id
                run_logs.append(result)
                _upsert_phase2_status(
                    db,
                    user_id,
                    {
                        "status": "running",
                        "stage": "chunk_regeneration",
                        "started_at": started_at,
                        "artifact_dir": str(artifact_dir),
                        "deleted_poisoned_chunks": len(poisoned),
                        "runs": run_logs,
                    },
                )
                if int(result.get("returncode", 1)) != 0:
                    _upsert_phase2_status(
                        db,
                        user_id,
                        {
                            "status": "failed",
                            "stage": "chunk_regeneration",
                            "started_at": started_at,
                            "artifact_dir": str(artifact_dir),
                            "deleted_poisoned_chunks": len(poisoned),
                            "runs": run_logs,
                            "error": f"multi-era run failed for {run_id}",
                        },
                    )
                    return {"ok": False, "error": f"multi-era run failed for {run_id}", "runs": run_logs}

            _upsert_phase2_status(
                db,
                user_id,
                {
                    "status": "running",
                    "stage": "edge_audit",
                    "started_at": started_at,
                    "artifact_dir": str(artifact_dir),
                    "deleted_poisoned_chunks": len(poisoned),
                    "runs": run_logs,
                },
            )

            audit_cmd = [
                os.getenv("PYTHON", "python"),
                str(audit_script),
                "--bare-run-id",
                "stage2_only_aug",
                "--control-run-id",
                "control_legacy_aug",
                "--out-prefix",
                "phase2_edge_audit_aug",
            ]
            audit_result = _run_logged_subprocess(
                cmd=audit_cmd,
                env=env,
                cwd=Path(__file__).resolve().parent.parent,
                timeout_seconds=max(600, timeout_seconds),
            )
            if int(audit_result.get("returncode", 1)) != 0:
                _upsert_phase2_status(
                    db,
                    user_id,
                    {
                        "status": "failed",
                        "stage": "edge_audit",
                        "started_at": started_at,
                        "artifact_dir": str(artifact_dir),
                        "runs": run_logs,
                        "audit": audit_result,
                        "error": "phase2_edge_audit failed",
                    },
                )
                return {"ok": False, "error": "phase2_edge_audit failed", "runs": run_logs, "audit": audit_result}

        audit_json = artifact_dir / "phase2_edge_audit_aug.json"
        audit_payload: dict[str, Any] = {}
        if audit_json.exists():
            try:
                audit_payload = json.loads(audit_json.read_text(encoding="utf-8"))
            except Exception:
                audit_payload = {}
        out = {
            "ok": True,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "artifact_dir": str(artifact_dir),
            "deleted_poisoned_chunks": len(poisoned),
            "runs": run_logs,
            "audit": {
                "verdict": audit_payload.get("verdict"),
                "overlay_finding": audit_payload.get("overlay_finding"),
                "recommendation": audit_payload.get("recommendation"),
                "json_path": str(audit_json),
                "markdown_path": str(artifact_dir / "phase2_edge_audit_aug.md"),
            },
        }
        _upsert_phase2_status(
            db,
            user_id,
            {
                "status": "success",
                "stage": "completed",
                **out,
            },
        )
        return out
    except Exception as exc:
        safe_error = safe_exception_message(exc, fallback="phase2_stage1_failed")
        try:
            _upsert_phase2_status(
                db,
                user_id,
                {
                    "status": "failed",
                    "stage": "exception",
                    "started_at": started_at,
                    "error": safe_error,
                },
            )
        except Exception:
            db.rollback()
        return {"ok": False, "error": safe_error}
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
            row.error_message = safe_exception_message(exc, fallback="invalid_backtest_spec")
            db.commit()
            return {"ok": False, "error": row.error_message, "run_id": run_id}
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
            row.error_message = safe_exception_message(exc, fallback="backtest_failed")
            row.result_json = None
            db.commit()
            return {"ok": False, "error": row.error_message, "run_id": run_id}
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
        try:
            upsert_trade_outcome(
                db,
                user_id,
                _build_trade_outcome_payload(user_id=user_id, order_row=row, result=result),
            )
        except Exception as outcome_exc:
            LOG.debug("Trade outcome persist skipped for user=%s order=%s: %s", user_id, row.id, outcome_exc)
        return {"ok": True, "order_id": row.id, "result": result}
    except Exception as exc:
        db.rollback()
        row = db.query(Order).filter(Order.id == order_id).first()
        safe_error = safe_exception_message(exc, fallback="order_execution_failed")
        if row:
            row.status = "failed"
            row.error_message = safe_error
            row.result_json = json.dumps({"ok": False, "error": safe_error})
            db.commit()
        return {"ok": False, "order_id": order_id, "error": safe_error}
    finally:
        db.close()
