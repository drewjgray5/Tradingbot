#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"


def _run(name: str, cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), capture_output=True, text=True)
    return {
        "name": name,
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily PM diagnostics and drift checks")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--universe-file", required=True)
    parser.add_argument("--pm-historical-file", required=True)
    args = parser.parse_args()

    py = sys.executable
    steps = [
        _run(
            "validate_data_integrity",
            [
                py,
                str(SKILL_DIR / "scripts" / "validate_data_integrity.py"),
                "--start-date",
                args.start_date,
                "--end-date",
                args.end_date,
                "--universe-file",
                args.universe_file,
                "--pm-historical-file",
                args.pm_historical_file,
                "--output-prefix",
                "data_integrity_daily",
            ],
        ),
        _run("validate_shadow_mode", [py, str(SKILL_DIR / "scripts" / "validate_shadow_mode.py")]),
    ]
    passed = all(int(s.get("returncode", 1)) == 0 for s in steps)
    payload = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": "pm_daily_diagnostics",
        "passed": passed,
        "steps": steps,
    }
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ARTIFACT_DIR / f"pm_daily_diagnostics_{run_id}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"Artifact: {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
