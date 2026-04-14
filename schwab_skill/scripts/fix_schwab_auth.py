#!/usr/bin/env python3
"""
Repair Schwab auth end-to-end:
1) validate required env vars
2) auto-repair callback vars in .env (safe defaults)
3) remove cached token files
4) run browser OAuth for both sessions
5) run healthcheck endpoint validation

Run from schwab_skill:
  python scripts/fix_schwab_auth.py
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = SKILL_DIR / ".env"
DEFAULT_CALLBACK = "https://127.0.0.1:8182"
REQUIRED_KEYS = (
    "SCHWAB_MARKET_APP_KEY",
    "SCHWAB_MARKET_APP_SECRET",
    "SCHWAB_ACCOUNT_APP_KEY",
    "SCHWAB_ACCOUNT_APP_SECRET",
)


def _run(cmd: list[str]) -> int:
    return subprocess.call(cmd, cwd=SKILL_DIR)


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _repair_callback_vars(path: Path, dry_run: bool) -> tuple[str, str]:
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines() if text else []
    out_lines: list[str] = []

    current = _load_env_file(path)
    account_cb = current.get("SCHWAB_CALLBACK_URL", DEFAULT_CALLBACK).strip() or DEFAULT_CALLBACK
    market_cb = current.get("SCHWAB_MARKET_CALLBACK_URL", account_cb).strip() or account_cb

    saw_account = False
    saw_market = False

    for line in lines:
        if line.startswith("SCHWAB_CALLBACK_URL="):
            out_lines.append(f"SCHWAB_CALLBACK_URL={account_cb}")
            saw_account = True
            continue
        if line.startswith("SCHWAB_MARKET_CALLBACK_URL="):
            out_lines.append(f"SCHWAB_MARKET_CALLBACK_URL={market_cb}")
            saw_market = True
            continue
        out_lines.append(line)

    if not saw_account:
        out_lines.append(f"SCHWAB_CALLBACK_URL={account_cb}")
    if not saw_market:
        out_lines.append(f"SCHWAB_MARKET_CALLBACK_URL={market_cb}")

    if not dry_run:
        path.write_text("\n".join(out_lines).rstrip() + "\n")

    return account_cb, market_cb


def _print_preflight(env: dict[str, str]) -> list[str]:
    missing = [key for key in REQUIRED_KEYS if not env.get(key)]
    print("Preflight checks:")
    for key in REQUIRED_KEYS:
        print(f"  - {key}: {'OK' if env.get(key) else 'MISSING'}")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair local Schwab OAuth setup and validate endpoints."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be repaired without writing files or running OAuth.",
    )
    parser.add_argument(
        "--skip-oauth",
        action="store_true",
        help="Skip browser OAuth step and run healthcheck only.",
    )
    args = parser.parse_args()

    env_before = _load_env_file(ENV_PATH)
    missing = _print_preflight(env_before)
    account_cb, market_cb = _repair_callback_vars(ENV_PATH, dry_run=args.dry_run)
    print(f"  - SCHWAB_CALLBACK_URL: {account_cb}")
    print(f"  - SCHWAB_MARKET_CALLBACK_URL: {market_cb}")

    if missing:
        print("\nMissing required Schwab app credentials in .env:")
        for key in missing:
            print(f"  - {key}")
        print("Add these keys first, then rerun this script.")
        return 1

    if args.dry_run:
        print("\nDry run complete. No files were changed.")
        return 0

    print("Removing old token files (if present)...")
    for token_name in ("tokens_market.enc", "tokens_account.enc"):
        token_path = SKILL_DIR / token_name
        if token_path.exists():
            token_path.unlink()
            print(f"  removed {token_name}")
        else:
            print(f"  {token_name} not present")

    if not args.skip_oauth:
        print("\nStarting browser OAuth for market + account sessions...")
        rc = _run([sys.executable, "run_dual_auth_browser.py"])
        if rc != 0:
            print("\nOAuth step failed. Re-run and complete both browser approvals.")
            return rc
    else:
        print("\nSkipping OAuth step as requested (--skip-oauth).")

    print("\nValidating with healthcheck...")
    rc = _run([sys.executable, "healthcheck.py"])
    if rc == 0:
        print("\nSchwab auth repair complete.")
    else:
        print("\nAuth still failing. Check TROUBLESHOOTING.md section for 401/authorization.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
