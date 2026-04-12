from __future__ import annotations

import json
from pathlib import Path

from webapp.calibration_snapshot import build_calibration_snapshot


def test_build_calibration_snapshot_empty(tmp_path: Path) -> None:
    snap = build_calibration_snapshot(tmp_path)
    assert snap.get("self_study") is None
    assert snap.get("hypothesis_ledger") is None


def test_build_calibration_snapshot_with_files(tmp_path: Path) -> None:
    (tmp_path / ".self_study.json").write_text(
        json.dumps({"suggested_min_conviction": 40, "round_trips": 3}),
        encoding="utf-8",
    )
    (tmp_path / ".hypothesis_ledger.json").write_text(
        json.dumps([{"source": "signal_scanner", "ticker": "AAPL"}]),
        encoding="utf-8",
    )
    snap = build_calibration_snapshot(tmp_path)
    assert snap["self_study"]["suggested_min_conviction"] == 40
    assert snap["hypothesis_ledger"]["row_count"] == 1
