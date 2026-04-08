#!/usr/bin/env python3
"""
Score hypothesis ledger outcomes at configured trading-day horizons (default T+1, T+5, T+20).

Uses daily OHLCV from market_data.get_daily_history. Safe to run on a schedule after close.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


def _load_ledger(skill_dir: Path) -> dict[str, Any]:
    path = skill_dir / ".hypothesis_ledger.json"
    if not path.exists():
        return {"schema": 1, "records": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_ledger(skill_dir: Path, data: dict[str, Any]) -> None:
    path = skill_dir / ".hypothesis_ledger.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _entry_price_and_start_idx(df: pd.DataFrame, created: pd.Timestamp, explicit: float | None) -> tuple[float, int] | None:
    if df is None or df.empty or "close" not in df.columns:
        return None
    idx = df.index
    try:
        pos = idx.searchsorted(created.normalize().tz_localize(None), side="left")
    except Exception:
        pos = 0
    pos = min(max(pos, 0), len(df) - 1)
    start_i = pos
    entry = explicit
    if entry is None or entry <= 0:
        entry = float(df["close"].iloc[start_i])
    if entry <= 0:
        return None
    return entry, start_i


def _score_horizon(
    df: pd.DataFrame,
    start_i: int,
    horizon: int,
    entry: float,
    direction: str,
) -> dict[str, Any] | None:
    """Return metrics using the next `horizon` daily bars after start_i (exclusive of signal bar)."""
    end_i = start_i + horizon
    if end_i >= len(df):
        return None
    window = df.iloc[start_i + 1 : end_i + 1]
    if window.empty:
        return None
    last = float(window["close"].iloc[-1])
    lows = window["low"].astype(float)
    highs = window["high"].astype(float)
    if direction == "short":
        ret_pct = 100.0 * (entry - last) / entry
        mae_pct = 100.0 * (float(highs.max()) - entry) / entry
        thesis_hit = last < entry
    else:
        ret_pct = 100.0 * (last - entry) / entry
        mae_pct = 100.0 * (float(lows.min()) - entry) / entry
        thesis_hit = last > entry
    return {
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "horizon_trading_days": horizon,
        "return_pct": round(ret_pct, 4),
        "max_adverse_excursion_pct": round(mae_pct, 4),
        "thesis_hit": bool(thesis_hit),
        "exit_close": round(last, 4),
    }


def run_score(skill_dir: Path, dry_run: bool) -> int:
    from config import get_hypothesis_score_horizons
    from market_data import get_daily_history
    from schwab_auth import DualSchwabAuth

    horizons = get_hypothesis_score_horizons(skill_dir)
    data = _load_ledger(skill_dir)
    records = data.get("records")
    if not isinstance(records, list):
        print("No records in ledger.")
        return 0

    auth = DualSchwabAuth(skill_dir=skill_dir)
    changed = False
    for rec in records:
        if not isinstance(rec, dict):
            continue
        rid = rec.get("id")
        tkr = str(rec.get("ticker") or "").upper()
        if not tkr:
            continue
        pred = rec.get("prediction") if isinstance(rec.get("prediction"), dict) else {}
        direction = str(pred.get("direction") or "long").lower()
        entry_hint = pred.get("entry_reference_px")
        try:
            entry_hint_f = float(entry_hint) if entry_hint is not None else None
        except (TypeError, ValueError):
            entry_hint_f = None

        created_raw = rec.get("created_at")
        try:
            created = pd.Timestamp(created_raw)
        except Exception:
            continue

        df = get_daily_history(tkr, days=450, auth=auth, skill_dir=skill_dir)
        ep = _entry_price_and_start_idx(df, created, entry_hint_f)
        if ep is None:
            continue
        entry_px, start_i = ep

        outcomes = rec.get("outcomes") if isinstance(rec.get("outcomes"), dict) else {}
        for h in horizons:
            key = str(int(h))
            if outcomes.get(key):
                continue
            metrics = _score_horizon(df, start_i, int(h), entry_px, direction)
            if metrics is None:
                continue
            outcomes[key] = metrics
            changed = True
            print(f"Scored {rid} {tkr} H={h} return={metrics['return_pct']}% hit={metrics['thesis_hit']}")
        rec["outcomes"] = outcomes

    if changed and not dry_run:
        _save_ledger(skill_dir, data)
        print("Ledger updated.")
    elif dry_run and changed:
        print("Dry run: would update ledger.")
    else:
        print("No new scores.")
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Score hypothesis ledger horizons")
    parser.add_argument("--skill-dir", type=Path, default=SKILL_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return run_score(args.skill_dir, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
