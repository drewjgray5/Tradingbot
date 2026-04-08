#!/usr/bin/env python3
"""
Walk-forward bounded optimization loop for strategy parameters.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
sys.path.insert(0, str(SKILL_DIR))

from backtest import run_backtest  # noqa: E402
from config import (  # noqa: E402
    get_advisory_confidence_high,
    get_advisory_confidence_low,
    get_quality_breakout_volume_min_ratio,
    get_quality_gates_mode,
    get_quality_min_signal_score,
    get_quality_require_breakout_volume,
    get_quality_soft_min_reasons,
    get_signal_top_n,
    get_signal_universe_target_size,
)


@dataclass
class EvalResult:
    params: dict[str, str]
    walk_forward: dict[str, Any]
    objective: float
    reason: str


TUNABLE_KEYS = (
    "QUALITY_GATES_MODE",
    "QUALITY_SOFT_MIN_REASONS",
    "QUALITY_MIN_SIGNAL_SCORE",
    "QUALITY_BREAKOUT_VOLUME_MIN_RATIO",
    "QUALITY_REQUIRE_BREAKOUT_VOLUME",
    "ADVISORY_CONFIDENCE_HIGH",
    "ADVISORY_CONFIDENCE_LOW",
    "SIGNAL_TOP_N",
    "SIGNAL_UNIVERSE_TARGET_SIZE",
)

MUTATION_PLAN: dict[str, list[str]] = {
    "QUALITY_GATES_MODE": ["soft", "hard", "shadow"],
    "QUALITY_SOFT_MIN_REASONS": ["-1", "+1"],
    "QUALITY_MIN_SIGNAL_SCORE": ["-3", "+3", "-5", "+5"],
    "QUALITY_BREAKOUT_VOLUME_MIN_RATIO": ["-0.05", "+0.05", "-0.10", "+0.10"],
    "QUALITY_REQUIRE_BREAKOUT_VOLUME": ["toggle"],
    "ADVISORY_CONFIDENCE_HIGH": ["-0.01", "+0.01", "-0.02", "+0.02"],
    "ADVISORY_CONFIDENCE_LOW": ["-0.01", "+0.01", "-0.02", "+0.02"],
    "SIGNAL_TOP_N": ["-1", "+1", "-2", "+2"],
    "SIGNAL_UNIVERSE_TARGET_SIZE": ["-25", "+25", "-50", "+50"],
}

WALK_FORWARD_SPLITS = (
    {"name": "wf_train_long", "start": "2018-01-01", "tickers": 24, "is_oos": False},
    {"name": "wf_train_mid", "start": "2020-01-01", "tickers": 24, "is_oos": False},
    {"name": "wf_oos_recent", "start": "2022-01-01", "tickers": 24, "is_oos": True},
)

ROBUST_BASELINE_POLICY = {
    "min_trades": 35,
    "max_drawdown_degrade": 1.5,
    "min_oos_pf": 1.15,
    "min_oos_pf_margin": 0.01,
}


def _to_bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _clamp(key: str, value: str) -> str:
    if key == "QUALITY_GATES_MODE":
        raw = str(value).strip().lower()
        return raw if raw in {"off", "shadow", "soft", "hard"} else "soft"
    if key == "QUALITY_SOFT_MIN_REASONS":
        return str(min(5, max(1, int(float(value)))))
    if key == "QUALITY_MIN_SIGNAL_SCORE":
        return str(min(85, max(20, int(float(value)))))
    if key == "QUALITY_BREAKOUT_VOLUME_MIN_RATIO":
        return f"{min(1.30, max(0.70, float(value))):.2f}"
    if key == "QUALITY_REQUIRE_BREAKOUT_VOLUME":
        return "true" if _to_bool(value) else "false"
    if key == "ADVISORY_CONFIDENCE_HIGH":
        return f"{min(0.90, max(0.50, float(value))):.3f}"
    if key == "ADVISORY_CONFIDENCE_LOW":
        return f"{min(0.80, max(0.40, float(value))):.3f}"
    if key == "SIGNAL_TOP_N":
        return str(min(15, max(1, int(float(value)))))
    if key == "SIGNAL_UNIVERSE_TARGET_SIZE":
        return str(min(600, max(80, int(float(value)))))
    return str(value)


def _normalize_params(params: dict[str, str]) -> dict[str, str]:
    out = {k: _clamp(k, v) for k, v in params.items()}
    hi = float(out["ADVISORY_CONFIDENCE_HIGH"])
    lo = float(out["ADVISORY_CONFIDENCE_LOW"])
    if lo >= hi:
        lo = max(0.40, min(0.79, hi - 0.02))
        out["ADVISORY_CONFIDENCE_LOW"] = f"{lo:.3f}"
    return out


def _default_params() -> dict[str, str]:
    return _normalize_params(
        {
            "QUALITY_GATES_MODE": str(get_quality_gates_mode(SKILL_DIR)),
            "QUALITY_SOFT_MIN_REASONS": str(get_quality_soft_min_reasons(SKILL_DIR)),
            "QUALITY_MIN_SIGNAL_SCORE": str(get_quality_min_signal_score(SKILL_DIR)),
            "QUALITY_BREAKOUT_VOLUME_MIN_RATIO": str(get_quality_breakout_volume_min_ratio(SKILL_DIR)),
            "QUALITY_REQUIRE_BREAKOUT_VOLUME": "true" if get_quality_require_breakout_volume(SKILL_DIR) else "false",
            "ADVISORY_CONFIDENCE_HIGH": str(get_advisory_confidence_high(SKILL_DIR)),
            "ADVISORY_CONFIDENCE_LOW": str(get_advisory_confidence_low(SKILL_DIR)),
            "SIGNAL_TOP_N": str(get_signal_top_n(SKILL_DIR)),
            "SIGNAL_UNIVERSE_TARGET_SIZE": str(get_signal_universe_target_size(SKILL_DIR)),
        }
    )


def _canonical(params: dict[str, str]) -> str:
    return json.dumps({k: params[k] for k in sorted(params.keys())}, sort_keys=True)


@contextmanager
def _temporary_env(overrides: dict[str, str]):
    old: dict[str, str | None] = {}
    try:
        for k, v in overrides.items():
            old[k] = os.environ.get(k)
            os.environ[k] = str(v)
        yield
    finally:
        for k, prev in old.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


def _objective(metrics: dict[str, Any], min_trades: int) -> float:
    pf_net = float(metrics.get("profit_factor_net", 0) or 0)
    wr_net = float(metrics.get("win_rate_net", metrics.get("win_rate", 0)) or 0)
    expectancy = float(metrics.get("avg_return_net_pct", metrics.get("avg_return_pct", 0)) or 0)
    drawdown = float(metrics.get("max_drawdown_net_pct", metrics.get("max_drawdown_pct", 0)) or 0)
    trades = int(metrics.get("total_trades", 0) or 0)
    drawdown_mag = abs(min(0.0, drawdown))
    starvation_penalty = max(0, min_trades - trades) * 0.6
    drawdown_penalty = max(0.0, drawdown_mag - 25.0) * 1.8
    return (120.0 * pf_net) + (0.7 * wr_net) + (28.0 * expectancy) - starvation_penalty - drawdown_penalty


def _default_tickers(n: int) -> list[str]:
    base = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "V", "UNH",
        "HD", "PG", "MA", "DIS", "BAC", "XOM", "CVX", "KO", "PEP", "WMT",
        "IBM", "ORCL", "CRM", "ADBE", "NFLX", "INTC", "AMD", "QCOM", "TXN", "AVGO",
        "CSCO", "ACN", "NOW", "INTU", "AMAT", "LRCX", "KLAC", "MU", "SBUX", "NKE",
    ]
    return base[: max(10, min(n, len(base)))]


def _evaluate_single(params: dict[str, str], tickers: list[str], start_date: str, skip_mirofish: bool, min_trades: int) -> dict[str, Any]:
    with _temporary_env(params | ({"BACKTEST_SKIP_MIROFISH": "true"} if skip_mirofish else {})):
        metrics = run_backtest(tickers=tickers, start_date=start_date)
    return {
        "start_date": start_date,
        "ticker_count": len(tickers),
        "objective": _objective(metrics, min_trades=min_trades),
        "total_trades": int(metrics.get("total_trades", 0) or 0),
        "profit_factor_net": float(metrics.get("profit_factor_net", metrics.get("profit_factor", 0)) or 0),
        "avg_return_net_pct": float(metrics.get("avg_return_net_pct", metrics.get("avg_return_pct", 0)) or 0),
        "max_drawdown_net_pct": float(metrics.get("max_drawdown_net_pct", metrics.get("max_drawdown_pct", 0)) or 0),
        "win_rate_net": float(metrics.get("win_rate_net", metrics.get("win_rate", 0)) or 0),
    }


def _evaluate_walk_forward(params: dict[str, str], ticker_pool: list[str], skip_mirofish: bool, min_trades: int) -> dict[str, Any]:
    splits: list[dict[str, Any]] = []
    for split in WALK_FORWARD_SPLITS:
        tickers = ticker_pool[: int(split["tickers"])]
        row = _evaluate_single(params, tickers, str(split["start"]), skip_mirofish, min_trades)
        row["name"] = str(split["name"])
        row["is_oos"] = bool(split["is_oos"])
        splits.append(row)
    oos = [s for s in splits if s.get("is_oos")]
    pf_vals = [float(s["profit_factor_net"]) for s in splits]
    exp_vals = [float(s["avg_return_net_pct"]) for s in splits]
    dd_vals = [float(s["max_drawdown_net_pct"]) for s in splits]
    obj_vals = [float(s["objective"]) for s in splits]
    trades = [int(s["total_trades"]) for s in splits]
    oos_row = oos[-1] if oos else splits[-1]
    return {
        "splits": splits,
        "aggregates": {
            "objective_mean": round(sum(obj_vals) / len(obj_vals), 4),
            "pf_mean": round(sum(pf_vals) / len(pf_vals), 4),
            "expectancy_mean": round(sum(exp_vals) / len(exp_vals), 4),
            "drawdown_worst": round(min(dd_vals), 4),
            "trades_min": int(min(trades)),
            "oos_pf": round(float(oos_row["profit_factor_net"]), 4),
            "oos_expectancy": round(float(oos_row["avg_return_net_pct"]), 4),
            "oos_drawdown": round(float(oos_row["max_drawdown_net_pct"]), 4),
            "oos_trades": int(oos_row["total_trades"]),
        },
    }


def _review_candidate(
    baseline_wf: dict[str, Any],
    candidate_wf: dict[str, Any],
    *,
    min_trades: int,
    max_drawdown_degrade: float,
    min_oos_pf: float,
) -> tuple[bool, str]:
    b = baseline_wf["aggregates"]
    c = candidate_wf["aggregates"]
    if int(c["trades_min"]) < int(min_trades):
        return False, f"rejected: trades_min {int(c['trades_min'])} < {int(min_trades)}"
    if float(c["oos_pf"]) < float(min_oos_pf):
        return False, f"rejected: oos_pf {float(c['oos_pf']):.3f} < {float(min_oos_pf):.3f}"
    b_dd = abs(min(0.0, float(b["drawdown_worst"])))
    c_dd = abs(min(0.0, float(c["drawdown_worst"])))
    if c_dd > (b_dd + float(max_drawdown_degrade)):
        return False, (
            f"rejected: drawdown degraded {c_dd:.2f}% > baseline+cap "
            f"{b_dd + float(max_drawdown_degrade):.2f}%"
        )
    return True, "accepted_by_walkforward_gates"


def _is_better_candidate(best_wf: dict[str, Any], candidate_wf: dict[str, Any], candidate_obj: float, best_obj: float, min_oos_pf_margin: float) -> bool:
    b = best_wf["aggregates"]
    c = candidate_wf["aggregates"]
    b_oos_pf = float(b["oos_pf"])
    c_oos_pf = float(c["oos_pf"])
    if c_oos_pf >= (b_oos_pf + float(min_oos_pf_margin)):
        return True
    if c_oos_pf < b_oos_pf:
        return False
    # Tie-break at equal OOS PF: prefer stronger objective, then higher OOS expectancy.
    if candidate_obj > best_obj:
        return True
    if candidate_obj < best_obj:
        return False
    return float(c["oos_expectancy"]) > float(b["oos_expectancy"])


def _apply_mutation(params: dict[str, str], key: str, op: str) -> tuple[dict[str, str], str]:
    out = dict(params)
    before = out[key]
    if op == "toggle":
        out[key] = "false" if _to_bool(before) else "true"
    elif key == "QUALITY_GATES_MODE":
        out[key] = op
    elif op.startswith("+") or op.startswith("-"):
        delta = float(op)
        if key in {"QUALITY_SOFT_MIN_REASONS", "QUALITY_MIN_SIGNAL_SCORE", "SIGNAL_TOP_N", "SIGNAL_UNIVERSE_TARGET_SIZE"}:
            out[key] = str(int(round(float(before) + delta)))
        else:
            out[key] = f"{float(before) + delta:.4f}"
    else:
        out[key] = op
    out = _normalize_params(out)
    return out, f"{key}: {before} -> {out[key]}"


def _propose_next(best_params: dict[str, str], tried: set[str], round_idx: int) -> tuple[dict[str, str] | None, str]:
    key_order = list(TUNABLE_KEYS)
    offset = round_idx % len(key_order)
    key_order = key_order[offset:] + key_order[:offset]
    for key in key_order:
        for op in MUTATION_PLAN.get(key, []):
            cand, desc = _apply_mutation(best_params, key, op)
            sig = _canonical(cand)
            if sig in tried:
                continue
            return cand, desc
    return None, "no_untried_neighbor"


def _write_markdown_report(path: Path, run_id: str, baseline: EvalResult, best: EvalResult, history: list[dict[str, Any]]) -> None:
    b = baseline.walk_forward["aggregates"]
    c = best.walk_forward["aggregates"]
    lines = [
        "# Walk-Forward Optimization Report",
        "",
        f"- run_id: `{run_id}`",
        f"- baseline_objective: `{baseline.objective:.4f}`",
        f"- challenger_objective: `{best.objective:.4f}`",
        "",
        "## Champion vs Challenger",
        "",
        "| Metric | Baseline | Challenger | Delta |",
        "|---|---:|---:|---:|",
        f"| PF mean | {b['pf_mean']:.4f} | {c['pf_mean']:.4f} | {c['pf_mean'] - b['pf_mean']:+.4f} |",
        f"| Expectancy mean (%) | {b['expectancy_mean']:.4f} | {c['expectancy_mean']:.4f} | {c['expectancy_mean'] - b['expectancy_mean']:+.4f} |",
        f"| Worst drawdown (%) | {b['drawdown_worst']:.4f} | {c['drawdown_worst']:.4f} | {c['drawdown_worst'] - b['drawdown_worst']:+.4f} |",
        f"| OOS PF | {b['oos_pf']:.4f} | {c['oos_pf']:.4f} | {c['oos_pf'] - b['oos_pf']:+.4f} |",
        f"| OOS expectancy (%) | {b['oos_expectancy']:.4f} | {c['oos_expectancy']:.4f} | {c['oos_expectancy'] - b['oos_expectancy']:+.4f} |",
        f"| Trades min | {int(b['trades_min'])} | {int(c['trades_min'])} | {int(c['trades_min']) - int(b['trades_min']):+d} |",
        "",
        "## Final Parameter Set",
        "",
        "```json",
        json.dumps(best.params, indent=2, sort_keys=True),
        "```",
        "",
        f"## Search Rounds: {len(history) - 1}",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded walk-forward optimization loop")
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--stall-rounds", type=int, default=3)
    parser.add_argument("--tickers", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--min-trades", type=int, default=int(ROBUST_BASELINE_POLICY["min_trades"]))
    parser.add_argument("--max-drawdown-degrade", type=float, default=float(ROBUST_BASELINE_POLICY["max_drawdown_degrade"]))
    parser.add_argument("--min-oos-pf", type=float, default=float(ROBUST_BASELINE_POLICY["min_oos_pf"]))
    parser.add_argument("--min-oos-pf-margin", type=float, default=float(ROBUST_BASELINE_POLICY["min_oos_pf_margin"]))
    parser.add_argument("--include-mirofish", action="store_true")
    args = parser.parse_args()

    random.seed(args.seed)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    ticker_pool = _default_tickers(args.tickers)

    baseline_params = _default_params()
    baseline_wf = _evaluate_walk_forward(
        baseline_params,
        ticker_pool=ticker_pool,
        skip_mirofish=not args.include_mirofish,
        min_trades=args.min_trades,
    )
    baseline_obj = float(baseline_wf["aggregates"]["objective_mean"])
    best = EvalResult(params=baseline_params, walk_forward=baseline_wf, objective=baseline_obj, reason="baseline")

    history: list[dict[str, Any]] = [
        {"round": 0, "mutation": "none", "accepted": True, "reason": "baseline", "objective": round(baseline_obj, 4)}
    ]
    tried: set[str] = {_canonical(best.params)}
    stall = 0
    min_explore = min(max(1, args.rounds), max(1, len(TUNABLE_KEYS)))
    print(f"Baseline objective={best.objective:.4f} oos_pf={best.walk_forward['aggregates']['oos_pf']:.3f}")

    for r in range(1, max(1, args.rounds) + 1):
        candidate_params, mutation = _propose_next(best.params, tried, r)
        if candidate_params is None:
            print(f"Round {r}: no untried neighbors remain.")
            break
        tried.add(_canonical(candidate_params))
        cand_wf = _evaluate_walk_forward(
            candidate_params,
            ticker_pool=ticker_pool,
            skip_mirofish=not args.include_mirofish,
            min_trades=args.min_trades,
        )
        cand_obj = float(cand_wf["aggregates"]["objective_mean"])
        ok, reason = _review_candidate(
            best.walk_forward,
            cand_wf,
            min_trades=args.min_trades,
            max_drawdown_degrade=args.max_drawdown_degrade,
            min_oos_pf=args.min_oos_pf,
        )
        accepted = bool(
            ok and _is_better_candidate(
                best.walk_forward,
                cand_wf,
                candidate_obj=cand_obj,
                best_obj=best.objective,
                min_oos_pf_margin=args.min_oos_pf_margin,
            )
        )
        if accepted:
            prev_obj = best.objective
            best = EvalResult(
                params=candidate_params,
                walk_forward=cand_wf,
                objective=cand_obj,
                reason=f"accepted: {prev_obj:.4f}->{cand_obj:.4f}",
            )
            stall = 0
        else:
            stall += 1
        history.append(
            {
                "round": r,
                "mutation": mutation,
                "accepted": accepted,
                "reason": best.reason if accepted else (reason if not ok else "rejected: objective_not_improved"),
                "objective": round(cand_obj, 4),
                "aggregates": cand_wf["aggregates"],
                "params": candidate_params,
            }
        )
        print(f"Round {r}: {mutation} | objective={cand_obj:.4f} | accepted={accepted}")
        if r >= min_explore and stall >= max(1, args.stall_rounds):
            print(f"Stopping early after {stall} stalled rounds.")
            break

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "started_with": baseline_params,
        "best_params": best.params,
        "baseline_walk_forward": baseline_wf,
        "best_walk_forward": best.walk_forward,
        "best_objective": round(best.objective, 4),
        "rounds_executed": len(history) - 1,
        "history": history,
        "gates": {
            "min_trades": int(args.min_trades),
            "max_drawdown_degrade": float(args.max_drawdown_degrade),
            "min_oos_pf": float(args.min_oos_pf),
            "min_oos_pf_margin": float(args.min_oos_pf_margin),
        },
        "baseline_policy_defaults": ROBUST_BASELINE_POLICY,
    }
    out_json = ARTIFACT_DIR / f"optimization_walkforward_{run_id}.json"
    out_md = ARTIFACT_DIR / f"optimization_walkforward_{run_id}.md"
    out_json.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    _write_markdown_report(out_md, run_id, EvalResult(baseline_params, baseline_wf, baseline_obj, "baseline"), best, history)
    print(f"Optimization artifact: {out_json}")
    print(f"Optimization report: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
