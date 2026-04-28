#!/usr/bin/env python3
"""
Validate regime counterfactual evidence from phase1 diagnostics.

Focuses on whether requiring SPY above 50 SMA + rising 200 SMA improves
expectancy/PF in difficult regimes with acceptable trade retention.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
sys.path.insert(0, str(SKILL_DIR))

TARGET_COHORT = "spy_above_50sma_AND_rising"
BASELINE_COHORT = "all"


def _load_phase1_payload(run_id: str, *, refresh: bool) -> dict[str, Any]:
    artifact = ARTIFACT_DIR / f"phase1_diagnostics_{run_id}.json"
    if refresh or not artifact.exists():
        cmd = [
            sys.executable,
            str(SKILL_DIR / "scripts" / "phase1_trade_diagnostics.py"),
            "--run-id",
            run_id,
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(SKILL_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "phase1 diagnostics generation failed: "
                + ((proc.stderr or proc.stdout or "").strip()[-500:])
            )
    return json.loads(artifact.read_text(encoding="utf-8"))


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.3f}%"


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate regime counterfactual guardrail evidence")
    parser.add_argument("--run-id", default="control_legacy", help="Phase1 diagnostics run id")
    parser.add_argument(
        "--min-bear-pf-delta",
        type=float,
        default=0.20,
        help="Minimum PF improvement required in bear_rates.",
    )
    parser.add_argument(
        "--min-bear-keep-pct",
        type=float,
        default=0.60,
        help="Minimum trade retention ratio (0-1) in bear_rates for target cohort.",
    )
    parser.add_argument(
        "--min-bear-exp-delta",
        type=float,
        default=0.005,
        help="Minimum expectancy delta required in bear_rates (decimal form).",
    )
    parser.add_argument(
        "--min-chop-exp-delta",
        type=float,
        default=0.001,
        help="Minimum expectancy delta required in volatility_chop (decimal form).",
    )
    parser.add_argument(
        "--refresh-artifact",
        action="store_true",
        help="Rebuild phase1 diagnostics artifact before validation.",
    )
    args = parser.parse_args()

    try:
        payload = _load_phase1_payload(args.run_id, refresh=args.refresh_artifact)
    except Exception as exc:
        print(f"FAIL: unable to load phase1 diagnostics: {exc}")
        return 1

    trade_count = int(payload.get("trade_count", 0) or 0)
    counterfactual = ((payload.get("analysis") or {}).get("counterfactual_regime") or {})
    if not isinstance(counterfactual, dict) or not counterfactual:
        if trade_count <= 0:
            print("PASS: regime counterfactual guardrail skipped (no phase1 trades available)")
            return 0
        print("FAIL: counterfactual diagnostics missing from artifact")
        return 1

    required_eras = ("bear_rates", "volatility_chop")
    missing = [era for era in required_eras if era not in counterfactual]
    if missing:
        if trade_count <= 0:
            print("PASS: regime counterfactual guardrail skipped (no phase1 trades available)")
            return 0
        print(f"FAIL: missing required eras in counterfactual diagnostics: {missing}")
        return 1

    failures: list[str] = []
    print("Regime counterfactual evidence")

    for era in required_eras:
        era_data = counterfactual.get(era) or {}
        base = era_data.get(BASELINE_COHORT) or {}
        filt = era_data.get(TARGET_COHORT) or {}

        base_pf = _as_float(base.get("pf"))
        filt_pf = _as_float(filt.get("pf"))
        base_exp = _as_float(base.get("expectancy"))
        filt_exp = _as_float(filt.get("expectancy"))
        keep_pct = _as_float(filt.get("kept_pct"))

        pf_delta = None if base_pf is None or filt_pf is None else (filt_pf - base_pf)
        exp_delta = None if base_exp is None or filt_exp is None else (filt_exp - base_exp)
        keep_ratio = None if keep_pct is None else keep_pct / 100.0

        print(
            f"  {era}: "
            f"all_pf={base_pf}, filt_pf={filt_pf}, pf_delta={pf_delta}, "
            f"all_exp={_fmt_pct(base_exp)}, filt_exp={_fmt_pct(filt_exp)}, "
            f"exp_delta={_fmt_pct(exp_delta)}, kept={keep_pct}%"
        )

        if era == "bear_rates":
            if pf_delta is None or pf_delta < float(args.min_bear_pf_delta):
                failures.append(
                    "bear_pf_delta_below_threshold:"
                    f"{pf_delta}<{float(args.min_bear_pf_delta)}"
                )
            if exp_delta is None or exp_delta < float(args.min_bear_exp_delta):
                failures.append(
                    "bear_expectancy_delta_below_threshold:"
                    f"{exp_delta}<{float(args.min_bear_exp_delta)}"
                )
            if keep_ratio is None or keep_ratio < float(args.min_bear_keep_pct):
                failures.append(
                    "bear_kept_ratio_below_threshold:"
                    f"{keep_ratio}<{float(args.min_bear_keep_pct)}"
                )
        if era == "volatility_chop":
            if exp_delta is None or exp_delta < float(args.min_chop_exp_delta):
                failures.append(
                    "chop_expectancy_delta_below_threshold:"
                    f"{exp_delta}<{float(args.min_chop_exp_delta)}"
                )

    if failures:
        print(f"FAIL: regime counterfactual guardrail validation failed: {failures}")
        return 1

    print("PASS: regime counterfactual guardrail validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
