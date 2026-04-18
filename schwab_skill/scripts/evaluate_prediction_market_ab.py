#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from prediction_market_experiment import (  # noqa: E402
    ExperimentPaths,
    load_frozen_universe,
    run_ab_backtest_experiment,
    run_shadow_scan_experiment,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B evaluation for prediction-market overlay")
    parser.add_argument("--start-date", required=True, help="Backtest start date YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Backtest end date YYYY-MM-DD")
    parser.add_argument(
        "--universe-file",
        required=True,
        help="JSON file with frozen universe: {\"as_of\":\"YYYY-MM-DD\",\"tickers\":[...]}",
    )
    parser.add_argument(
        "--pm-historical-file",
        required=True,
        help="Historical PM snapshots JSON for strict point-in-time replay",
    )
    parser.add_argument(
        "--skip-shadow",
        action="store_true",
        help="Skip current-session shadow scan comparison",
    )
    parser.add_argument(
        "--skip-integrity-gate",
        action="store_true",
        help="Skip pre-run data integrity gate (not recommended).",
    )
    args = parser.parse_args()

    universe_path = Path(args.universe_file)
    pm_hist_path = Path(args.pm_historical_file)
    if not universe_path.exists():
        raise SystemExit(f"Universe file missing: {universe_path}")
    if not pm_hist_path.exists():
        raise SystemExit(f"PM historical file missing: {pm_hist_path}")

    if not args.skip_integrity_gate:
        gate_cmd = [
            sys.executable,
            str(SKILL_DIR / "scripts" / "validate_data_integrity.py"),
            "--start-date",
            args.start_date,
            "--end-date",
            args.end_date,
            "--universe-file",
            str(universe_path),
            "--pm-historical-file",
            str(pm_hist_path),
        ]
        gate_proc = subprocess.run(gate_cmd, cwd=str(SKILL_DIR), capture_output=True, text=True)
        if gate_proc.stdout:
            print(gate_proc.stdout.strip())
        if gate_proc.stderr:
            print(gate_proc.stderr.strip())
        if gate_proc.returncode != 0:
            raise SystemExit("Data integrity gate failed; aborting PM A/B evaluation.")

    ab = run_ab_backtest_experiment(
        start_date=args.start_date,
        end_date=args.end_date,
        paths=ExperimentPaths(
            universe_file=universe_path,
            pm_historical_file=pm_hist_path,
        ),
        skill_dir=SKILL_DIR,
    )
    print("A/B backtest complete:")
    print(json.dumps(ab, indent=2))

    if not args.skip_shadow:
        watchlist = load_frozen_universe(universe_path, start_date=args.start_date)
        shadow = run_shadow_scan_experiment(watchlist=watchlist, skill_dir=SKILL_DIR)
        print("\nShadow scan comparison complete:")
        print(json.dumps(shadow, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
