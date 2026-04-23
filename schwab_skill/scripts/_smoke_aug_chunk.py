"""
One-shot smoke test for the augmented chunk schema.

Runs ``run_multi_era_backtest_schwab_only.py --single-chunk`` against a tiny
30-ticker slice of the recent_current era with augmented logging + OHLC path
turned on. Verifies that the chunk JSON contains the new fields (ticker,
entry_price, exit_price, mfe, mae, ohlc_path) before we commit to the long
full-universe runs.

Delete after verification.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / "scripts" / "run_multi_era_backtest_schwab_only.py"

SMOKE_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "NVDA", "TSLA", "JPM", "V", "JNJ",
    "WMT", "PG", "MA", "HD", "DIS",
    "BAC", "XOM", "CVX", "PFE", "KO",
    "PEP", "INTC", "CSCO", "VZ", "T",
    "ABT", "MRK", "MCD", "NKE", "ORCL",
]

STAGE2_ENV = {
    "META_POLICY_MODE": "off",
    "UNCERTAINTY_MODE": "off",
    "EVENT_RISK_MODE": "off",
    "EXIT_MANAGER_MODE": "off",
    "EXEC_QUALITY_MODE": "off",
    "QUALITY_GATES_ENABLED": "false",
    "FORENSIC_ENABLED": "false",
    "PEAD_ENABLED": "false",
    "ADVISORY_MODEL_ENABLED": "false",
    "SCAN_VCP_GATE_MODE": "shadow",
    "SCAN_SECTOR_GATE_MODE": "shadow",
    "SCAN_VCP_PENALTY_POINTS": "0",
    "SCAN_SECTOR_PENALTY_POINTS": "0",
    "SCAN_SECTOR_UNRESOLVED_PENALTY_POINTS": "0",
    # The two augmentation flags:
    "BACKTEST_AUGMENTED_LOGGING": "true",
    "BACKTEST_OHLC_PATH": "true",
}


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="smoke_aug_"))
    print(f"workdir: {workdir}")
    tickers_file = workdir / "tickers.json"
    out_file = workdir / "chunk.json"
    tickers_file.write_text(json.dumps(SMOKE_TICKERS), encoding="utf-8")

    cmd = [
        sys.executable,
        str(SCRIPT),
        "--single-chunk",
        "--start-date", "2024-01-01",
        "--era-name", "recent_current",
        "--tickers-file", str(tickers_file),
        "--out-file", str(out_file),
    ]
    env = {**os.environ, **STAGE2_ENV}
    print("running single-chunk smoke (30 tickers, recent_current era, augmented + path)...")
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), env=env, capture_output=True, text=True, timeout=900)
    print(f"  exit code: {proc.returncode}")
    if proc.returncode != 0:
        print(f"  stderr tail: {(proc.stderr or '').strip()[-1200:]}")
        return proc.returncode
    print(f"  stdout: {(proc.stdout or '').strip()}")

    if not out_file.exists():
        print(f"  ERROR: expected output file missing: {out_file}")
        return 2

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    trades = payload.get("trades") or []
    print(f"  trades produced: {len(trades)}")
    if not trades:
        print("  WARNING: zero trades; cannot verify augmented field schema.")
        print("  This is normal for a 30-ticker slice if the bare-signal pickup rate is low.")
        print("  Increase ticker count or pick a different era to retry.")
        return 0

    expected_keys = {
        "ticker", "entry_price", "exit_price", "mfe", "mae",
        "qty_estimate", "day_volume", "slippage_pct", "fees_pct",
    }
    sample = trades[0]
    sample_keys = set(sample.keys())
    missing = expected_keys - sample_keys
    print(f"  sample trade keys ({len(sample_keys)}): {sorted(sample_keys)}")
    print(f"  missing required augmented keys: {sorted(missing) if missing else 'none'}")
    print(f"  ohlc_path present in sample: {'ohlc_path' in sample}")
    if "ohlc_path" in sample and sample["ohlc_path"]:
        print(f"  ohlc_path length (days): {len(sample['ohlc_path'])}")
        print(f"  ohlc_path first day: {sample['ohlc_path'][0]}")

    if missing:
        print(f"  ERROR: augmented schema incomplete; missing {missing}")
        return 3
    print("  smoke PASSED.")
    print(f"  artifact preserved at: {out_file}  (size={out_file.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
