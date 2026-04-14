"""Learning and performance routes: challenger, evolve, data-provider, performance, calibration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from ..calibration_snapshot import build_calibration_snapshot
from ..recovery_map import map_failure as _map_failure
from ..schemas import ApiResponse

router = APIRouter(tags=["learning"])

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
BACKTEST_RESULTS_PATH = SKILL_DIR / ".backtest_results.json"
TRADE_OUTCOMES_PATH = SKILL_DIR / ".trade_outcomes.json"
EXECUTION_METRICS_PATH = SKILL_DIR / "execution_safety_metrics.json"
VALIDATION_ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err_response(endpoint: str, exc: Exception) -> ApiResponse:
    mapped = _map_failure(str(exc), source=endpoint)
    headline = f"{mapped.get('title', 'Error')}: {mapped.get('summary', 'Something went wrong.')}"
    raw = str(mapped.get("raw_error") or "").strip()
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


def _require_api_key_if_set(
    x_api_key: str | None = Header(default=None),
    x_user: str | None = Header(default=None),
) -> dict[str, str]:
    configured = os.getenv("WEB_API_KEY", "").strip()
    if not configured:
        unsafe = (os.getenv("WEB_ALLOW_UNSAFE_LOCAL_WRITES") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if unsafe:
            return {"actor": (x_user or "unsafe-local-user").strip() or "unsafe-local-user"}
        raise HTTPException(
            status_code=503,
            detail="WEB_API_KEY is required for write operations. Configure WEB_API_KEY on the server.",
        )
    if not x_api_key or x_api_key != configured:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")
    return {"actor": (x_user or "web-user").strip() or "web-user"}


def _get_validation_status() -> dict[str, Any]:
    from ..main import _latest_validation_status
    return _latest_validation_status()


def _get_challenger_summary() -> dict[str, Any]:
    try:
        from challenger_mode import ChallengerRunner

        runner = ChallengerRunner(skill_dir=SKILL_DIR)
        latest = runner.get_latest_comparison()
        win_rate = runner.get_win_rate_summary()
        strategy_update = _read_json_file(SKILL_DIR / "strategy_update.json", {})
        can_run = bool(isinstance(strategy_update, dict) and strategy_update.get("env_overrides"))
        return {"available": True, "latest": latest, "win_rate": win_rate, "can_run": can_run}
    except Exception:
        return {"available": False}


@router.get("/api/performance", response_model=ApiResponse)
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
    return _ok({
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
            "status": _get_validation_status(),
            "artifacts_present": VALIDATION_ARTIFACT_DIR.exists(),
        },
        "separation_guard": {
            "commingled_metric_allowed": False,
            "message": "Backtest, shadow/paper, and live are reported as separate buckets only.",
        },
        "challenger": _get_challenger_summary(),
    })


@router.get("/api/calibration/summary", response_model=ApiResponse)
def api_calibration_summary() -> ApiResponse:
    return _ok(build_calibration_snapshot(SKILL_DIR))


@router.get("/api/challenger/latest", response_model=ApiResponse)
def challenger_latest() -> ApiResponse:
    try:
        from challenger_mode import ChallengerRunner

        runner = ChallengerRunner(skill_dir=SKILL_DIR)
        latest = runner.get_latest_comparison()
        if not latest:
            return _ok({"status": "no_data", "message": "No challenger runs yet."})
        return _ok(latest)
    except Exception as e:
        return _err_response("challenger_latest", e)


@router.get("/api/challenger/history", response_model=ApiResponse)
def challenger_history(n: int = 10) -> ApiResponse:
    try:
        from challenger_mode import ChallengerRunner

        runner = ChallengerRunner(skill_dir=SKILL_DIR)
        return _ok({
            "history": runner.get_comparison_history(n),
            "win_rate": runner.get_win_rate_summary(),
        })
    except Exception as e:
        return _err_response("challenger_history", e)


@router.post("/api/challenger/run", response_model=ApiResponse)
def challenger_run(
    _auth: dict[str, str] = Depends(_require_api_key_if_set),
) -> ApiResponse:
    try:
        from challenger_mode import ChallengerRunner

        runner = ChallengerRunner(skill_dir=SKILL_DIR)
        result = runner.run()
        return _ok(result)
    except Exception as e:
        return _err_response("challenger_run", e)


@router.get("/api/data-provider/status", response_model=ApiResponse)
def data_provider_status() -> ApiResponse:
    try:
        from data_provider import DataProvider

        provider = DataProvider(skill_dir=SKILL_DIR)
        return _ok(provider.status())
    except Exception as e:
        return _err_response("data_provider_status", e)


@router.post("/api/evolve/run", response_model=ApiResponse)
def evolve_run(
    _auth: dict[str, str] = Depends(_require_api_key_if_set),
) -> ApiResponse:
    try:
        from evolve_logic import LearningEngine

        engine = LearningEngine(skill_dir=SKILL_DIR)
        result = engine.run(apply=False)
        return _ok(result)
    except Exception as e:
        return _err_response("evolve_run", e)
