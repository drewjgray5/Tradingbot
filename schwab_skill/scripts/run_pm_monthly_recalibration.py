#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"


def _load_latest_weekly() -> dict[str, Any]:
    candidates = sorted(ARTIFACT_DIR.glob("pm_weekly_summary_*.json"))
    if not candidates:
        return {}
    try:
        data = json.loads(candidates[-1].read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Monthly PM threshold recalibration guard")
    parser.add_argument(
        "--require-weekly-pass-count",
        type=int,
        default=3,
        help="Minimum count of passing weekly summaries required before recalibration",
    )
    args = parser.parse_args()

    recent = sorted(ARTIFACT_DIR.glob("pm_weekly_summary_*.json"))[-int(max(1, args.require_weekly_pass_count)) :]
    pass_count = 0
    checked = 0
    for path in recent:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        checked += 1
        if bool((data or {}).get("passed")):
            pass_count += 1

    approved = checked >= int(args.require_weekly_pass_count) and pass_count >= int(args.require_weekly_pass_count)
    payload = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": "pm_monthly_recalibration",
        "checked_weekly_reports": checked,
        "passing_weekly_reports": pass_count,
        "required_pass_count": int(args.require_weekly_pass_count),
        "approved_for_recalibration": approved,
        "latest_weekly_summary": _load_latest_weekly().get("run_at"),
        "reason": "sufficient_oos_support" if approved else "insufficient_out_of_sample_support",
    }
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ARTIFACT_DIR / f"pm_monthly_recalibration_{run_id}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"Artifact: {out}")
    return 0 if approved else 1


if __name__ == "__main__":
    raise SystemExit(main())
