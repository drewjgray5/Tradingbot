#!/usr/bin/env python3
"""
Server-side heavy strategy validation loop.

Designed for scheduled hosts (Task Scheduler/systemd/cron), separate from fast CI.
Runs heavy checks and emits a compact status artifact for dashboard visibility.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
STATUS_FILE = ARTIFACT_DIR / "continuous_validation_status.json"


def _run_step(name: str, cmd: list[str]) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), capture_output=True, text=True)
    ended = datetime.now(timezone.utc)
    return {
        "name": name,
        "command": " ".join(cmd),
        "returncode": int(proc.returncode),
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "stdout_tail": (proc.stdout or "").strip()[-2000:],
        "stderr_tail": (proc.stderr or "").strip()[-2000:],
    }


def _latest_artifact(pattern: str) -> str | None:
    files = sorted(ARTIFACT_DIR.glob(pattern))
    if not files:
        return None
    try:
        return str(files[-1].relative_to(SKILL_DIR))
    except ValueError:
        return str(files[-1])


def _write_status(payload: dict[str, Any]) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run heavy strategy validation/tuning cycle.")
    parser.add_argument("--skip-backtest", action="store_true", help="Skip weekly promotion backtest step.")
    parser.add_argument("--skip-tune", action="store_true", help="Skip walk-forward tune cycle.")
    parser.add_argument("--strict", action="store_true", help="Stop at first failure.")
    args = parser.parse_args()

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    py = sys.executable
    steps: list[tuple[str, list[str]]] = [
        ("validate_all_server", [py, str(SCRIPTS_DIR / "validate_all.py"), "--profile", "server", "--strict"]),
    ]
    if not args.skip_tune:
        steps.append(("run_strategy_tune_cycle", [py, str(SCRIPTS_DIR / "run_strategy_tune_cycle.py")]))
    if not args.skip_backtest:
        steps.append(
            (
                "validate_backtest_promotion",
                [
                    py,
                    str(SCRIPTS_DIR / "validate_backtest.py"),
                    "--promotion",
                    "--warn-on-regression",
                ],
            )
        )

    run_started = datetime.now(timezone.utc).isoformat()
    total_steps = len(steps)
    results: list[dict[str, Any]] = []
    base_status: dict[str, Any] = {
        "generated_at": run_started,
        "run_status": "running",
        "started_at": run_started,
        "finished_at": None,
        "passed": None,
        "failed_steps": [],
        "total_steps": total_steps,
        "completed_steps": 0,
        "progress_pct": 0,
        "current_step": None,
        "current_step_index": 0,
        "results": [],
        "latest_artifacts": {
            "validate_all": _latest_artifact("validate_all_*_server.json"),
            "walkforward": _latest_artifact("optimization_walkforward_*.json"),
            "candidate_ranking": _latest_artifact("optimization_candidate_ranking_*.json"),
            "strategy_promotion_decision": _latest_artifact("strategy_promotion_decision_*.json"),
            "strategy_promotion_report": _latest_artifact("strategy_promotion_report_*.json"),
            "tune_cycle_summary": _latest_artifact("strategy_tune_cycle_summary_*.json"),
        },
    }
    _write_status(base_status)

    for idx, (name, cmd) in enumerate(steps, start=1):
        base_status.update(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "current_step": name,
                "current_step_index": idx,
                "completed_steps": len(results),
                "progress_pct": int((len(results) / max(1, total_steps)) * 100),
                "results": results,
            }
        )
        _write_status(base_status)
        step = _run_step(name, cmd)
        results.append(step)
        base_status.update(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "completed_steps": len(results),
                "progress_pct": int((len(results) / max(1, total_steps)) * 100),
                "results": results,
            }
        )
        _write_status(base_status)
        if args.strict and step["returncode"] != 0:
            break

    failed = [r["name"] for r in results if int(r.get("returncode", 1)) != 0]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_status": "failed" if failed else "completed",
        "started_at": run_started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "passed": len(failed) == 0,
        "failed_steps": failed,
        "total_steps": total_steps,
        "completed_steps": len(results),
        "progress_pct": int((len(results) / max(1, total_steps)) * 100),
        "current_step": None,
        "current_step_index": len(results),
        "results": results,
        "latest_artifacts": {
            "validate_all": _latest_artifact("validate_all_*_server.json"),
            "walkforward": _latest_artifact("optimization_walkforward_*.json"),
            "candidate_ranking": _latest_artifact("optimization_candidate_ranking_*.json"),
            "strategy_promotion_decision": _latest_artifact("strategy_promotion_decision_*.json"),
            "strategy_promotion_report": _latest_artifact("strategy_promotion_report_*.json"),
            "tune_cycle_summary": _latest_artifact("strategy_tune_cycle_summary_*.json"),
        },
    }
    _write_status(summary)
    print(f"Wrote status artifact: {STATUS_FILE}")
    print(json.dumps({"passed": summary["passed"], "failed_steps": summary["failed_steps"]}, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
