#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from agent_intelligence import apply_meta_policy_to_signal, compute_uncertainty_score
from config import (
    get_meta_policy_mode,
    get_meta_policy_size_mult_max,
    get_meta_policy_size_mult_min,
    get_mirofish_weighting_mode,
    get_uncertainty_mode,
)


def main() -> int:
    skill_dir = SKILL_DIR
    failures: list[str] = []

    if get_mirofish_weighting_mode(skill_dir) not in {"off", "shadow", "live"}:
        failures.append("invalid MIROFISH_WEIGHTING_MODE")
    if get_meta_policy_mode(skill_dir) not in {"off", "shadow", "live"}:
        failures.append("invalid META_POLICY_MODE")
    if get_uncertainty_mode(skill_dir) not in {"off", "shadow", "live"}:
        failures.append("invalid UNCERTAINTY_MODE")

    size_min = float(get_meta_policy_size_mult_min(skill_dir))
    size_max = float(get_meta_policy_size_mult_max(skill_dir))
    if size_min > size_max:
        failures.append("META_POLICY_SIZE_MULT_MIN > META_POLICY_SIZE_MULT_MAX")

    test_signal = {
        "ticker": "AAPL",
        "signal_score": 60.0,
        "mirofish_conviction": 25.0,
        "mirofish_disagreement": 0.5,
        "prediction_market": {
            "features": {"pm_uncertainty": 0.7, "pm_market_quality_score": 0.4},
            "overlay": {"confidence": 0.5, "score_delta": 0.5},
        },
        "advisory": {"p_up_10d": 0.56, "confidence_bucket": "medium"},
        "_data_quality": "ok",
    }
    uncertainty = compute_uncertainty_score(test_signal)
    score = float(uncertainty.get("score", 0.0))
    if not (0.0 <= score <= 1.0):
        failures.append("uncertainty score out of bounds")

    out_signal, keep = apply_meta_policy_to_signal(
        signal=test_signal,
        diagnostics={
            "meta_policy_processed": 0,
            "meta_policy_suppressed": 0,
            "meta_policy_downsized": 0,
            "meta_policy_applied": 0,
            "meta_policy_shadow_actions": 0,
            "uncertainty_high_count": 0,
            "uncertainty_medium_count": 0,
        },
        skill_dir=skill_dir,
    )
    _ = keep
    meta = out_signal.get("meta_policy")
    if not isinstance(meta, dict):
        failures.append("meta_policy payload missing")

    if failures:
        for line in failures:
            print(f"FAIL: {line}")
        return 1
    print("validate_agent_intelligence: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
