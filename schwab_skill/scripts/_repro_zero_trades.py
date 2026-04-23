"""Repro for the zero-trade chunk anomaly.

Loads the SAME 120 tickers used by control_legacy_aug late_bull chunk_0001,
applies the SAME env overrides, runs the SAME _run_single_chunk path, and
prints what actually happens (excluded count, trades, data integrity counters,
chunk file size). If this also produces zero trades, the bug is reliably
reproducible and we can dig into why; if it produces real trades, then the
issue was a transient runtime condition (Schwab auth, OneDrive lock, etc.).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "validation_artifacts"
SCRATCH = ARTIFACT_DIR / "_repro_zero_trades"
SCRATCH.mkdir(parents=True, exist_ok=True)

src_tickers = ARTIFACT_DIR / "multi_era_chunks" / "control_legacy_aug" / "late_bull" / "chunk_0001_tickers.json"
src_chunk = ARTIFACT_DIR / "multi_era_chunks" / "control_legacy_aug" / "late_bull" / "chunk_0001.json"
print(f"Source tickers file: {src_tickers}")
print(f"  exists={src_tickers.exists()} size={src_tickers.stat().st_size if src_tickers.exists() else 'n/a'}")
print(f"Original failing chunk: {src_chunk}")
if src_chunk.exists():
    print(f"  size={src_chunk.stat().st_size}")
    print(f"  content={src_chunk.read_text()[:300]}")

tickers = json.loads(src_tickers.read_text(encoding="utf-8"))
print(f"\nReproducing with {len(tickers)} tickers (first 5 = {tickers[:5]})")

repro_tickers = SCRATCH / "tickers.json"
repro_tickers.write_text(json.dumps(tickers), encoding="utf-8")
repro_out = SCRATCH / "out.json"
if repro_out.exists():
    repro_out.unlink()

env = os.environ.copy()
env["SCHWAB_ONLY_DATA"] = "true"
env["BACKTEST_SKIP_MIROFISH"] = "true"
env["SEC_FILING_LLM_SUMMARY_ENABLED"] = "false"
env["BACKTEST_AUGMENTED_LOGGING"] = "true"
env["BACKTEST_OHLC_PATH"] = "true"
env["META_POLICY_MODE"] = "off"
env["UNCERTAINTY_MODE"] = "off"
env["EVENT_RISK_MODE"] = "off"
env["EXIT_MANAGER_MODE"] = "off"
env["EXEC_QUALITY_MODE"] = "off"

cmd = [
    sys.executable,
    str(ROOT / "scripts" / "run_multi_era_backtest_schwab_only.py"),
    "--single-chunk",
    "--start-date", "2015-01-01",
    "--end-date", "2017-12-31",
    "--era-name", "late_bull",
    "--tickers-file", str(repro_tickers),
    "--out-file", str(repro_out),
]
print(f"\nLaunching: {' '.join(cmd)}")
t0 = time.time()
proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=14400, env=env)
elapsed = time.time() - t0
print(f"\nReturned code={proc.returncode} elapsed={elapsed:.1f}s")
print(f"stdout (last 800 chars):\n{(proc.stdout or '')[-800:]}")
print(f"stderr (last 800 chars):\n{(proc.stderr or '')[-800:]}")

if repro_out.exists():
    print(f"\nOutput file: {repro_out} size={repro_out.stat().st_size}")
    payload = json.loads(repro_out.read_text(encoding="utf-8"))
    print(f"  era={payload.get('era')} chunk_size={payload.get('chunk_size')} excluded={payload.get('excluded_count')} trades={len(payload.get('trades', []))}")
else:
    print("\nNo output file produced.")
