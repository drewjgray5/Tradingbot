#!/usr/bin/env python3
"""
Run multiple optimization-loop jobs with timeout/retry guardrails.

This avoids losing an overnight batch when one subprocess hits transient
data-fetch failures or hangs.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
OPTIMIZER = SKILL_DIR / "scripts" / "optimize_strategy_loop.py"


def _schedule_for_idx(idx: int) -> tuple[str, int, int, int]:
    if idx % 3 == 0:
        # Longest horizon gets lighter breadth/rounds to avoid timeout pileups.
        return "2015-01-01", 30, 5, 3000
    if idx % 3 == 1:
        return "2018-01-01", 24, 6, 2700
    return "2020-01-01", 20, 8, 2400


def _extract_optimizer_artifact(stdout: str) -> str | None:
    m = re.search(r"Optimization artifact:\s*(.+)", stdout or "")
    if not m:
        return None
    return m.group(1).strip()


def _run_one(
    *,
    seed: int,
    start_date: str,
    tickers: int,
    rounds: int,
    stall_rounds: int,
    min_trades: int,
    max_drawdown_degrade: float,
    min_oos_pf: float,
    min_oos_pf_margin: float,
    timeout_seconds: int,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(OPTIMIZER),
        "--rounds",
        str(rounds),
        "--stall-rounds",
        str(stall_rounds),
        "--tickers",
        str(tickers),
        "--seed",
        str(seed),
        "--min-trades",
        str(min_trades),
        "--max-drawdown-degrade",
        str(max_drawdown_degrade),
        "--min-oos-pf",
        str(min_oos_pf),
        "--min-oos-pf-margin",
        str(min_oos_pf_margin),
    ]
    started = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(SKILL_DIR.parent),
            capture_output=True,
            text=True,
            timeout=max(60, int(timeout_seconds)),
        )
        stdout = (proc.stdout or "").strip()
        return {
            "seed": seed,
            "start_date": start_date,
            "tickers": tickers,
            "returncode": proc.returncode,
            "timed_out": False,
            "started_at": started.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "stdout": stdout,
            "stderr": (proc.stderr or "").strip(),
            "ok": proc.returncode == 0,
            "optimizer_artifact": _extract_optimizer_artifact(stdout),
        }
    except subprocess.TimeoutExpired as e:
        return {
            "seed": seed,
            "start_date": start_date,
            "tickers": tickers,
            "returncode": None,
            "timed_out": True,
            "started_at": started.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "stdout": (e.stdout or "").strip() if isinstance(e.stdout, str) else "",
            "stderr": (e.stderr or "").strip() if isinstance(e.stderr, str) else "",
            "ok": False,
            "error": f"timeout_after_{timeout_seconds}s",
        }
    except Exception as e:  # pragma: no cover - defensive fallback
        return {
            "seed": seed,
            "start_date": start_date,
            "tickers": tickers,
            "returncode": None,
            "timed_out": False,
            "started_at": started.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "stdout": "",
            "stderr": "",
            "ok": False,
            "error": str(e),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resilient multi-run strategy optimization batch")
    parser.add_argument("--runs", type=int, default=24, help="Number of optimizer subprocess runs")
    parser.add_argument("--seed-start", type=int, default=7, help="Starting seed; increments each run")
    parser.add_argument("--rounds", type=int, default=8, help="Per-run max optimization rounds")
    parser.add_argument("--stall-rounds", type=int, default=4, help="Per-run early-stop stall rounds")
    parser.add_argument("--min-trades", type=int, default=35, help="Minimum walk-forward trades gate")
    parser.add_argument("--max-drawdown-degrade", type=float, default=1.5, help="Drawdown degradation cap vs baseline")
    parser.add_argument("--min-oos-pf", type=float, default=1.15, help="Minimum OOS PF gate")
    parser.add_argument("--min-oos-pf-margin", type=float, default=0.01, help="Required OOS PF improvement margin")
    parser.add_argument("--timeout-seconds", type=int, default=3600, help="Per-run hard timeout")
    parser.add_argument("--retry-on-fail", type=int, default=1, help="Extra attempts for failed runs")
    args = parser.parse_args()

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    records: list[dict[str, Any]] = []

    for i in range(max(1, args.runs)):
        seed = args.seed_start + i
        start_date, tickers, rounds, timeout_seconds = _schedule_for_idx(i)
        attempts = 0
        best_record: dict[str, Any] | None = None
        while attempts <= max(0, args.retry_on_fail):
            attempts += 1
            rec = _run_one(
                seed=seed,
                start_date=start_date,
                tickers=tickers,
                rounds=min(max(2, int(args.rounds)), rounds),
                stall_rounds=min(max(2, int(args.stall_rounds)), rounds),
                min_trades=args.min_trades,
                max_drawdown_degrade=args.max_drawdown_degrade,
                min_oos_pf=args.min_oos_pf,
                min_oos_pf_margin=args.min_oos_pf_margin,
                timeout_seconds=min(max(300, int(args.timeout_seconds)), timeout_seconds),
            )
            rec["attempt"] = attempts
            best_record = rec
            if rec.get("ok"):
                break
        assert best_record is not None
        records.append(best_record)
        status = "PASS" if best_record.get("ok") else "FAIL"
        print(
            f"{status} seed={seed} start={start_date} tickers={tickers} "
            f"attempt={best_record.get('attempt')} returncode={best_record.get('returncode')}"
        )

    passed = sum(1 for r in records if r.get("ok"))
    failed = len(records) - passed
    summary = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "runs": args.runs,
            "seed_start": args.seed_start,
            "rounds": args.rounds,
            "stall_rounds": args.stall_rounds,
            "min_trades": args.min_trades,
            "max_drawdown_degrade": args.max_drawdown_degrade,
            "min_oos_pf": args.min_oos_pf,
            "min_oos_pf_margin": args.min_oos_pf_margin,
            "timeout_seconds": args.timeout_seconds,
            "retry_on_fail": args.retry_on_fail,
        },
        "results": {
            "passed": passed,
            "failed": failed,
        },
        "records": records,
    }
    out = ARTIFACT_DIR / f"optimization_batch_{run_id}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Batch summary artifact: {out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
