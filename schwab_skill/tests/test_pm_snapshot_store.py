from __future__ import annotations

from scripts.build_pm_snapshot_store import _match_confidence, _ts


def test_match_confidence_prefers_ticker_specific_events() -> None:
    high = _match_confidence(
        ticker="AAPL",
        question="Will AAPL beat earnings this quarter?",
        description="AAPL earnings and revenue expectations",
    )
    low = _match_confidence(
        ticker="AAPL",
        question="Will BTC break 100k this year?",
        description="crypto macro market question",
    )
    assert float(high) > float(low)
    assert 0.0 <= float(high) <= 1.0


def test_timestamp_normalization_to_utc_z() -> None:
    out = _ts("2026-01-01T10:00:00+00:00")
    assert out is not None
    assert out.endswith("Z")

