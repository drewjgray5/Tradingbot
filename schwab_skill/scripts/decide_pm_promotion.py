#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
AB_RESULTS_FILE = SKILL_DIR / ".prediction_market_ab_results.json"


def _load_payload(path: Path | None) -> dict[str, Any]:
    if path is not None:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data:
            if isinstance(data[-1], dict):
                return data[-1]
        raise ValueError("Provided artifact does not contain a JSON object payload")
    if not AB_RESULTS_FILE.exists():
        raise ValueError(f"Missing AB results history: {AB_RESULTS_FILE}")
    rows = json.loads(AB_RESULTS_FILE.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("AB results history empty")
    last = rows[-1]
    if not isinstance(last, dict):
        raise ValueError("AB results latest payload invalid")
    return last


def decide_promotion(
    payload: dict[str, Any],
    *,
    max_dd_blowout_pct: float,
    min_regime_buckets: int,
    max_ci_below_zero: float,
) -> tuple[bool, list[str], dict[str, Any]]:
    if str(payload.get("mode") or "") != "ab_walkforward":
        raise ValueError("Promotion gate expects walk-forward payload (mode=ab_walkforward).")

    holdout = payload.get("holdout") or {}
    holdout_paired = holdout.get("paired") or {}
    holdout_ci = holdout_paired.get("mean_net_return_delta_ci95") or [0.0, 0.0]
    holdout_ci_low = float(holdout_ci[0] if len(holdout_ci) >= 1 else 0.0)
    holdout_mean = float(holdout_paired.get("mean_net_return_delta", 0.0) or 0.0)

    holdout_treatment = holdout.get("treatment") or {}
    holdout_dd = float(holdout_treatment.get("max_drawdown_net_pct", 0.0) or 0.0)

    train_rows = [r for r in list(payload.get("windows") or []) if not bool(r.get("is_holdout"))]
    train_dd_worst = min(
        [
            float(((row.get("treatment") or {}).get("max_drawdown_net_pct", 0.0) or 0.0))
            for row in train_rows
        ]
        or [0.0]
    )
    dd_cap = float(train_dd_worst) - float(max_dd_blowout_pct)

    regimes = payload.get("aggregates", {}).get("regime_slices") or {}
    positive_regimes = sum(1 for _, r in regimes.items() if float((r or {}).get("mean_delta", 0) or 0) > 0)

    reasons: list[str] = []
    promote = True
    if holdout_mean <= 0:
        promote = False
        reasons.append(f"holdout_mean_delta_not_positive:{holdout_mean:.8f}")
    if holdout_ci_low < -abs(float(max_ci_below_zero)):
        promote = False
        reasons.append(
            f"holdout_ci_low_too_negative:{holdout_ci_low:.8f}<-{abs(float(max_ci_below_zero)):.8f}"
        )
    if float(holdout_dd) < float(dd_cap):
        promote = False
        reasons.append(f"holdout_drawdown_blowout:{holdout_dd:.4f}<{dd_cap:.4f}")
    if positive_regimes < int(min_regime_buckets):
        promote = False
        reasons.append(
            f"regime_stability_insufficient:{positive_regimes}<{int(min_regime_buckets)}"
        )
    if not reasons:
        reasons.append("promotion_gates_passed")
    checks = {
        "holdout_mean_delta": holdout_mean,
        "holdout_ci_low": holdout_ci_low,
        "holdout_drawdown_pct": holdout_dd,
        "train_drawdown_worst_pct": train_dd_worst,
        "holdout_drawdown_cap_pct": dd_cap,
        "positive_regime_buckets": positive_regimes,
        "regime_buckets_total": len(regimes),
    }
    return promote, reasons, checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Promotion decision gate for prediction-market walk-forward results")
    parser.add_argument("--artifact", default="", help="Optional walk-forward JSON artifact path")
    parser.add_argument("--max-dd-blowout-pct", type=float, default=2.5)
    parser.add_argument("--min-regime-buckets", type=int, default=2)
    parser.add_argument("--max-ci-below-zero", type=float, default=0.0005)
    args = parser.parse_args()

    artifact = Path(args.artifact) if args.artifact else None
    payload = _load_payload(artifact)
    try:
        promote, reasons, checks = decide_promotion(
            payload,
            max_dd_blowout_pct=float(args.max_dd_blowout_pct),
            min_regime_buckets=int(args.min_regime_buckets),
            max_ci_below_zero=float(args.max_ci_below_zero),
        )
    except ValueError as exc:
        raise SystemExit(str(exc))

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "promote": promote,
        "reasons": reasons,
        "inputs": {
            "artifact": str(artifact) if artifact else str(AB_RESULTS_FILE),
            "max_dd_blowout_pct": float(args.max_dd_blowout_pct),
            "min_regime_buckets": int(args.min_regime_buckets),
            "max_ci_below_zero": float(args.max_ci_below_zero),
        },
        "checks": checks,
    }
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_json = ARTIFACT_DIR / f"pm_promotion_decision_{run_id}.json"
    out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    print(f"Decision artifact: {out_json}")
    return 0 if promote else 1


if __name__ == "__main__":
    raise SystemExit(main())
