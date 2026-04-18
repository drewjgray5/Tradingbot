from __future__ import annotations

from prediction_market_experiment import _paired_trade_analysis, load_frozen_universe


def test_load_frozen_universe_validates_as_of(tmp_path) -> None:
    path = tmp_path / "universe.json"
    path.write_text('{"as_of":"2025-01-01","tickers":["aapl","msft","AAPL"]}')
    tickers = load_frozen_universe(path, start_date="2025-01-15")
    assert tickers == ["AAPL", "MSFT"]


def test_load_frozen_universe_rejects_future_as_of(tmp_path) -> None:
    path = tmp_path / "universe.json"
    path.write_text('{"as_of":"2025-02-01","tickers":["AAPL"]}')
    try:
        _ = load_frozen_universe(path, start_date="2025-01-15")
    except ValueError as exc:
        assert "on/before backtest start_date" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_paired_trade_analysis_counts_and_delta() -> None:
    control = [
        {"ticker": "AAPL", "entry_date": "2026-01-01T00:00:00", "net_return": 0.01},
        {"ticker": "MSFT", "entry_date": "2026-01-02T00:00:00", "net_return": -0.02},
    ]
    treatment = [
        {"ticker": "AAPL", "entry_date": "2026-01-01T00:00:00", "net_return": 0.03},
        {"ticker": "NVDA", "entry_date": "2026-01-03T00:00:00", "net_return": 0.02},
    ]
    paired = _paired_trade_analysis(control_trades=control, treatment_trades=treatment)
    assert int(paired["common_trade_count"]) == 1
    assert int(paired["control_only_count"]) == 1
    assert int(paired["treatment_only_count"]) == 1
    assert float(paired["mean_net_return_delta"]) == 0.02
