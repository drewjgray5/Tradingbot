#!/usr/bin/env python3
"""
One-time OAuth setup for BOTH Market and Account sessions.
Run from schwab_skill directory.
"""
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))

from schwab_auth import DualSchwabAuth


def main():
    auth = DualSchwabAuth(skill_dir=SKILL_DIR)

    # Market Session
    print("=== MARKET SESSION (OHLCV, quotes) ===")
    print("Open this URL in browser, log in, approve:")
    print(auth.market_session.get_authorization_url())
    redirect = input("Paste full redirect URL: ").strip()
    if redirect:
        auth.market_session.complete_auth(redirect)
        print("Market session saved to tokens_market.enc")
    else:
        print("Skipped market session.")

    # Account Session
    print("\n=== ACCOUNT SESSION (orders, balances) ===")
    print("Open this URL in browser, log in, approve:")
    print(auth.account_session.get_authorization_url())
    redirect = input("Paste full redirect URL: ").strip()
    if redirect:
        auth.account_session.complete_auth(redirect)
        print("Account session saved to tokens_account.enc")
    else:
        print("Skipped account session.")

    print("\nDone. Both sessions will auto-refresh in background.")


if __name__ == "__main__":
    main()
