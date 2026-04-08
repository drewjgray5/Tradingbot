#!/usr/bin/env python3
"""
Run self-study analysis: learn from trade outcomes and update .self_study.json.
Typically runs automatically at 4:00 PM ET. Use this script to run manually.
"""
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from self_study import run_self_study


def main():
    result = run_self_study(skill_dir=SKILL_DIR)
    print(f"Round trips: {result['round_trips_count']}")
    wr = result.get('win_rate')
    print(f"Win rate: {f'{wr}%' if wr is not None else 'N/A'}")
    ar = result.get('avg_return_pct')
    print(f"Avg return: {f'{ar}%' if ar is not None else 'N/A'}")
    print(f"Suggested min conviction: {result.get('suggested_min_conviction', 'N/A')}")
    if result.get("by_conviction"):
        print("By conviction band:", result["by_conviction"])
    if result.get("by_sector"):
        print("By sector:", result["by_sector"])

if __name__ == "__main__":
    main()
