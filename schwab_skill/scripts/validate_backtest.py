#!/usr/bin/env python3
"""
Backtest-driven validation: run backtest, save results, compare to previous run.

Run before enabling new params or periodically to validate strategy.
Usage: python scripts/validate_backtest.py [--warn-on-regression]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
RESULTS_FILE = SKILL_DIR / ".backtest_results.json"


def load_previous() -> dict | None:
    if not RESULTS_FILE.exists():
        return None
    try:
        return json.loads(RESULTS_FILE.read_text())
    except Exception:
        return None


def save_results(result: dict) -> None:
    out = {
        "run_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_trades": result.get("total_trades", 0),
        "win_rate": result.get("win_rate", 0),
        "win_rate_net": result.get("win_rate_net", 0),
        "avg_return_pct": result.get("avg_return_pct", 0),
        "avg_return_net_pct": result.get("avg_return_net_pct", 0),
        "total_return_pct": result.get("total_return_pct", 0),
        "total_return_net_pct": result.get("total_return_net_pct", 0),
        "max_drawdown_net_pct": result.get("max_drawdown_net_pct", 0),
        "findings": result.get("findings", ""),
    }
    RESULTS_FILE.write_text(json.dumps(out, indent=2))
    print(f"Results saved to {RESULTS_FILE}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run backtest and validate")
    parser.add_argument("--warn-on-regression", action="store_true", help="Exit 1 if metrics regress vs previous")
    parser.add_argument("--tickers", type=int, default=40, help="Number of tickers to test")
    parser.add_argument("--start", default="2015-01-01", help="Backtest start date")
    parser.add_argument("--promotion", action="store_true", help="Enforce promotion-grade net-performance gates.")
    args = parser.parse_args()

    sys.path.insert(0, str(SKILL_DIR))
    from backtest import run_backtest

    tickers = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "V", "UNH",
        "HD", "PG", "MA", "DIS", "BAC", "XOM", "CVX", "KO", "PEP", "WMT",
        "IBM", "ORCL", "CRM", "ADBE", "NFLX", "INTC", "AMD", "QCOM", "TXN", "AVGO",
        "CSCO", "ACN", "NOW", "INTU", "AMAT", "LRCX", "KLAC", "MU", "SBUX", "NKE",
    ][: args.tickers]

    print("Running backtest...")
    result = run_backtest(
        tickers,
        start_date=args.start,
        slippage_bps_per_side=15.0,
        fee_per_share=0.005,
        min_fee_per_order=1.0,
        max_adv_participation=0.02,
    )

    print("\n--- BACKTEST RESULTS ---")
    for k, v in result.items():
        if k not in ("trades_sample", "findings"):
            print(f"  {k}: {v}")
    print("\n--- FINDINGS ---")
    print(result.get("findings", ""))

    prev = load_previous()
    save_results(result)

    if args.warn_on_regression and prev and "run_at" in prev:
        prev_win = prev.get("win_rate", 0)
        prev_ret = prev.get("total_return_net_pct", prev.get("total_return_pct", 0))
        curr_win = result.get("win_rate", 0)
        curr_ret = result.get("total_return_net_pct", result.get("total_return_pct", 0))
        if curr_win < prev_win - 5 or curr_ret < prev_ret - 10:
            print(f"\nWARNING: Metrics regressed. Win: {prev_win}% -> {curr_win}%, Return: {prev_ret}% -> {curr_ret}%")
            return 1

    if args.promotion:
        errors: list[str] = []
        if int(result.get("total_trades", 0) or 0) < 150:
            errors.append(f"total_trades {int(result.get('total_trades', 0) or 0)} < 150")
        if float(result.get("profit_factor_net", 0.0) or 0.0) < 1.05:
            errors.append(f"profit_factor_net {float(result.get('profit_factor_net', 0.0) or 0.0):.3f} < 1.050")
        if float(result.get("max_drawdown_net_pct", 0.0) or 0.0) < -45.0:
            errors.append(f"max_drawdown_net_pct {float(result.get('max_drawdown_net_pct', 0.0) or 0.0):.2f}% < -45.00%")
        if float(result.get("win_rate_net", 0.0) or 0.0) < 50.0:
            errors.append(f"win_rate_net {float(result.get('win_rate_net', 0.0) or 0.0):.2f}% < 50.00%")
        if errors:
            print("\nFAIL: promotion backtest gates not met:")
            for e in errors:
                print(f"  - {e}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
