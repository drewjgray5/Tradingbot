#!/usr/bin/env python3
"""
Weekly scheduled challenger cycle (dry-run by default).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly advisory retrain/evaluate/promote schedule entrypoint")
    parser.add_argument("--apply", action="store_true", help="Apply promotion if challenger qualifies")
    args = parser.parse_args()

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "run_advisory_retrain_cycle.py"),
        "--profile",
        "promotion",
        "--promotion",
        "--strict",
        "--allow-model-upgrades",
        "--max-tickers",
        "250",
        "--notify",
    ]
    if args.apply:
        cmd.append("--apply")
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
