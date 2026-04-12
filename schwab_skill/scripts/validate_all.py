#!/usr/bin/env python3
"""
Unified validation pipeline for local, CI, server, and container profiles.
Writes a machine-readable summary artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"


def _run_step(name: str, cmd: list[str], env_overrides: dict[str, str] | None = None) -> dict[str, Any]:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    started = datetime.now(timezone.utc)
    proc = subprocess.run(
        cmd,
        cwd=str(SKILL_DIR),
        capture_output=True,
        text=True,
        env=env,
    )
    ended = datetime.now(timezone.utc)
    return {
        "name": name,
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def _steps_for_profile(
    profile: str,
    web_base_url: str,
    skip_backtest: bool,
    promotion: bool,
    pf_robust: bool,
    baseline_execution_events: dict[str, int] | None = None,
) -> list[tuple[str, list[str], dict[str, str] | None]]:
    py = sys.executable
    advisory_cmd = [py, str(SCRIPTS_DIR / "validate_advisory_model.py")]
    if promotion:
        advisory_cmd += ["--strict", "--promotion"]
    steps: list[tuple[str, list[str], dict[str, str] | None]] = [
        ("validate_hypothesis_chain", [py, str(SCRIPTS_DIR / "validate_hypothesis_chain.py")], None),
        ("validate_plugin_modes", [py, str(SCRIPTS_DIR / "validate_plugin_modes.py")], None),
        ("validate_execution_quality", [py, str(SCRIPTS_DIR / "validate_execution_quality.py")], None),
        ("validate_exit_manager", [py, str(SCRIPTS_DIR / "validate_exit_manager.py")], None),
        ("validate_event_risk", [py, str(SCRIPTS_DIR / "validate_event_risk.py")], None),
        ("validate_regime_v2", [py, str(SCRIPTS_DIR / "validate_regime_v2.py")], None),
        ("validate_signal_quality", [py, str(SCRIPTS_DIR / "validate_signal_quality.py")], None),
        ("validate_scanner_parallelization", [py, str(SCRIPTS_DIR / "validate_scanner_parallelization.py")], None),
        ("validate_ui_payloads", [py, str(SCRIPTS_DIR / "validate_ui_payloads.py")], None),
        ("validate_shadow_mode", [py, str(SCRIPTS_DIR / "validate_shadow_mode.py")], None),
        ("validate_advisory_model", advisory_cmd, None),
        ("validate_promotion_flow", [py, str(SCRIPTS_DIR / "validate_promotion_flow.py")], None),
        ("validation_smoke", [py, str(SCRIPTS_DIR / "validation_smoke.py")], None),
    ]

    if profile in {"local", "server"}:
        steps.append(("healthcheck", [py, str(SKILL_DIR / "healthcheck.py")], None))
        sec_env = {"SEC_ENRICHMENT_ENABLED": "true", "SEC_TAGGING_ENABLED": "true"}
        steps.append(("validate_sec_enrichment", [py, str(SCRIPTS_DIR / "validate_sec_enrichment.py")], sec_env))

    if profile in {"ci", "container"} and not skip_backtest:
        backtest_cmd = [py, str(SCRIPTS_DIR / "validate_backtest.py"), "--tickers", "20"]
        if promotion:
            backtest_cmd.append("--promotion")
        steps.append(("validate_backtest", backtest_cmd, None))
    if pf_robust:
        steps.append(("validate_pf_robustness", [py, str(SCRIPTS_DIR / "validate_pf_robustness.py")], None))

    obs_cmd = [
        py,
        str(SCRIPTS_DIR / "validate_observability_gates.py"),
        "--days",
        "1",
        "--max-stop-failures",
        "3",
    ]
    if baseline_execution_events:
        obs_cmd += ["--baseline-events-json", json.dumps(baseline_execution_events, separators=(",", ":"))]
    if web_base_url:
        obs_cmd += ["--web-base-url", web_base_url]
    steps.append(("validate_observability_gates", obs_cmd, None))
    return steps


def _read_baseline_execution_events() -> dict[str, int]:
    path = SKILL_DIR / "execution_safety_metrics.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        day_key = date.today().isoformat()
        day_events = (((data or {}).get("days") or {}).get(day_key) or {}).get("events") or {}
        out: dict[str, int] = {}
        if isinstance(day_events, dict):
            for k, v in day_events.items():
                out[str(k)] = int(v or 0)
        return out
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full validation pipeline and emit summary artifact")
    parser.add_argument(
        "--profile",
        choices=["local", "server", "container", "ci"],
        default="local",
        help="Validation profile with environment-appropriate gates",
    )
    parser.add_argument(
        "--web-base-url",
        default="",
        help="Optional web API base URL for observability gate checks",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail immediately on first failed step",
    )
    parser.add_argument(
        "--skip-backtest",
        action="store_true",
        help="Skip backtest step (faster smoke pass for constrained environments)",
    )
    parser.add_argument(
        "--promotion",
        action="store_true",
        help="Enable promotion-grade advisory and backtest validation gates.",
    )
    parser.add_argument(
        "--pf-robust",
        action="store_true",
        help="Run PF robustness checks across multiple windows/universe sizes.",
    )
    args = parser.parse_args()

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results: list[dict[str, Any]] = []

    steps = _steps_for_profile(
        args.profile,
        args.web_base_url,
        args.skip_backtest,
        args.promotion,
        args.pf_robust,
        _read_baseline_execution_events(),
    )
    for name, cmd, env_overrides in steps:
        step = _run_step(name, cmd, env_overrides)
        results.append(step)
        status = "PASS" if step["returncode"] == 0 else "FAIL"
        print(f"{status}: {name}")
        if step["stdout"]:
            print(step["stdout"])
        if step["stderr"]:
            print(step["stderr"])
        if args.strict and step["returncode"] != 0:
            break

    failed = [r for r in results if r["returncode"] != 0]
    summary = {
        "profile": args.profile,
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": len(failed) == 0,
        "failed_steps": [r["name"] for r in failed],
        "results": results,
    }
    blob = json.dumps(summary, indent=2)
    out_path = ARTIFACT_DIR / f"validate_all_{run_id}_{args.profile}.json"
    out_path.write_text(blob, encoding="utf-8")
    print(f"Summary artifact: {out_path}")
    latest = ARTIFACT_DIR / "latest_validation_report.json"
    latest.write_text(blob, encoding="utf-8")
    print(f"Latest summary (stable path): {latest}")

    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

