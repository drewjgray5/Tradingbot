from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from scripts.validate_data_integrity import run_validation


class _Provider:
    def lookup_event(self, *, ticker: str, as_of: datetime):
        return type(
            "Snap",
            (),
            {
                "updated_ts": as_of.replace(tzinfo=timezone.utc),
            },
        )()


def test_data_integrity_gate_passes_with_sufficient_coverage(tmp_path, monkeypatch) -> None:
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"as_of": "2025-01-01", "tickers": ["AAPL", "MSFT"]}), encoding="utf-8")
    pm_file = tmp_path / "pm.json"
    pm_file.write_text("[]", encoding="utf-8")

    dates = pd.date_range("2025-01-01", periods=320, freq="D")
    base_df = pd.DataFrame(
        {
            "open": [1.0] * len(dates),
            "high": [1.1] * len(dates),
            "low": [0.9] * len(dates),
            "close": [1.0] * len(dates),
            "volume": [1000.0] * len(dates),
        },
        index=dates,
    )
    base_df.index.name = "date"

    monkeypatch.setattr(
        "scripts.validate_data_integrity.get_daily_history_with_meta",
        lambda *args, **kwargs: (base_df.copy(), {"provider": "schwab", "used_fallback": False}),
    )
    monkeypatch.setattr("scripts.validate_data_integrity.load_historical_provider", lambda *_args, **_kwargs: _Provider())
    monkeypatch.setattr("scripts.validate_data_integrity.DualSchwabAuth", lambda *args, **kwargs: object())
    monkeypatch.setattr("scripts.validate_data_integrity.get_data_integrity_min_history_bars", lambda *_: 250)
    monkeypatch.setattr("scripts.validate_data_integrity.get_data_integrity_min_history_coverage_pct", lambda *_: 95.0)
    monkeypatch.setattr("scripts.validate_data_integrity.get_data_integrity_min_pm_coverage_pct", lambda *_: 10.0)
    monkeypatch.setattr("scripts.validate_data_integrity.get_data_integrity_fail_on_silent_fallback", lambda *_: True)
    monkeypatch.setattr("scripts.validate_data_integrity.get_data_integrity_max_fallback_unknown_count", lambda *_: 0)

    out = run_validation(
        start_date="2025-01-15",
        end_date="2025-12-15",
        universe_file=universe,
        pm_historical_file=pm_file,
        skill_dir=tmp_path,
    )
    assert out["passed"] is True
    assert float(out["history_coverage"]["coverage_pct"]) >= 95.0

