"""Inspect an actual production chunk from stage2_only_aug to confirm augmented schema."""

from __future__ import annotations

import json
import pathlib
import sys


def main() -> int:
    root = pathlib.Path("schwab_skill/validation_artifacts/multi_era_chunks/stage2_only_aug")
    if not root.exists():
        print(f"NOT FOUND: {root}")
        return 1

    chunks = sorted(root.rglob("chunk_[0-9]*.json"))
    chunks = [c for c in chunks if not c.name.endswith("_tickers.json")]
    print(f"Found {len(chunks)} chunk JSONs (excluding _tickers).")
    if not chunks:
        return 1

    legacy_required = {"return", "net_return", "entry_date", "exit_date", "stop_pct"}
    aug_required = {
        "ticker", "entry_price", "exit_price", "mfe", "mae",
        "exit_reason", "signal_score",
    }

    for chunk_path in chunks[:3]:
        print(f"\n=== {chunk_path.relative_to(root)} ===")
        try:
            payload = json.loads(chunk_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ERROR reading: {e}")
            continue
        trades = payload.get("trades") or []
        print(f"  era={payload.get('era')!r}  chunk_size={payload.get('chunk_size')}  trades={len(trades)}")
        if not trades:
            print("  (no trades in this chunk; skipping schema check)")
            continue
        sample = trades[0]
        keys = sorted(sample.keys())
        print(f"  sample keys ({len(keys)}): {keys}")
        missing_legacy = legacy_required - set(keys)
        missing_aug = aug_required - set(keys)
        print(f"  missing legacy keys: {missing_legacy or 'none'}")
        print(f"  missing aug keys:    {missing_aug or 'none'}")
        if "ohlc_path" in sample:
            path = sample["ohlc_path"]
            print(f"  ohlc_path length: {len(path) if isinstance(path, list) else 'NOT LIST'}")
            if isinstance(path, list) and path:
                print(f"  ohlc_path[0]: {path[0]}")
        else:
            print("  ohlc_path: MISSING (expected with BACKTEST_OHLC_PATH=true)")
        print(
            "  sample numeric values: "
            f"ret={sample.get('return')}  net_ret={sample.get('net_return')}  "
            f"mfe={sample.get('mfe')}  mae={sample.get('mae')}  "
            f"exit_reason={sample.get('exit_reason')!r}  ticker={sample.get('ticker')!r}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
