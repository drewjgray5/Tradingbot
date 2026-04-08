#!/usr/bin/env python3
"""
Quick health check: token files + live Schwab endpoint authorization checks.
  python healthcheck.py
"""
import sys
from pathlib import Path

import requests

SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))

SCHWAB_BASE = "https://api.schwabapi.com"


def _probe_endpoint(
    name: str,
    token: str,
    url: str,
    params: dict | None = None,
) -> tuple[bool, str]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    except Exception as e:
        return False, f"{name}: request failed ({e})"

    if resp.ok:
        return True, f"{name}: OK"

    detail = ""
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            errs = payload.get("errors")
            if isinstance(errs, list) and errs:
                first = errs[0] if isinstance(errs[0], dict) else {}
                detail = str(first.get("detail") or first.get("title") or "").strip()
        if not detail:
            detail = str(payload)[:180]
    except Exception:
        detail = (resp.text or "").strip()[:180]
    return False, f"{name}: FAILED ({resp.status_code}{': ' + detail if detail else ''})"


def main() -> None:
    skill_dir = SKILL_DIR

    market = skill_dir / "tokens_market.enc"
    account = skill_dir / "tokens_account.enc"
    if not market.exists() or not account.exists():
        print("NOT READY: OAuth tokens missing.")
        print("  Run: python run_dual_auth_browser.py")
        print("  Add https://127.0.0.1:8182 to BOTH apps in Schwab Developer Portal.")
        sys.exit(1)

    from logger_setup import get_logger, setup_logging
    from schwab_auth import DualSchwabAuth

    setup_logging()
    log = get_logger(__name__)
    auth = DualSchwabAuth(skill_dir=skill_dir)

    failures: list[str] = []
    market_token = ""
    account_token = ""
    try:
        market_token = auth.get_market_token()
    except Exception as e:
        failures.append(f"Market token load failed ({e})")
    try:
        account_token = auth.get_account_token()
    except Exception as e:
        failures.append(f"Account token load failed ({e})")

    if market_token:
        ok, msg = _probe_endpoint(
            "Market endpoint",
            market_token,
            f"{SCHWAB_BASE}/marketdata/v1/quotes",
            params={"symbols": "SPY"},
        )
        print(msg)
        if not ok:
            failures.append(msg)
    if account_token:
        ok, msg = _probe_endpoint(
            "Account endpoint",
            account_token,
            f"{SCHWAB_BASE}/trader/v1/accounts/accountNumbers",
        )
        print(msg)
        if not ok:
            failures.append(msg)

    if failures:
        print("\nNOT READY: Schwab authorization check failed.")
        print("Recommended repair:")
        print("  1) Delete tokens_market.enc and tokens_account.enc")
        print("  2) Run: python run_dual_auth_browser.py")
        print("  3) Verify both apps are Ready for use in Schwab portal")
        print("  4) Verify account app has Accounts/Trading + linked brokerage account")
        log.warning("Healthcheck failed: %s", "; ".join(failures))
        sys.exit(1)

    from main import daily_heartbeat

    daily_heartbeat(skill_dir=skill_dir)
    print("Health check done. Schwab auth appears healthy. See trading_bot.log and Discord.")


if __name__ == "__main__":
    main()
