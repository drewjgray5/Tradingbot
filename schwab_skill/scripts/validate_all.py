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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Steps that read/write shared on-disk state and must therefore run after every
# other step has completed. Keeping observability last guarantees its baseline
# counters reflect the full run rather than a partial one.
_SEQUENTIAL_TAIL = {"validate_observability_gates"}
# Steps that must run before parallel work because downstream scripts depend
# on artifacts they emit (e.g. healthcheck token refresh).
_SEQUENTIAL_HEAD = {"healthcheck"}

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
    pm_cadence: bool,
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
        ("validate_agent_intelligence", [py, str(SCRIPTS_DIR / "validate_agent_intelligence.py")], None),
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
    if pm_cadence:
        steps.append(
            (
                "run_pm_monthly_recalibration",
                [py, str(SCRIPTS_DIR / "run_pm_monthly_recalibration.py"), "--require-weekly-pass-count", "1"],
                None,
            )
        )

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
    parser.add_argument(
        "--pm-cadence",
        action="store_true",
        help="Include PM cadence guard checks in the validation run.",
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=1,
        help=(
            "Run independent steps in parallel using a thread pool. Defaults to 1 (sequential). "
            "Pre-seed (healthcheck) and observability steps still run sequentially to preserve "
            "side-effect ordering."
        ),
    )
    parser.add_argument(
        "--baseline",
        default="",
        help=(
            "Optional path to a prior summary JSON. When set, the run emits a "
            "`baseline_delta` block listing steps whose status flipped vs. the baseline. "
            "Use the literal value 'latest' to compare against "
            "validation_artifacts/latest_validation_report.json from the previous run."
        ),
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help=(
            "When --baseline is set, exit non-zero if any step that was passing "
            "in the baseline is now failing. Useful in scheduled CI to surface "
            "drift even when --strict isn't on."
        ),
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
        args.pm_cadence,
        _read_baseline_execution_events(),
    )

    head_steps = [s for s in steps if s[0] in _SEQUENTIAL_HEAD]
    middle_steps = [s for s in steps if s[0] not in _SEQUENTIAL_HEAD and s[0] not in _SEQUENTIAL_TAIL]
    tail_steps = [s for s in steps if s[0] in _SEQUENTIAL_TAIL]

    aborted = False

    def _emit(step: dict[str, Any]) -> None:
        status = "PASS" if step["returncode"] == 0 else "FAIL"
        print(f"{status}: {step['name']}")
        if step.get("stdout"):
            print(step["stdout"])
        if step.get("stderr"):
            print(step["stderr"])

    def _run_sequential(group: list[tuple[str, list[str], dict[str, str] | None]]) -> bool:
        nonlocal aborted
        for name, cmd, env_overrides in group:
            if aborted:
                return False
            step = _run_step(name, cmd, env_overrides)
            results.append(step)
            _emit(step)
            if args.strict and step["returncode"] != 0:
                aborted = True
                return False
        return True

    _run_sequential(head_steps)

    if not aborted and middle_steps:
        max_workers = max(1, int(args.max_parallel or 1))
        if max_workers <= 1:
            _run_sequential(middle_steps)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_run_step, name, cmd, env_overrides): name
                    for name, cmd, env_overrides in middle_steps
                }
                for fut in as_completed(futures):
                    step = fut.result()
                    results.append(step)
                    _emit(step)
                    if args.strict and step["returncode"] != 0:
                        aborted = True
                        # Cancel remaining queued tasks; running tasks finish naturally.
                        for pending in futures:
                            if not pending.done():
                                pending.cancel()
                        break

    if not aborted:
        _run_sequential(tail_steps)

    # Sort results into the original step order so artifacts read consistently.
    order = {name: idx for idx, (name, *_rest) in enumerate(steps)}
    results.sort(key=lambda r: order.get(r.get("name", ""), 1_000_000))

    failed = [r for r in results if r["returncode"] != 0]
    summary: dict[str, Any] = {
        "profile": args.profile,
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": len(failed) == 0,
        "failed_steps": [r["name"] for r in failed],
        "max_parallel": int(args.max_parallel or 1),
        "results": results,
    }

    delta: dict[str, Any] = {}
    if args.baseline:
        # Resolve "latest" alias to the previous run's stable artifact path
        # so CI can simply pass --baseline latest after restoring the prior
        # `validation_artifacts/` from cache.
        baseline_arg = (
            str(ARTIFACT_DIR / "latest_validation_report.json")
            if args.baseline.strip().lower() == "latest"
            else args.baseline
        )
        delta = _baseline_delta(baseline_arg, summary)
        if delta:
            summary["baseline_delta"] = delta
            print("Baseline delta:")
            for line in _format_delta(delta):
                print(f"  {line}")
            # Stable path for downstream tooling (Slack alerts, dashboards)
            # so they don't have to re-parse the full summary JSON.
            delta_path = ARTIFACT_DIR / "latest_baseline_delta.json"
            delta_path.write_text(json.dumps(delta, indent=2), encoding="utf-8")
            print(f"Baseline delta artifact: {delta_path}")

    blob = json.dumps(summary, indent=2)
    out_path = ARTIFACT_DIR / f"validate_all_{run_id}_{args.profile}.json"
    out_path.write_text(blob, encoding="utf-8")
    print(f"Summary artifact: {out_path}")
    latest = ARTIFACT_DIR / "latest_validation_report.json"
    latest.write_text(blob, encoding="utf-8")
    print(f"Latest summary (stable path): {latest}")

    if (
        args.fail_on_regression
        and delta
        and delta.get("regressed")
        and summary["passed"]
    ):
        # Don't double-fail: when summary is already failing we let the
        # regular non-zero exit take precedence. Only override the exit
        # code when the run looked clean but a regression vs. baseline
        # snuck in.
        print(
            "Exiting non-zero due to --fail-on-regression with regressed steps: "
            + ", ".join(delta["regressed"])
        )
        return 1

    return 0 if summary["passed"] else 1


def _baseline_delta(baseline_path: str, summary: dict[str, Any]) -> dict[str, Any]:
    """Compare the new summary against a prior one. Returns an empty dict if
    the baseline is missing or unreadable."""
    p = Path(baseline_path)
    if not p.is_absolute():
        p = SKILL_DIR / baseline_path
    try:
        prev = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    def _status_map(rows: list[dict[str, Any]]) -> dict[str, str]:
        # NB: ``r.get("returncode", 1) or 1`` was the previous form here
        # and silently treated successful steps (returncode == 0) as
        # failures because ``0 or 1`` collapses to ``1``. That made
        # regressed/recovered always empty. Use a fall-back default
        # instead so genuine zeros survive.
        out: dict[str, str] = {}
        for r in rows or []:
            name = r.get("name", "")
            if not name:
                continue
            rc_raw = r.get("returncode", 1)
            try:
                rc = int(rc_raw if rc_raw is not None else 1)
            except (TypeError, ValueError):
                rc = 1
            out[name] = "pass" if rc == 0 else "fail"
        return out

    prev_status = _status_map(prev.get("results") or [])
    new_status = _status_map(summary.get("results") or [])
    regressed = sorted(n for n in new_status if prev_status.get(n) == "pass" and new_status[n] == "fail")
    recovered = sorted(n for n in new_status if prev_status.get(n) == "fail" and new_status[n] == "pass")
    new_steps = sorted(n for n in new_status if n not in prev_status)
    removed_steps = sorted(n for n in prev_status if n not in new_status)
    return {
        "baseline_path": str(p),
        "baseline_passed": bool(prev.get("passed")),
        "regressed": regressed,
        "recovered": recovered,
        "new_steps": new_steps,
        "removed_steps": removed_steps,
    }


def _format_delta(delta: dict[str, Any]) -> list[str]:
    out = []
    if delta.get("regressed"):
        out.append(f"REGRESSED: {', '.join(delta['regressed'])}")
    if delta.get("recovered"):
        out.append(f"RECOVERED: {', '.join(delta['recovered'])}")
    if delta.get("new_steps"):
        out.append(f"NEW: {', '.join(delta['new_steps'])}")
    if delta.get("removed_steps"):
        out.append(f"REMOVED: {', '.join(delta['removed_steps'])}")
    if not out:
        out.append("no status changes vs baseline")
    return out


if __name__ == "__main__":
    raise SystemExit(main())

