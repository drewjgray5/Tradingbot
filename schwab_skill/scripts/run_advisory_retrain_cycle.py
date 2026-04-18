#!/usr/bin/env python3
"""
Single-entry orchestration for advisory retrain/evaluate/promote cycle.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from promotion_guard import ensure_signed_approval


def _run(cmd: list[str]) -> int:
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR))
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run advisory challenger cycle and optional promotion")
    parser.add_argument("--profile", choices=["standard", "promotion"], default="promotion")
    parser.add_argument("--max-tickers", type=int, default=250)
    parser.add_argument("--allow-model-upgrades", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--promotion", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Apply promotion decision if challenger qualifies")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument(
        "--challenger-model-path",
        default=str(SKILL_DIR / "artifacts" / "advisory_model_candidate.json"),
    )
    args = parser.parse_args()
    if not ensure_signed_approval(
        "advisory_retrain_cycle", apply_requested=args.apply
    ):
        return 2

    train_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "train_and_evaluate_challenger.py"),
        "--profile",
        args.profile,
        "--max-tickers",
        str(args.max_tickers),
        "--challenger-model-out",
        str(args.challenger_model_path),
    ]
    if args.allow_model_upgrades:
        train_cmd.append("--allow-model-upgrades")
    if args.strict:
        train_cmd.append("--strict")
    if args.promotion:
        train_cmd.append("--promotion")

    rc = _run(train_cmd)
    if rc != 0:
        return rc

    promote_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "decide_and_promote_advisory_model.py"),
        "--challenger-model-path",
        str(args.challenger_model_path),
    ]
    if args.strict:
        promote_cmd.append("--strict")
    if args.promotion:
        promote_cmd.append("--promotion")
    if args.apply:
        promote_cmd.append("--apply")
    if args.notify:
        promote_cmd.append("--notify")
    return _run(promote_cmd)


if __name__ == "__main__":
    raise SystemExit(main())
