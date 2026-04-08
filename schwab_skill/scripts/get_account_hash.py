#!/usr/bin/env python3
"""Print account hash for SCHWAB_ACCOUNT_HASH. Run from schwab_skill dir."""
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from execution import _get_account_hash_for_orders
from schwab_auth import DualSchwabAuth

auth = DualSchwabAuth(skill_dir=SKILL_DIR)
token = auth.get_account_token()
h = _get_account_hash_for_orders(token, SKILL_DIR)
if h:
    print(f"SCHWAB_ACCOUNT_HASH={h}")
    print("\nAdd the above line to your .env file if you get 'Invalid account number' on trades.")
else:
    print("Failed to fetch account hash. Check OAuth and Trader API access.")
    sys.exit(1)
