#!/usr/bin/env python3
"""
Release-gate checks for operational telemetry.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


def _fetch_web_metrics(base_url: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/health/deep"
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        return {}
    return (data.get("data") or {}).get("metrics") or {}


def main() -> int:
    from circuit_breaker import discord_circuit, schwab_circuit
    from execution import get_execution_safety_summary

    parser = argparse.ArgumentParser(description="Validate observability release gates")
    parser.add_argument("--days", type=int, default=1, help="Lookback window for safety metrics")
    parser.add_argument("--max-web-error-rate-pct", type=float, default=5.0)
    parser.add_argument("--max-stop-failures", type=int, default=1)
    parser.add_argument("--max-guardrail-blocks", type=int, default=25)
    parser.add_argument("--web-base-url", default="", help="Optional web API base URL, e.g. http://127.0.0.1:8000")
    parser.add_argument(
        "--baseline-events-json",
        default="",
        help="Optional JSON object of pre-existing execution events to subtract from current counts.",
    )
    args = parser.parse_args()

    failures: list[str] = []
    details: dict[str, Any] = {}

    details["schwab_circuit_stable"] = schwab_circuit.connection_stable
    details["discord_circuit_stable"] = discord_circuit.connection_stable
    if not schwab_circuit.connection_stable:
        failures.append("schwab_circuit_unstable")
    if not discord_circuit.connection_stable:
        failures.append("discord_circuit_unstable")

    safety = get_execution_safety_summary(skill_dir=SKILL_DIR, days=max(1, args.days))
    events = safety.get("events", {}) or {}
    baseline_events: dict[str, Any] = {}
    if args.baseline_events_json:
        try:
            parsed = json.loads(args.baseline_events_json)
            if isinstance(parsed, dict):
                baseline_events = parsed
        except Exception:
            baseline_events = {}

    delta_events: dict[str, int] = {}
    for key, value in events.items():
        curr = int(value or 0)
        base = int(baseline_events.get(key, 0) or 0)
        delta_events[key] = max(0, curr - base)

    stop_failures = int(delta_events.get("stop_protection_failed", 0) or 0)
    guardrail_blocks = int(delta_events.get("guardrail_blocked_order", 0) or 0)
    details["execution_events"] = events
    details["execution_events_delta"] = delta_events
    if baseline_events:
        details["execution_events_baseline"] = baseline_events
    if stop_failures > args.max_stop_failures:
        failures.append(f"stop_failures_exceeded:{stop_failures}>{args.max_stop_failures}")
    if guardrail_blocks > args.max_guardrail_blocks:
        failures.append(f"guardrail_blocks_exceeded:{guardrail_blocks}>{args.max_guardrail_blocks}")

    if args.web_base_url:
        try:
            metrics = _fetch_web_metrics(args.web_base_url)
            req = int(metrics.get("requests_total", 0) or 0)
            err = int(metrics.get("errors_total", 0) or 0)
            rate = ((err / req) * 100.0) if req > 0 else 0.0
            details["web_metrics"] = {
                "requests_total": req,
                "errors_total": err,
                "error_rate_pct": round(rate, 3),
            }
            if rate > args.max_web_error_rate_pct:
                failures.append(f"web_error_rate_exceeded:{rate:.2f}>{args.max_web_error_rate_pct}")
        except Exception as e:
            failures.append(f"web_metrics_fetch_failed:{e}")

    if failures:
        print("FAIL: observability gates failed")
        print(json.dumps({"failures": failures, "details": details}, indent=2))
        return 1

    print("PASS: observability gates satisfied")
    print(json.dumps({"details": details}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

