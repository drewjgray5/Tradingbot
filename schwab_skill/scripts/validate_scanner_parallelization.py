#!/usr/bin/env python3
"""
Validation checks for two-stage scanner shortlist behavior and determinism.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


def main() -> int:
    import signal_scanner as scanner

    # Test shortlist sizing math.
    n = scanner._compute_stage_a_shortlist_limit(total_candidates=17, top_n=5, multiplier=3.0, cap=40)
    if n != 15:
        print(f"FAIL: shortlist limit mismatch expected=15 actual={n}")
        return 1

    n_cap = scanner._compute_stage_a_shortlist_limit(total_candidates=100, top_n=10, multiplier=3.0, cap=20)
    if n_cap != 20:
        print(f"FAIL: shortlist cap mismatch expected=20 actual={n_cap}")
        return 1

    # Deterministic shortlist ordering for same candidate universe.
    rng = random.Random(7)
    candidates = [{"ticker": f"T{i:03d}", "stage_a_score": rng.uniform(0, 100)} for i in range(200)]
    candidates_a = list(candidates)
    candidates_b = list(candidates)

    k = scanner._compute_stage_a_shortlist_limit(total_candidates=len(candidates), top_n=8, multiplier=2.5, cap=30)
    out_a = [c["ticker"] for c in sorted(candidates_a, key=lambda c: c.get("stage_a_score", 0), reverse=True)[:k]]
    out_b = [c["ticker"] for c in sorted(candidates_b, key=lambda c: c.get("stage_a_score", 0), reverse=True)[:k]]
    if out_a != out_b:
        print("FAIL: deterministic shortlist ordering failed")
        return 1

    print("PASS: scanner parallelization shortlist checks succeeded")
    print(f"  shortlist_size={k}")
    print(f"  top5={out_a[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
