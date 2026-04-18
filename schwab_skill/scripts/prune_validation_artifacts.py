#!/usr/bin/env python3
"""Apply retention to ``validation_artifacts/``.

The validation pipeline writes one timestamped JSON per run plus large
``multi_era_chunks/<RUN_ID>/`` directories from the schwab-only multi-era
backtest. Without retention these accumulate gigabytes and pollute git status.

Default policy: keep the most recent ``--keep`` runs of each file pattern, plus
``latest_validation_report.json`` (which is a stable alias). Anything older is
removed. Use ``--dry-run`` first to preview.

Examples:
    python scripts/prune_validation_artifacts.py --keep 5
    python scripts/prune_validation_artifacts.py --dry-run --keep 10 --include-chunks
"""

from __future__ import annotations

import argparse
import re
import shutil
from collections import defaultdict
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
CHUNKS_DIR = ARTIFACT_DIR / "multi_era_chunks"

# Files matching one of these patterns are retained based on their (timestamp,
# basename-prefix) pairing. The prefix is everything before the timestamp.
TIMESTAMPED_PATTERNS = [
    re.compile(r"^(?P<prefix>.+?)_(?P<stamp>\d{8}T\d{6}Z)(?:_[^/]*)?\.json$"),
    re.compile(r"^(?P<prefix>.+?)_(?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z?)\.json$"),
]
ALWAYS_KEEP = {"latest_validation_report.json", "baseline.json"}


def _classify(name: str) -> tuple[str, str] | None:
    for pat in TIMESTAMPED_PATTERNS:
        m = pat.match(name)
        if m:
            return m.group("prefix"), m.group("stamp")
    return None


def _candidates(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.iterdir() if p.is_file() and p.name not in ALWAYS_KEEP]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", type=int, default=5, help="Most recent runs to keep per prefix")
    parser.add_argument("--dry-run", action="store_true", help="Preview deletions without removing files")
    parser.add_argument(
        "--include-chunks",
        action="store_true",
        help="Also prune multi_era_chunks/ subdirs (only the newest --keep runs are retained)",
    )
    args = parser.parse_args()

    if not ARTIFACT_DIR.exists():
        print(f"Nothing to prune; {ARTIFACT_DIR} does not exist.")
        return 0

    keep = max(1, int(args.keep))
    grouped: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    skipped = 0
    for path in _candidates(ARTIFACT_DIR):
        cls = _classify(path.name)
        if not cls:
            skipped += 1
            continue
        prefix, stamp = cls
        grouped[prefix].append((stamp, path))

    deleted_files: list[Path] = []
    for prefix, entries in grouped.items():
        entries.sort(key=lambda kv: kv[0], reverse=True)
        to_delete = [p for _stamp, p in entries[keep:]]
        for path in to_delete:
            deleted_files.append(path)
            if not args.dry_run:
                try:
                    path.unlink()
                except OSError:
                    pass

    deleted_dirs: list[Path] = []
    if args.include_chunks and CHUNKS_DIR.exists():
        run_dirs = sorted([p for p in CHUNKS_DIR.iterdir() if p.is_dir()], reverse=True)
        for old in run_dirs[keep:]:
            deleted_dirs.append(old)
            if not args.dry_run:
                shutil.rmtree(old, ignore_errors=True)

    print(f"Prefixes scanned: {len(grouped)}; unrecognised files skipped: {skipped}")
    print(f"Files {'would delete' if args.dry_run else 'deleted'}: {len(deleted_files)}")
    for p in deleted_files[:50]:
        print(f"  {p.relative_to(ARTIFACT_DIR)}")
    if args.include_chunks:
        print(f"Multi-era chunk dirs {'would delete' if args.dry_run else 'deleted'}: {len(deleted_dirs)}")
        for p in deleted_dirs[:50]:
            print(f"  multi_era_chunks/{p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
