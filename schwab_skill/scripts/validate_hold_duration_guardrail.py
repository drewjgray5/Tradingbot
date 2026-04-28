#!/usr/bin/env python3
"""
Validate that edge concentration is strongest in longer hold buckets.

Reads phase1 diagnostics artifacts and asserts that 21-40d holds outperform
shorter holds (0-20d) by expectancy, both globally and across eras.
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

SHORT_BUCKETS = {"0-5d", "6-10d", "11-20d"}
LONG_BUCKET = "21-40d"


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


def _weighted_expectancy(rows: list[dict[str, Any]], buckets: set[str]) -> tuple[float | None, int]:
    selected = [r for r in rows if str(r.get("bucket")) in buckets and int(r.get("n", 0) or 0) > 0]
    total_n = sum(int(r.get("n", 0) or 0) for r in selected)
    if total_n <= 0:
        return None, 0
    weighted = 0.0
    for row in selected:
        n = int(row.get("n", 0) or 0)
        exp = row.get("expectancy")
        if exp is None:
            continue
        weighted += float(exp) * n
    return weighted / total_n, total_n


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.3f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate hold-duration guardrail evidence")
    parser.add_argument("--run-id", default="control_legacy", help="Phase1 diagnostics run id")
    parser.add_argument(
        "--min-global-delta",
        type=float,
        default=0.02,
        help="Minimum long-short expectancy delta required globally (decimal form).",
    )
    parser.add_argument(
        "--min-era-passes",
        type=int,
        default=4,
        help="Minimum eras where long expectancy must exceed short expectancy.",
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

    hold_buckets = ((payload.get("analysis") or {}).get("hold_buckets") or {})
    if not isinstance(hold_buckets, dict) or not hold_buckets:
        print("FAIL: hold bucket diagnostics missing from artifact")
        return 1

    era_rows: list[dict[str, Any]] = []
    aggregate_long_sum = 0.0
    aggregate_long_n = 0
    aggregate_short_sum = 0.0
    aggregate_short_n = 0
    era_passes = 0

    for era, rows_any in hold_buckets.items():
        rows = rows_any if isinstance(rows_any, list) else []
        long_exp, long_n = _weighted_expectancy(rows, {LONG_BUCKET})
        short_exp, short_n = _weighted_expectancy(rows, SHORT_BUCKETS)
        delta = None if long_exp is None or short_exp is None else long_exp - short_exp
        if delta is not None and delta > 0:
            era_passes += 1
        if long_exp is not None and long_n > 0:
            aggregate_long_sum += long_exp * long_n
            aggregate_long_n += long_n
        if short_exp is not None and short_n > 0:
            aggregate_short_sum += short_exp * short_n
            aggregate_short_n += short_n

        era_rows.append(
            {
                "era": str(era),
                "long_n": long_n,
                "long_exp": long_exp,
                "short_n": short_n,
                "short_exp": short_exp,
                "delta": delta,
            }
        )

    if aggregate_long_n <= 0 or aggregate_short_n <= 0:
        print("FAIL: insufficient hold bucket samples to validate guardrail")
        return 1

    global_long_exp = aggregate_long_sum / aggregate_long_n
    global_short_exp = aggregate_short_sum / aggregate_short_n
    global_delta = global_long_exp - global_short_exp

    print("Hold-duration guardrail evidence")
    print(
        "Global: "
        f"long(21-40d) n={aggregate_long_n} exp={_fmt_pct(global_long_exp)} | "
        f"short(0-20d) n={aggregate_short_n} exp={_fmt_pct(global_short_exp)} | "
        f"delta={_fmt_pct(global_delta)}"
    )
    for row in era_rows:
        print(
            f"  {row['era']}: "
            f"long n={row['long_n']} exp={_fmt_pct(row['long_exp'])}, "
            f"short n={row['short_n']} exp={_fmt_pct(row['short_exp'])}, "
            f"delta={_fmt_pct(row['delta'])}"
        )

    failures: list[str] = []
    if global_delta < float(args.min_global_delta):
        failures.append(
            "global_long_minus_short_delta_below_threshold:"
            f"{global_delta:.6f}<{float(args.min_global_delta):.6f}"
        )
    if era_passes < int(args.min_era_passes):
        failures.append(
            f"insufficient_era_passes:{era_passes}<{int(args.min_era_passes)}"
        )

    if failures:
        print(f"FAIL: hold-duration guardrail validation failed: {failures}")
        return 1

    print(
        "PASS: hold-duration guardrail validated "
        f"(global_delta={_fmt_pct(global_delta)}, era_passes={era_passes}/{len(era_rows)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
