#!/usr/bin/env python3
"""
Validate promotion decision edge cases using synthetic champion/challenger metrics.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


def _artifact(
    auc: float,
    brier: float,
    top20: float,
    wf_auc: float,
    wf_top20: float,
    fold_count: int = 6,
) -> dict:
    return {
        "calibration_metrics": {
            "auc": auc,
            "brier": brier,
            "top20_hit_rate": top20,
        },
        "walk_forward": {
            "fold_count": fold_count,
            "summary": {
                "auc": {"mean": wf_auc, "std": 0.01},
                "top20_hit_rate": {"mean": wf_top20, "std": 0.01},
            },
        },
    }


def main() -> int:
    from promotion_utils import decide_promotion

    champion = _artifact(auc=0.55, brier=0.24, top20=0.56, wf_auc=0.54, wf_top20=0.55)
    better = _artifact(auc=0.57, brier=0.235, top20=0.58, wf_auc=0.55, wf_top20=0.56)
    tied = _artifact(auc=0.55, brier=0.24, top20=0.56, wf_auc=0.54, wf_top20=0.55)
    partial = _artifact(auc=0.57, brier=0.245, top20=0.55, wf_auc=0.53, wf_top20=0.54)
    missing = {"calibration_metrics": {}}

    ok = decide_promotion(champion, better, challenger_gates_passed=True, require_walkforward_gain=True)
    if not ok.get("promote"):
        print(f"FAIL: expected promotion for better challenger, reasons={ok.get('reasons')}")
        return 1

    tie = decide_promotion(champion, tied, challenger_gates_passed=True, require_walkforward_gain=True)
    if tie.get("promote"):
        print("FAIL: tie challenger should not promote")
        return 1

    pg = decide_promotion(champion, partial, challenger_gates_passed=True, require_walkforward_gain=True)
    if pg.get("promote"):
        print("FAIL: partial gate pass challenger should not promote")
        return 1

    mg = decide_promotion(champion, missing, challenger_gates_passed=False, require_walkforward_gain=True)
    if mg.get("promote"):
        print("FAIL: missing metrics challenger should not promote")
        return 1

    print("PASS: promotion flow edge-case checks succeeded")
    print(f"  better_decision={ok.get('promote')}")
    print(f"  tie_reasons={tie.get('reasons')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
