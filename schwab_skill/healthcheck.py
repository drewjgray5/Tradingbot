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
) -> tuple[bool, int | None, str]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    except Exception as e:
        return False, None, f"{name}: request failed ({e})"

    if resp.ok:
        return True, resp.status_code, f"{name}: OK"

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
    return False, resp.status_code, f"{name}: FAILED ({resp.status_code}{': ' + detail if detail else ''})"


def _probe_with_refresh(
    name: str,
    session,
    url: str,
    params: dict | None = None,
) -> tuple[bool, str]:
    token = session.get_access_token() or ""
    if not token:
        return False, f"{name}: FAILED (missing access token)"

    ok, status_code, msg = _probe_endpoint(name=name, token=token, url=url, params=params)
    if ok:
        return True, msg

    if status_code == 401 and session.force_refresh():
        token = session.get_access_token() or ""
        if token:
            ok_after, _status_after, msg_after = _probe_endpoint(
                name=name,
                token=token,
                url=url,
                params=params,
            )
            if ok_after:
                return True, f"{name}: OK (recovered after token refresh)"
            return False, f"{msg_after} [after forced refresh]"

    return False, msg


def main() -> None:
    skill_dir = SKILL_DIR

    market = skill_dir / "tokens_market.enc"
    account = skill_dir / "tokens_account.enc"
    if not market.exists() or not account.exists():
        print("NOT READY: OAuth token files are missing (market and/or account).")
        print("  Fix: run `python run_dual_auth_browser.py` from the schwab_skill directory.")
        print("  Fix: add https://127.0.0.1:8182 as a redirect URL on BOTH Schwab Developer Portal apps.")
        sys.exit(1)

    from logger_setup import get_logger, setup_logging
    from schwab_auth import DualSchwabAuth

    setup_logging()
    log = get_logger(__name__)
    auth = DualSchwabAuth(skill_dir=skill_dir)

    failures: list[str] = []
    if not auth.market_session.client_id or not auth.market_session.client_secret:
        failures.append("Missing SCHWAB_MARKET_APP_KEY and/or SCHWAB_MARKET_APP_SECRET in .env")
    if not auth.account_session.client_id or not auth.account_session.client_secret:
        failures.append("Missing SCHWAB_ACCOUNT_APP_KEY and/or SCHWAB_ACCOUNT_APP_SECRET in .env")

    market_token = auth.market_session.get_access_token() or ""
    account_token = auth.account_session.get_access_token() or ""

    if market_token:
        ok, msg = _probe_with_refresh(
            "Market endpoint",
            auth.market_session,
            f"{SCHWAB_BASE}/marketdata/v1/quotes",
            params={"symbols": "SPY"},
        )
        print(msg)
        if not ok:
            failures.append(msg)
    elif auth.market_session.client_id and auth.market_session.client_secret:
        failures.append("Market token load failed (missing/undecryptable token file)")

    if account_token:
        ok, msg = _probe_with_refresh(
            "Account endpoint",
            auth.account_session,
            f"{SCHWAB_BASE}/trader/v1/accounts/accountNumbers",
        )
        print(msg)
        if not ok:
            failures.append(msg)
    elif auth.account_session.client_id and auth.account_session.client_secret:
        failures.append("Account token load failed (missing/undecryptable token file)")

    if failures:
        print("\nNOT READY: One or more Schwab API checks failed (see lines above).")
        print("Recommended repair:")
        print("  1) Delete tokens_market.enc and/or tokens_account.enc if tokens are corrupt or expired.")
        print("  2) Run: python run_dual_auth_browser.py (complete both market and account flows).")
        print("  3) Verify .env has SCHWAB_MARKET_APP_* and SCHWAB_ACCOUNT_APP_* values for two distinct apps.")
        print("  4) Verify callbacks: SCHWAB_CALLBACK_URL (account) and SCHWAB_MARKET_CALLBACK_URL (market).")
        print("  5) In Schwab Developer Portal, confirm both apps are Ready and account app is linked to brokerage.")
        print("  6) If only quotes fail: check market-app product access plus network/VPN/firewall to api.schwabapi.com.")
        log.warning("Healthcheck failed: %s", "; ".join(failures))
        sys.exit(1)

    from main import daily_heartbeat

    daily_heartbeat(skill_dir=skill_dir)
    print("Health check done. Schwab auth appears healthy. See trading_bot.log and Discord.")


if __name__ == "__main__":
    main()
