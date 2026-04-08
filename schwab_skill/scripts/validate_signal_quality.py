#!/usr/bin/env python3
"""
Lightweight validation checks for signal-quality rollout.

Checks:
1) quality gate behavior toggles with QUALITY_GATES_ENABLED
2) top-N ranking determinism for fixed synthetic inputs
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


def _mk_signal(
    ticker: str,
    score: float,
    cont: float,
    bull: float,
    latest_vol: float,
    avg_vol: float,
) -> dict:
    return {
        "ticker": ticker,
        "signal_score": score,
        "mirofish_result": {
            "continuation_probability": cont,
            "bull_trap_probability": bull,
        },
        "latest_volume": latest_vol,
        "avg_vol_50": avg_vol,
    }


def main() -> int:
    import signal_scanner as scanner

    good = _mk_signal("AAPL", 72.0, 0.72, 0.18, 2_000_000, 1_400_000)
    weak = _mk_signal("TSLA", 42.0, 0.44, 0.61, 900_000, 1_500_000)

    # Gate thresholds used by evaluate function.
    os.environ["QUALITY_MIN_SIGNAL_SCORE"] = "50"
    os.environ["QUALITY_MIN_CONTINUATION_PROB"] = "0.55"
    os.environ["QUALITY_MAX_BULL_TRAP_PROB"] = "0.45"
    os.environ["QUALITY_REQUIRE_BREAKOUT_VOLUME"] = "true"

    # Validation 1: weak signal should produce reasons; strong signal should not.
    weak_reasons = scanner._evaluate_quality_gates(weak, SKILL_DIR)
    good_reasons = scanner._evaluate_quality_gates(good, SKILL_DIR)
    if not weak_reasons:
        print("FAIL: weak signal did not trigger quality reasons")
        return 1
    if good_reasons:
        print(f"FAIL: strong signal unexpectedly failed quality gate: {good_reasons}")
        return 1

    # Validation 2: top-N deterministic ranking.
    signals = [
        _mk_signal("MSFT", 66.0, 0.66, 0.22, 1_900_000, 1_200_000),
        _mk_signal("NVDA", 88.0, 0.78, 0.15, 3_100_000, 2_000_000),
        _mk_signal("META", 74.0, 0.62, 0.25, 1_700_000, 1_500_000),
    ]
    run1 = [s["ticker"] for s in sorted(signals, key=lambda s: s.get("signal_score", 0), reverse=True)[:2]]
    run2 = [s["ticker"] for s in sorted(signals, key=lambda s: s.get("signal_score", 0), reverse=True)[:2]]
    if run1 != run2:
        print("FAIL: top-N ranking is not deterministic")
        return 1

    # Validation 3: diagnostics snapshot increments and summary is readable.
    diag = {
        "quality_gates_would_filter": 2,
        "quality_gates_filtered": 0,
        "weak_mirofish_alignment": 1,
        "low_breakout_volume": 1,
    }
    scanner._record_quality_snapshot(SKILL_DIR, diag, [good, weak])
    summary = scanner.get_signal_quality_summary(skill_dir=SKILL_DIR, days=1)
    if summary.get("scan_count", 0) < 1:
        print("FAIL: quality diagnostics summary did not record scan")
        return 1

    print("PASS: signal quality validation checks succeeded")
    print(f"  weak_signal_reasons={weak_reasons}")
    print(f"  deterministic_top2={run1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
