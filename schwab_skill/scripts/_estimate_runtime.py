"""Estimate full multi-era runtime from current stage2_only_aug progress."""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone


def main() -> int:
    root = pathlib.Path("schwab_skill/validation_artifacts/multi_era_chunks/stage2_only_aug")
    eras_def = [
        ("recent_current", "2024-01-01", None),
        ("bear_rates", "2022-01-01", "2023-12-31"),
        ("crash_recovery", "2020-01-01", "2021-12-31"),
        ("volatility_chop", "2018-01-01", "2019-12-31"),
        ("late_bull", "2015-01-01", "2017-12-31"),
    ]

    tickers_files = list(root.rglob("chunk_*_tickers.json"))
    chunks_per_era = {}
    for era, _, _ in eras_def:
        era_dir = root / era
        if era_dir.exists():
            n_t = len([p for p in era_dir.glob("chunk_*_tickers.json")])
            n_c = len([p for p in era_dir.glob("chunk_*.json") if not p.name.endswith("_tickers.json")])
            chunks_per_era[era] = (n_t, n_c)
        else:
            chunks_per_era[era] = (0, 0)

    print("Per-era chunk progress (tickers_queued, chunks_completed):")
    for era, (nt, nc) in chunks_per_era.items():
        print(f"  {era:18s}  {nt:4d} queued / {nc:4d} done")

    # Need universe size for total chunk estimation
    cached = pathlib.Path("schwab_skill/.watchlist_cache.json")
    universe_n = None
    if cached.exists():
        try:
            payload = json.loads(cached.read_text(encoding="utf-8"))
            universe_n = len(payload.get("tickers") or payload.get("symbols") or [])
        except Exception:
            pass
    if universe_n is None:
        tickers_total = sum(nt for nt, _ in chunks_per_era.values()) or 1
        universe_n = tickers_total * 120
    print(f"Universe size estimate: {universe_n} tickers (chunk_size=120 -> ~{(universe_n + 119)//120} chunks/era)")

    chunks_per_era_estimate = (universe_n + 119) // 120
    total_eras = len(eras_def)
    total_chunks = chunks_per_era_estimate * total_eras

    # Estimate per-chunk seconds from completed chunks in recent_current
    era_dir = root / "recent_current"
    completed_paths = sorted(
        [p for p in era_dir.glob("chunk_*.json") if not p.name.endswith("_tickers.json")],
        key=lambda p: p.stat().st_mtime,
    )
    if completed_paths:
        last_t = datetime.fromtimestamp(completed_paths[-1].stat().st_mtime, tz=timezone.utc)
        progress_path = pathlib.Path(
            "schwab_skill/validation_artifacts/multi_era_backtest_schwab_only_stage2_only_aug_progress.json"
        )
        started_at = datetime.fromtimestamp(
            min(p.stat().st_ctime for p in (root / "recent_current").glob("chunk_*_tickers.json")),
            tz=timezone.utc,
        )
        n_done = len(completed_paths)
        wall_min = max(0.1, (last_t - started_at).total_seconds() / 60.0)
        per_chunk_serial_min = (wall_min * 4) / max(1, n_done)
        wall_per_chunk_min = wall_min / max(1, n_done)
        print(f"Wall-clock since launch: {wall_min:.1f} min")
        print(f"Completed chunks so far: {n_done}")
        print(f"Throughput: {n_done / wall_min:.2f} chunks/min wall-time")
        print(f"Per-chunk wall avg (with 4 workers): {wall_per_chunk_min:.1f} min/chunk")
        print(f"Per-chunk serialized cost: {per_chunk_serial_min:.1f} min/chunk")

        remaining_chunks = max(0, total_chunks - n_done)
        eta_min = remaining_chunks * wall_per_chunk_min
        eta_hours = eta_min / 60.0
        print(f"Estimated total chunks across 5 eras: {total_chunks}")
        print(f"Remaining chunks: {remaining_chunks}")
        print(f"ETA for stage2_only_aug full completion: {eta_hours:.1f} hours ({eta_hours/24:.2f} days)")
        print(f"Note: later eras (volatility_chop, late_bull) span 2-3 years -> may run slower per chunk")
    else:
        print("No completed chunks yet for throughput estimation.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
