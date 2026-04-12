"""Summarize on-disk calibration files from a skill directory (SaaS worker temp dir)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_calibration_snapshot(skill_dir: Path) -> dict[str, Any]:
    """
    Compact snapshot for AppState / API. Safe on missing or huge files.
    """
    out: dict[str, Any] = {"skill_dir_tag": skill_dir.name[:32]}
    ss = _read_json(skill_dir / ".self_study.json")
    if isinstance(ss, dict):
        keys = (
            "suggested_min_conviction",
            "round_trips",
            "hypothesis_calibration",
            "last_run",
            "updated_at",
        )
        out["self_study"] = {k: ss.get(k) for k in keys if k in ss}
    else:
        out["self_study"] = None

    ledger_path = skill_dir / ".hypothesis_ledger.json"
    hl = _read_json(ledger_path)
    if isinstance(hl, list):
        n = len(hl)
        tail = hl[-50:] if n > 50 else hl
        sources: dict[str, int] = {}
        for row in tail:
            if not isinstance(row, dict):
                continue
            src = str(row.get("source") or "unknown")
            sources[src] = sources.get(src, 0) + 1
        out["hypothesis_ledger"] = {
            "row_count": n,
            "recent_source_counts": sources,
            "truncated": n > 50,
        }
    elif isinstance(hl, dict):
        out["hypothesis_ledger"] = {"row_count": 1, "shape": "object"}
    else:
        out["hypothesis_ledger"] = None

    return out
