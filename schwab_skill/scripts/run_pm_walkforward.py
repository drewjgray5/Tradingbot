#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from prediction_market_experiment import (  # noqa: E402
    ExperimentPaths,
    run_ab_walkforward_experiment,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run prediction-market A/B walk-forward with untouched holdout")
    parser.add_argument("--start-date", required=True, help="Evaluation start date YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Evaluation end date YYYY-MM-DD")
    parser.add_argument("--holdout-start", required=True, help="Holdout start date YYYY-MM-DD")
    parser.add_argument("--universe-file", required=True, help="Frozen universe JSON path")
    parser.add_argument("--pm-historical-file", required=True, help="Historical PM snapshot store JSON path")
    parser.add_argument("--train-window-days", type=int, default=365)
    parser.add_argument("--step-days", type=int, default=120)
    args = parser.parse_args()

    universe = Path(args.universe_file)
    pm_hist = Path(args.pm_historical_file)
    if not universe.exists():
        raise SystemExit(f"Universe file missing: {universe}")
    if not pm_hist.exists():
        raise SystemExit(f"PM historical file missing: {pm_hist}")

    out = run_ab_walkforward_experiment(
        start_date=args.start_date,
        end_date=args.end_date,
        holdout_start=args.holdout_start,
        paths=ExperimentPaths(universe_file=universe, pm_historical_file=pm_hist),
        skill_dir=SKILL_DIR,
        train_window_days=max(60, int(args.train_window_days)),
        step_days=max(20, int(args.step_days)),
    )
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
