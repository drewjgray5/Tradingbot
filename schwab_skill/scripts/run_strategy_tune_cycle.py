#!/usr/bin/env python3
"""
Weekly strategy tune + promotion workflow orchestration.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from promotion_guard import ensure_signed_approval


def _run(cmd: list[str]) -> int:
    print(" ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR))
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run weekly walk-forward tune and promotion decision")
    parser.add_argument("--deep-runs", type=int, default=12)
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--stall-rounds", type=int, default=3)
    parser.add_argument("--tickers", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--min-trades", type=int, default=35)
    parser.add_argument("--min-oos-pf", type=float, default=1.15)
    parser.add_argument("--min-oos-pf-delta", type=float, default=0.01)
    parser.add_argument("--min-pf-delta", type=float, default=0.02)
    parser.add_argument("--min-expectancy-delta", type=float, default=0.0)
    parser.add_argument("--max-drawdown-degrade-cap", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not ensure_signed_approval(
        "strategy_tune_cycle", apply_requested=args.apply
    ):
        return 2

    optimize_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "run_optimization_batch.py"),
        "--runs",
        str(args.deep_runs),
        "--rounds",
        str(max(args.rounds, 8)),
        "--stall-rounds",
        str(max(args.stall_rounds, 3)),
        "--seed-start",
        str(args.seed),
        "--min-oos-pf",
        str(args.min_oos_pf),
        "--min-oos-pf-margin",
        str(args.min_oos_pf_delta),
        "--min-trades",
        str(args.min_trades),
        "--max-drawdown-degrade",
        str(args.max_drawdown_degrade_cap),
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    rc = _run(optimize_cmd)
    if rc != 0:
        return rc

    rank_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "rank_optimization_candidates.py"),
        "--min-oos-pf",
        str(args.min_oos_pf),
        "--min-trades",
        str(args.min_trades),
    ]
    rc = _run(rank_cmd)
    if rc != 0:
        return 1
    ranking_artifacts = sorted((SKILL_DIR / "validation_artifacts").glob("optimization_candidate_ranking_*.json"))
    if not ranking_artifacts:
        print("No optimization candidate ranking artifact found.")
        return 1
    ranking = ranking_artifacts[-1]

    decision_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "decide_strategy_promotion.py"),
        "--ranking-artifact",
        str(ranking),
        "--min-oos-pf",
        str(args.min_oos_pf),
        "--min-oos-pf-delta",
        str(args.min_oos_pf_delta),
        "--min-pf-delta",
        str(args.min_pf_delta),
        "--min-expectancy-delta",
        str(args.min_expectancy_delta),
        "--max-drawdown-degrade-cap",
        str(args.max_drawdown_degrade_cap),
        "--min-trades-threshold",
        str(args.min_trades),
    ]
    if args.apply:
        decision_cmd.append("--apply")
    rc = _run(decision_cmd)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = SKILL_DIR / "validation_artifacts" / f"strategy_tune_cycle_summary_{run_id}.json"
    decision_artifacts = sorted((SKILL_DIR / "validation_artifacts").glob("strategy_promotion_decision_*.json"))
    latest_decision = str(decision_artifacts[-1]) if decision_artifacts else None
    summary = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": rc == 0,
        "go_no_go": "go" if rc == 0 else "no_go",
        "selection_policy": {
            "market_scope": "equities_only",
            "mode": "robust",
            "deep_runs": int(args.deep_runs),
        },
        "gates": {
            "min_oos_pf": float(args.min_oos_pf),
            "min_oos_pf_delta": float(args.min_oos_pf_delta),
            "min_pf_delta": float(args.min_pf_delta),
            "min_expectancy_delta": float(args.min_expectancy_delta),
            "max_drawdown_degrade_cap": float(args.max_drawdown_degrade_cap),
            "min_trades": int(args.min_trades),
        },
        "artifacts": {
            "ranking": str(ranking),
            "promotion_decision": latest_decision,
        },
    }
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Tune summary artifact: {out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
