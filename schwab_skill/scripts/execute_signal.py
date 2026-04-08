#!/usr/bin/env python3
"""
Execute a signal from Discord. Usage:
  python scripts/execute_signal.py AAPL 10
  python scripts/execute_signal.py AAPL 10 BUY MARKET
"""
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

def main():
    if len(sys.argv) < 3:
        print("Usage: python execute_signal.py TICKER QTY [SIDE] [ORDER_TYPE]")
        print("  e.g. python execute_signal.py AAPL 10 BUY MARKET")
        sys.exit(1)
    ticker = sys.argv[1].upper()
    qty = int(sys.argv[2])
    side = sys.argv[3].upper() if len(sys.argv) > 3 else "BUY"
    order_type = sys.argv[4].upper() if len(sys.argv) > 4 else "MARKET"
    limit_price = float(sys.argv[5]) if len(sys.argv) > 5 else None

    confirm = input(f"Execute {side} {qty} {ticker} at {order_type}? (y/N): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        sys.exit(0)

    from execution import place_order
    result = place_order(ticker, qty, side, order_type, limit_price, skill_dir=SKILL_DIR)
    if isinstance(result, str):
        print("Error:", result)
        sys.exit(1)
    print("Order placed:", result)

if __name__ == "__main__":
    main()
