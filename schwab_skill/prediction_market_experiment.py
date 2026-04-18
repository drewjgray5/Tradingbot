from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backtest import run_backtest
from signal_scanner import scan_for_signals_detailed

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
AB_RESULTS_FILE = ".prediction_market_ab_results.json"
SHADOW_RESULTS_FILE = ".prediction_market_shadow_eval.json"


@dataclass(slots=True)
class ExperimentPaths:
    universe_file: Path
    pm_historical_file: Path


@dataclass(slots=True)
class WalkForwardWindow:
    name: str
    start_date: str
    end_date: str
    is_holdout: bool


def run_ab_backtest_experiment(
    *,
    start_date: str,
    end_date: str,
    paths: ExperimentPaths,
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    sd = skill_dir or SKILL_DIR
    universe = load_frozen_universe(paths.universe_file, start_date=start_date)
    control_overrides = build_control_overrides()
    treatment_overrides = build_treatment_overrides()

    control = run_backtest(
        tickers=universe,
        start_date=start_date,
        end_date=end_date,
        skill_dir=sd,
        env_overrides=control_overrides,
        include_all_trades=True,
    )
    treatment = run_backtest(
        tickers=universe,
        start_date=start_date,
        end_date=end_date,
        skill_dir=sd,
        env_overrides=treatment_overrides,
        include_all_trades=True,
        prediction_market_snapshot_path=str(paths.pm_historical_file),
    )

    paired = _paired_trade_analysis(
        control_trades=list(control.get("trades") or []),
        treatment_trades=list(treatment.get("trades") or []),
    )
    result = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": "ab_backtest",
        "constraints": {
            "point_in_time": True,
            "survivorship_controlled": True,
            "universe_file": str(paths.universe_file),
            "pm_historical_file": str(paths.pm_historical_file),
        },
        "window": {"start_date": start_date, "end_date": end_date},
        "control": _summary_fields(control),
        "treatment": _summary_fields(treatment),
        "paired": paired,
        "verdict": _verdict_from_paired(paired),
    }
    _append_history(sd / AB_RESULTS_FILE, result)
    return result


def run_shadow_scan_experiment(
    *,
    watchlist: list[str],
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    sd = skill_dir or SKILL_DIR
    control_overrides = build_control_overrides()
    treatment_overrides = build_treatment_overrides()

    control_signals, control_diag = scan_for_signals_detailed(
        skill_dir=sd,
        env_overrides=control_overrides,
        watchlist_override=watchlist,
    )
    treatment_signals, treatment_diag = scan_for_signals_detailed(
        skill_dir=sd,
        env_overrides=treatment_overrides,
        watchlist_override=watchlist,
    )
    comparison = _compare_signal_sets(control_signals, treatment_signals)
    result = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": "shadow_scan",
        "constraints": {
            "point_in_time": True,
            "survivorship_controlled": True,
            "watchlist_size": len(watchlist),
        },
        "control": {
            "signal_count": len(control_signals),
            "prediction_market": control_diag.get("prediction_market"),
            "diagnostics": _diag_compact(control_diag),
        },
        "treatment": {
            "signal_count": len(treatment_signals),
            "prediction_market": treatment_diag.get("prediction_market"),
            "diagnostics": _diag_compact(treatment_diag),
        },
        "comparison": comparison,
    }
    _append_history(sd / SHADOW_RESULTS_FILE, result)
    return result


def run_ab_walkforward_experiment(
    *,
    start_date: str,
    end_date: str,
    holdout_start: str,
    paths: ExperimentPaths,
    skill_dir: Path | None = None,
    train_window_days: int = 365,
    step_days: int = 120,
) -> dict[str, Any]:
    sd = skill_dir or SKILL_DIR
    windows = _build_walkforward_windows(
        start_date=start_date,
        end_date=end_date,
        holdout_start=holdout_start,
        train_window_days=train_window_days,
        step_days=step_days,
    )
    rows: list[dict[str, Any]] = []
    for window in windows:
        row = run_ab_backtest_experiment(
            start_date=window.start_date,
            end_date=window.end_date,
            paths=paths,
            skill_dir=sd,
        )
        row["window_name"] = window.name
        row["is_holdout"] = bool(window.is_holdout)
        row["regime_bucket"] = _classify_regime_bucket(window.start_date, window.end_date, sd)
        rows.append(row)

    train_rows = [r for r in rows if not bool(r.get("is_holdout"))]
    holdout_rows = [r for r in rows if bool(r.get("is_holdout"))]
    holdout = holdout_rows[-1] if holdout_rows else {}
    result = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": "ab_walkforward",
        "window_count": len(rows),
        "train_window_count": len(train_rows),
        "holdout_window_count": len(holdout_rows),
        "window_params": {
            "start_date": start_date,
            "end_date": end_date,
            "holdout_start": holdout_start,
            "train_window_days": int(train_window_days),
            "step_days": int(step_days),
        },
        "windows": rows,
        "aggregates": _aggregate_walkforward_rows(rows),
        "holdout": {
            "window_name": holdout.get("window_name"),
            "window": holdout.get("window"),
            "paired": holdout.get("paired"),
            "verdict": holdout.get("verdict"),
            "control": holdout.get("control"),
            "treatment": holdout.get("treatment"),
            "regime_bucket": holdout.get("regime_bucket"),
        },
    }
    _append_history(sd / AB_RESULTS_FILE, result)
    return result


def load_frozen_universe(path: Path | str, *, start_date: str) -> list[str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("universe file must be an object with as_of and tickers")
    as_of_raw = str(payload.get("as_of") or "").strip()
    tickers_raw = payload.get("tickers")
    if not as_of_raw:
        raise ValueError("universe file missing as_of")
    if not isinstance(tickers_raw, list):
        raise ValueError("universe file missing tickers list")
    as_of_date = datetime.fromisoformat(as_of_raw).date()
    start_dt = datetime.fromisoformat(start_date).date()
    if as_of_date > start_dt:
        raise ValueError("universe as_of must be on/before backtest start_date")
    cleaned = [str(t).strip().upper() for t in tickers_raw if str(t).strip()]
    if not cleaned:
        raise ValueError("universe file has no tickers")
    return list(dict.fromkeys(cleaned))


def build_control_overrides() -> dict[str, str]:
    return {
        "PRED_MARKET_ENABLED": "false",
        "PRED_MARKET_MODE": "off",
    }


def build_treatment_overrides() -> dict[str, str]:
    return {
        "PRED_MARKET_ENABLED": "true",
        "PRED_MARKET_MODE": "live",
    }


def _summary_fields(result: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "total_trades",
        "win_rate",
        "win_rate_net",
        "total_return_net_pct",
        "cagr_net_pct",
        "max_drawdown_net_pct",
        "profit_factor_net",
        "prediction_market_mode",
        "prediction_market_provider",
    ]
    out = {k: result.get(k) for k in keys}
    out["diagnostics"] = result.get("diagnostics")
    return out


def _paired_trade_analysis(
    *,
    control_trades: list[dict[str, Any]],
    treatment_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    c_map = {_trade_key(t): t for t in control_trades}
    t_map = {_trade_key(t): t for t in treatment_trades}
    common = sorted(set(c_map.keys()) & set(t_map.keys()))
    only_control = sorted(set(c_map.keys()) - set(t_map.keys()))
    only_treatment = sorted(set(t_map.keys()) - set(c_map.keys()))

    deltas: list[float] = []
    for key in common:
        c_ret = float(c_map[key].get("net_return") or 0.0)
        t_ret = float(t_map[key].get("net_return") or 0.0)
        deltas.append(t_ret - c_ret)

    mean_delta = (sum(deltas) / len(deltas)) if deltas else 0.0
    ci_low, ci_high = _bootstrap_mean_ci(deltas, n=2000, alpha=0.05, seed=42)
    return {
        "common_trade_count": len(common),
        "control_only_count": len(only_control),
        "treatment_only_count": len(only_treatment),
        "mean_net_return_delta": round(mean_delta, 8),
        "mean_net_return_delta_ci95": [round(ci_low, 8), round(ci_high, 8)],
        "positive_delta_rate": round((sum(1 for d in deltas if d > 0) / len(deltas)), 6) if deltas else 0.0,
    }


def _verdict_from_paired(paired: dict[str, Any]) -> str:
    ci = paired.get("mean_net_return_delta_ci95") or [0.0, 0.0]
    lo = float(ci[0])
    hi = float(ci[1])
    if lo > 0:
        return "treatment_better"
    if hi < 0:
        return "control_better"
    return "inconclusive"


def _bootstrap_mean_ci(
    samples: list[float],
    *,
    n: int,
    alpha: float,
    seed: int,
) -> tuple[float, float]:
    if not samples:
        return 0.0, 0.0
    rng = random.Random(seed)
    means: list[float] = []
    m = len(samples)
    for _ in range(max(100, n)):
        draw = [samples[rng.randrange(m)] for _ in range(m)]
        means.append(sum(draw) / m)
    means.sort()
    lo_idx = int((alpha / 2.0) * len(means))
    hi_idx = int((1.0 - (alpha / 2.0)) * len(means)) - 1
    lo_idx = max(0, min(lo_idx, len(means) - 1))
    hi_idx = max(0, min(hi_idx, len(means) - 1))
    return float(means[lo_idx]), float(means[hi_idx])


def _trade_key(trade: dict[str, Any]) -> str:
    return f"{str(trade.get('ticker') or '').upper()}|{trade.get('entry_date')}"


def _compare_signal_sets(control: list[dict[str, Any]], treatment: list[dict[str, Any]]) -> dict[str, Any]:
    c_tickers = [str(s.get("ticker") or "").upper() for s in control]
    t_tickers = [str(s.get("ticker") or "").upper() for s in treatment]
    c_set = set(c_tickers)
    t_set = set(t_tickers)
    return {
        "overlap": sorted(c_set & t_set),
        "control_only": sorted(c_set - t_set),
        "treatment_only": sorted(t_set - c_set),
        "control_ranked": c_tickers,
        "treatment_ranked": t_tickers,
    }


def _diag_compact(diag: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "watchlist_size",
        "stage2_fail",
        "vcp_fail",
        "quality_gates_filtered",
        "prediction_market_processed",
        "prediction_market_applied",
        "prediction_market_skipped",
        "prediction_market_errors",
        "exceptions",
    ]
    return {k: diag.get(k) for k in keys}


def _append_history(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    else:
        existing = []
    if not isinstance(existing, list):
        existing = []
    existing.append(payload)
    path.write_text(json.dumps(existing[-200:], indent=2), encoding="utf-8")


def _build_walkforward_windows(
    *,
    start_date: str,
    end_date: str,
    holdout_start: str,
    train_window_days: int,
    step_days: int,
) -> list[WalkForwardWindow]:
    start = datetime.fromisoformat(start_date).date()
    end = datetime.fromisoformat(end_date).date()
    holdout = datetime.fromisoformat(holdout_start).date()
    if holdout <= start:
        raise ValueError("holdout_start must be after start_date")
    if holdout > end:
        raise ValueError("holdout_start must be on/before end_date")
    windows: list[WalkForwardWindow] = []
    cursor = start
    idx = 0
    min_train = max(60, int(train_window_days))
    step = max(20, int(step_days))
    while cursor < holdout:
        win_end = min(holdout - timedelta(days=1), cursor + timedelta(days=min_train))
        if win_end <= cursor:
            break
        windows.append(
            WalkForwardWindow(
                name=f"wf_{idx:02d}",
                start_date=cursor.isoformat(),
                end_date=win_end.isoformat(),
                is_holdout=False,
            )
        )
        idx += 1
        cursor = cursor + timedelta(days=step)
        if cursor >= holdout:
            break
    windows.append(
        WalkForwardWindow(
            name="holdout",
            start_date=holdout.isoformat(),
            end_date=end.isoformat(),
            is_holdout=True,
        )
    )
    return windows


def _aggregate_walkforward_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    paired_counts = [int((r.get("paired") or {}).get("common_trade_count", 0) or 0) for r in rows]
    paired_deltas = [float((r.get("paired") or {}).get("mean_net_return_delta", 0) or 0) for r in rows]
    cagr_net = [float((r.get("treatment") or {}).get("cagr_net_pct", 0) or 0) for r in rows]
    dd_net = [float((r.get("treatment") or {}).get("max_drawdown_net_pct", 0) or 0) for r in rows]
    pf_net = [float((r.get("treatment") or {}).get("profit_factor_net", 0) or 0) for r in rows]
    win_net = [float((r.get("treatment") or {}).get("win_rate_net", 0) or 0) for r in rows]
    trade_count = [int((r.get("treatment") or {}).get("total_trades", 0) or 0) for r in rows]
    regime_perf: dict[str, dict[str, float]] = {}
    for row in rows:
        regime = str(row.get("regime_bucket") or "unknown")
        rp = regime_perf.setdefault(
            regime, {"windows": 0.0, "mean_delta": 0.0, "mean_cagr_net_pct": 0.0, "mean_pf_net": 0.0}
        )
        rp["windows"] += 1.0
        rp["mean_delta"] += float((row.get("paired") or {}).get("mean_net_return_delta", 0) or 0)
        rp["mean_cagr_net_pct"] += float((row.get("treatment") or {}).get("cagr_net_pct", 0) or 0)
        rp["mean_pf_net"] += float((row.get("treatment") or {}).get("profit_factor_net", 0) or 0)
    for regime, vals in regime_perf.items():
        n = max(1.0, float(vals["windows"]))
        vals["mean_delta"] = round(vals["mean_delta"] / n, 8)
        vals["mean_cagr_net_pct"] = round(vals["mean_cagr_net_pct"] / n, 4)
        vals["mean_pf_net"] = round(vals["mean_pf_net"] / n, 4)
        vals["windows"] = int(vals["windows"])
    return {
        "window_count": len(rows),
        "paired_common_trade_min": min(paired_counts) if paired_counts else 0,
        "paired_common_trade_total": sum(paired_counts),
        "mean_paired_delta": round(sum(paired_deltas) / len(paired_deltas), 8) if paired_deltas else 0.0,
        "mean_cagr_net_pct": round(sum(cagr_net) / len(cagr_net), 4) if cagr_net else 0.0,
        "max_drawdown_net_worst_pct": round(min(dd_net), 4) if dd_net else 0.0,
        "mean_profit_factor_net": round(sum(pf_net) / len(pf_net), 4) if pf_net else 0.0,
        "mean_hit_rate_net": round(sum(win_net) / len(win_net), 4) if win_net else 0.0,
        "mean_turnover_proxy_trades": round(sum(trade_count) / len(trade_count), 4) if trade_count else 0.0,
        "regime_slices": regime_perf,
    }


def _classify_regime_bucket(start_date: str, end_date: str, skill_dir: Path) -> str:
    try:
        from market_data import get_daily_history
        from stage_analysis import add_indicators

        spy = get_daily_history("SPY", days=900, auth=None, skill_dir=skill_dir)
        if spy is None or spy.empty:
            return "unknown"
        in_window = spy.loc[(spy.index >= start_date) & (spy.index <= end_date)].copy()
        if in_window.empty:
            return "unknown"
        in_window = add_indicators(in_window)
        start_px = float(in_window["close"].iloc[0])
        end_px = float(in_window["close"].iloc[-1])
        ret = (end_px - start_px) / start_px if start_px > 0 else 0.0
        above_200_rate = float((in_window["close"] > in_window["sma_200"]).mean())
        if above_200_rate >= 0.65 and ret > 0.02:
            return "bull"
        if above_200_rate <= 0.35 and ret < -0.02:
            return "bear"
        return "chop"
    except Exception:
        return "unknown"
