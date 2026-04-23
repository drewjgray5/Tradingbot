"""Scan all _aug chunks and identify the ones poisoned by Schwab auth failure.

A 'real' chunk has at least one trade, several KB of JSON, and excluded_count
roughly proportional to the universe age (older eras drop ~5-15 tickers due
to delistings / IPO date). A 'poisoned' chunk has trades=0, excluded_count=0,
and file size around 120 bytes -- this is the signature of all-fetches-failed
producing a no-trades early-return that omits the real excluded_count.

We list all suspicious chunks with full metadata so we can decide what to
delete before re-launching.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAGS = ["control_legacy_aug", "control_prod_default_aug", "stage2_only_aug"]
ERAS = ["recent_current", "bear_rates", "crash_recovery", "volatility_chop", "late_bull"]

print(f"{'tag':<28} {'era':<18} {'chunks':>7} {'empty':>6} {'tiny':>5} {'real':>5}")
print("-" * 80)
to_delete: list[Path] = []
for tag in TAGS:
    for era in ERAS:
        era_dir = ROOT / "validation_artifacts" / "multi_era_chunks" / tag / era
        if not era_dir.exists():
            continue
        chunks = sorted(p for p in era_dir.glob("chunk_[0-9]*.json") if not p.name.endswith("_tickers.json"))
        empty_zero = 0
        tiny = 0
        real = 0
        for c in chunks:
            try:
                payload = json.loads(c.read_text(encoding="utf-8"))
                ntrades = len(payload.get("trades") or [])
                excluded = int(payload.get("excluded_count", 0) or 0)
                size = c.stat().st_size
                if ntrades == 0 and excluded == 0 and size < 1024:
                    empty_zero += 1
                    to_delete.append(c)
                elif size < 1024:
                    tiny += 1
                else:
                    real += 1
            except Exception as exc:
                print(f"  WARN  {c.name}: {exc}")
        print(f"{tag:<28} {era:<18} {len(chunks):>7} {empty_zero:>6} {tiny:>5} {real:>5}")

print()
print(f"Total chunks flagged for deletion: {len(to_delete)}")
print()
manifest_path = ROOT / "validation_artifacts" / "_poisoned_chunks_to_delete.json"
manifest_path.write_text(
    json.dumps([str(p.relative_to(ROOT)) for p in to_delete], indent=2),
    encoding="utf-8",
)
print(f"Manifest: {manifest_path}")
