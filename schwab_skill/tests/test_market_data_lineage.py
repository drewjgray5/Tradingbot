from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

import market_data


def _fake_candles() -> list[dict[str, float | int]]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    day_ms = 24 * 60 * 60 * 1000
    return [
        {
            "datetime": now_ms - day_ms,
            "open": 100.0,
            "high": 101.0,
            "low": 99.5,
            "close": 100.5,
            "volume": 1000000,
        },
        {
            "datetime": now_ms,
            "open": 101.0,
            "high": 102.0,
            "low": 100.2,
            "close": 101.7,
            "volume": 1200000,
        },
    ]


def test_get_daily_history_with_meta_primary_provider(monkeypatch) -> None:
    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[dict[str, float | int]]]:
            return {"candles": _fake_candles()}

    monkeypatch.setattr(market_data, "_request_with_backoff", lambda *args, **kwargs: _Resp())

    df, meta = market_data.get_daily_history_with_meta("AAPL", days=30, auth=object())
    assert not df.empty
    assert list(df.columns) == market_data.OHLCV_COLUMNS
    assert meta["provider"] == "schwab"
    assert meta["used_fallback"] is False
    assert int(meta["rows"]) == len(df)


def test_get_daily_history_with_meta_fallback_provider(monkeypatch) -> None:
    fallback_df = pd.DataFrame(
        [
            {"open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5, "volume": 100.0},
            {"open": 1.1, "high": 2.1, "low": 1.0, "close": 1.6, "volume": 120.0},
        ]
    )
    fallback_df.index = pd.date_range("2026-01-01", periods=2, freq="D")
    fallback_df.index.name = "date"

    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(market_data, "_request_with_backoff", _raise)
    monkeypatch.setattr(market_data, "_get_daily_history_yfinance", lambda *args, **kwargs: fallback_df.copy())

    df, meta = market_data.get_daily_history_with_meta("MSFT", days=30, auth=object())
    assert not df.empty
    assert meta["provider"] == "yfinance"
    assert meta["used_fallback"] is True
    reason = meta["fallback_reason"]
    assert isinstance(reason, str) and reason.startswith("RuntimeError"), reason
