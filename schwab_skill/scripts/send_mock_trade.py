#!/usr/bin/env python3
"""
Send a mock trade confirmation to Discord. No real order is placed.
Run this, then check Discord and click Approve or Reject.
"""
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from discord_confirm import _bot_ready, request_mock_confirmation, start_confirm_bot
from logger_setup import setup_logging

setup_logging()


def main():
    print("Starting Discord confirm bot...")
    start_confirm_bot(SKILL_DIR / ".env")

    print("Waiting for bot to connect...")
    if not _bot_ready.wait(timeout=15):
        print("ERROR: Bot failed to connect. Check DISCORD_BOT_TOKEN and DISCORD_CONFIRM_CHANNEL_ID.")
        sys.exit(1)

    print("Sending mock trade (AAPL, ~22 shares @ $225)...")
    if not request_mock_confirmation(
        ticker="AAPL",
        price=225.0,
        skill_dir=SKILL_DIR,
        sma_50=218.50,
        sma_200=205.20,
        signal_score=72.5,
        mirofish_summary="Moderate bullish consensus. Institutional accumulation noted.",
        mirofish_conviction=45,
    ):
        print("ERROR: Could not queue mock trade.")
        sys.exit(1)

    print("\nMock trade sent to Discord! Check your confirmation channel.")
    print("Click Approve to see 'Mock executed' (no real order).")
    print("Staying alive 3 min so you can click... (Ctrl+C to exit)\n")
    try:
        time.sleep(180)
    except KeyboardInterrupt:
        pass
    print("Done.")


if __name__ == "__main__":
    main()
