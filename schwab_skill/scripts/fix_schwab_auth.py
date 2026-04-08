#!/usr/bin/env python3
"""
Repair Schwab auth end-to-end:
1) remove cached token files
2) run browser OAuth for both sessions
3) run healthcheck endpoint validation

Run from schwab_skill:
  python scripts/fix_schwab_auth.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


def _run(cmd: list[str]) -> int:
    return subprocess.call(cmd, cwd=SKILL_DIR)


def main() -> int:
    print("Removing old token files (if present)...")
    for token_name in ("tokens_market.enc", "tokens_account.enc"):
        token_path = SKILL_DIR / token_name
        if token_path.exists():
            token_path.unlink()
            print(f"  removed {token_name}")
        else:
            print(f"  {token_name} not present")

    print("\nStarting browser OAuth for market + account sessions...")
    rc = _run([sys.executable, "run_dual_auth_browser.py"])
    if rc != 0:
        print("\nOAuth step failed. Re-run and complete both browser approvals.")
        return rc

    print("\nValidating with healthcheck...")
    rc = _run([sys.executable, "healthcheck.py"])
    if rc == 0:
        print("\nSchwab auth repair complete.")
    else:
        print("\nAuth still failing. Check TROUBLESHOOTING.md section for 401/authorization.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
