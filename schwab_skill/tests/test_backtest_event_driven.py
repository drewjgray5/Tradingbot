from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent

if str(SKILL_DIR) not in sys.path:

    sys.path.insert(0, str(SKILL_DIR))



import backtest  # noqa: E402
from backtest_guardrails import AdaptiveGuardrailPolicy, GuardrailBucket  # noqa: E402
from backtest_intelligence import BacktestIntelligenceConfig  # noqa: E402


def _price_df(prices: list[float], volume: float = 1_000_000.0) -> pd.DataFrame:

    idx = pd.date_range("2020-01-01", periods=len(prices), freq="B")

    return pd.DataFrame(

        {

            "open": prices,

            "high": prices,

            "low": prices,

            "close": prices,

            "volume": [volume] * len(prices),

            "avg_vol_50": [volume] * len(prices),

            "sma_50": prices,

        },

        index=idx,

    )





def _install_common_monkeypatches(monkeypatch) -> None:

    monkeypatch.setattr(backtest, "get_breakout_confirm_enabled", lambda _sd: False)

    monkeypatch.setattr(backtest, "get_quality_gates_mode", lambda _sd: "off")

    monkeypatch.setattr(backtest, "get_adaptive_stop_enabled", lambda _sd: False)

    monkeypatch.setattr(backtest, "get_adaptive_stop_base_pct", lambda _sd: 0.05)

    monkeypatch.setattr(backtest, "get_forensic_enabled", lambda _sd: False)

    monkeypatch.setattr(backtest, "get_forensic_filter_mode", lambda _sd: "off")

    monkeypatch.setattr(backtest, "get_forensic_cache_hours", lambda _sd: 24.0)

    monkeypatch.setattr(backtest, "get_forensic_sloan_max", lambda _sd: 1.0)

    monkeypatch.setattr(backtest, "get_forensic_beneish_max", lambda _sd: 1.0)

    monkeypatch.setattr(backtest, "get_forensic_altman_min", lambda _sd: -10.0)

    monkeypatch.setattr(backtest, "get_pead_enabled", lambda _sd: False)

    monkeypatch.setattr(backtest, "get_pead_lookback_days", lambda _sd: 7)

    monkeypatch.setattr(backtest, "get_backtest_portfolio_starting_equity", lambda _sd: 100_000.0)

    monkeypatch.setattr(backtest, "get_backtest_portfolio_max_positions", lambda _sd: 10)

    monkeypatch.setattr(backtest, "is_stage_2", lambda _window, _sd: True)

    monkeypatch.setattr(backtest, "check_vcp_volume", lambda _window, _sd: True)

    monkeypatch.setattr(backtest, "_evaluate_quality_gates", lambda _signal, _sd: [])

    monkeypatch.setattr(backtest, "_quality_mode_should_filter", lambda _reasons, _sd: False)

    monkeypatch.setattr(backtest, "_sector_filter_pass", lambda _ticker, _i, _context: (True, "sector_winning"))

    monkeypatch.setattr(backtest, "_resolve_stop_pct_for_entry", lambda _df, _i, skill_dir=None: 0.05)

    monkeypatch.setattr(

        backtest,

        "compute_signal_components",

        lambda _window, mirofish_conviction=None, mirofish_result=None: {

            "score": 70.0,

            "avg_vcp_volume_ratio": 0.55,

        },

    )

    monkeypatch.setattr(

        backtest,

        "apply_exec_quality_overlay",

        lambda slippage_bps_per_side, day_volume, qty, skill_dir, mode: (

            float(slippage_bps_per_side),

            {"regime": "normal", "effective_slippage_bps": float(slippage_bps_per_side)},

        ),

    )





def test_cross_sectional_ranks_candidates_same_day(monkeypatch) -> None:

    _install_common_monkeypatches(monkeypatch)

    monkeypatch.setattr(backtest, "get_backtest_portfolio_max_positions", lambda _sd: 1)



    df = _price_df([100.0] * 230)

    ctx = backtest.BacktestContext(

        watchlist=["AAA", "BBB"],

        price_data={"AAA": df.copy(), "BBB": df.copy()},

        sector_etf_by_ticker={},

        sector_perf={},

        excluded_tickers=[],

        data_integrity={},

    )

    monkeypatch.setattr(backtest, "_prepare_context", lambda *args, **kwargs: ctx)

    monkeypatch.setattr(

        backtest,

        "_run_mirofish_for_entry",

        lambda ticker, _window, skill_dir=None: {"conviction_score": 80.0 if ticker == "AAA" else 20.0},

    )



    out = backtest._run_backtest_core(

        tickers=["AAA", "BBB"],

        start_date="2020-01-01",

        end_date="2020-12-31",

        include_all_trades=True,

        intelligence_overlay=BacktestIntelligenceConfig.all_off(),

    )

    trades = out.get("trades", [])

    assert len(trades) >= 1

    assert trades[0]["ticker"] == "AAA"





def test_liquidity_cap_and_telemetry_snapshot(monkeypatch) -> None:

    _install_common_monkeypatches(monkeypatch)



    df = _price_df([100.0] * 230, volume=100.0)

    ctx = backtest.BacktestContext(

        watchlist=["AAA"],

        price_data={"AAA": df},

        sector_etf_by_ticker={},

        sector_perf={},

        excluded_tickers=[],

        data_integrity={},

    )

    monkeypatch.setattr(backtest, "_prepare_context", lambda *args, **kwargs: ctx)

    monkeypatch.setattr(

        backtest,

        "_run_mirofish_for_entry",

        lambda ticker, _window, skill_dir=None: {"conviction_score": 55.0},

    )



    out = backtest._run_backtest_core(

        tickers=["AAA"],

        start_date="2020-01-01",

        end_date="2020-12-31",

        max_adv_participation=0.02,

        include_all_trades=True,

        intelligence_overlay=BacktestIntelligenceConfig.all_off(),

    )

    trades = out.get("trades", [])

    assert len(trades) >= 1

    first = trades[0]

    assert int(first["qty_estimate"]) <= 2

    telemetry = first.get("telemetry")

    assert isinstance(telemetry, dict)

    assert set(telemetry.keys()) == {

        "mirofish_conviction",

        "advisory_prob",

        "agent_uncertainty",

        "vcp_volume_ratio",

        "sector_rs_rank",

    }





def test_dynamic_exit_reenters_without_hold_jump(monkeypatch) -> None:

    _install_common_monkeypatches(monkeypatch)

    monkeypatch.setattr(backtest, "get_backtest_portfolio_max_positions", lambda _sd: 1)



    prices = [100.0] * 200 + [100.0, 90.0, 101.0, 102.0, 103.0, 104.0]

    df = _price_df(prices)

    ctx = backtest.BacktestContext(

        watchlist=["AAA"],

        price_data={"AAA": df},

        sector_etf_by_ticker={},

        sector_perf={},

        excluded_tickers=[],

        data_integrity={},

    )

    monkeypatch.setattr(backtest, "_prepare_context", lambda *args, **kwargs: ctx)

    monkeypatch.setattr(

        backtest,

        "_run_mirofish_for_entry",

        lambda ticker, _window, skill_dir=None: {"conviction_score": 60.0},

    )



    out = backtest._run_backtest_core(

        tickers=["AAA"],

        start_date="2020-01-01",

        end_date="2020-12-31",

        include_all_trades=True,

        intelligence_overlay=BacktestIntelligenceConfig.all_off(),

    )

    trades = [t for t in out.get("trades", []) if t.get("ticker") == "AAA"]

    assert len(trades) >= 2

    assert any(str(t.get("exit_reason")) == "trailing_stop" for t in trades)





def test_advisory_prob_ranking_used_when_mirofish_missing(monkeypatch) -> None:

    _install_common_monkeypatches(monkeypatch)

    monkeypatch.setattr(backtest, "get_backtest_portfolio_max_positions", lambda _sd: 1)



    df = _price_df([100.0] * 230)

    ctx = backtest.BacktestContext(

        watchlist=["AAA", "BBB"],

        price_data={"AAA": df.copy(), "BBB": df.copy()},

        sector_etf_by_ticker={},

        sector_perf={},

        excluded_tickers=[],

        data_integrity={},

    )

    monkeypatch.setattr(backtest, "_prepare_context", lambda *args, **kwargs: ctx)

    monkeypatch.setattr(backtest, "_run_mirofish_for_entry", lambda *args, **kwargs: None)



    def _inject_advisory(signal, diagnostics, skill_dir, mode):

        signal = dict(signal)

        signal["advisory"] = {"p_up_10d": 0.95 if signal.get("ticker") == "BBB" else 0.25}

        return signal, True, 1.0



    monkeypatch.setattr(backtest, "apply_meta_policy_overlay", _inject_advisory)



    out = backtest._run_backtest_core(

        tickers=["AAA", "BBB"],

        start_date="2020-01-01",

        end_date="2020-12-31",

        include_all_trades=True,

        intelligence_overlay=BacktestIntelligenceConfig(meta_policy="live"),

    )

    trades = out.get("trades", [])

    assert len(trades) >= 1

    assert trades[0]["ticker"] == "BBB"





def test_position_size_respects_ten_percent_equity_cap(monkeypatch) -> None:

    _install_common_monkeypatches(monkeypatch)

    monkeypatch.setattr(backtest, "get_backtest_portfolio_starting_equity", lambda _sd: 100_000.0)

    monkeypatch.setattr(backtest, "_estimate_order_qty", lambda *args, **kwargs: 100_000)



    df = _price_df([100.0] * 230, volume=10_000_000.0)

    ctx = backtest.BacktestContext(

        watchlist=["AAA"],

        price_data={"AAA": df},

        sector_etf_by_ticker={},

        sector_perf={},

        excluded_tickers=[],

        data_integrity={},

    )

    monkeypatch.setattr(backtest, "_prepare_context", lambda *args, **kwargs: ctx)

    monkeypatch.setattr(

        backtest,

        "_run_mirofish_for_entry",

        lambda ticker, _window, skill_dir=None: {"conviction_score": 50.0},

    )



    out = backtest._run_backtest_core(

        tickers=["AAA"],

        start_date="2020-01-01",

        end_date="2020-12-31",

        include_all_trades=True,

        intelligence_overlay=BacktestIntelligenceConfig.all_off(),

    )

    trades = out.get("trades", [])

    assert len(trades) >= 1

    # 10% of 100,000 at $100/share => max 100 shares.

    assert int(trades[0]["qty_estimate"]) <= 100


def test_adaptive_guardrails_filter_low_score(monkeypatch) -> None:

    _install_common_monkeypatches(monkeypatch)

    df = _price_df([100.0] * 230, volume=1_000_000.0)

    ctx = backtest.BacktestContext(

        watchlist=["AAA"],

        price_data={"AAA": df},

        sector_etf_by_ticker={},

        sector_perf={},

        excluded_tickers=[],

        data_integrity={},

    )

    monkeypatch.setattr(backtest, "_prepare_context", lambda *args, **kwargs: ctx)

    monkeypatch.setattr(

        backtest,

        "_run_mirofish_for_entry",

        lambda ticker, _window, skill_dir=None: {"conviction_score": 50.0},

    )

    monkeypatch.setattr(

        backtest,

        "compute_signal_components",

        lambda _window, mirofish_conviction=None, mirofish_result=None: {

            "score": 45.0,

            "avg_vcp_volume_ratio": 0.70,

        },

    )

    policy = AdaptiveGuardrailPolicy(
        min_signal_score=50.0,
        strong_signal_score=70.0,
        extra_position_slots=0,
        min_size_multiplier=0.5,
        max_size_multiplier=1.5,
        score_buckets=(
            GuardrailBucket(min_value=50.0, max_value=60.0, multiplier=1.0),
            GuardrailBucket(min_value=60.0, max_value=None, multiplier=1.0),
        ),
        vcp_buckets=(
            GuardrailBucket(min_value=float("-inf"), max_value=1.0, multiplier=1.0),
        ),
    )
    monkeypatch.setattr(backtest, "_load_adaptive_guardrail_policy", lambda _sd: policy)

    out = backtest._run_backtest_core(

        tickers=["AAA"],

        start_date="2020-01-01",

        end_date="2020-12-31",

        include_all_trades=True,

        intelligence_overlay=BacktestIntelligenceConfig.all_off(),

    )

    assert out.get("total_trades") == 0
    assert int((out.get("diagnostics") or {}).get("adaptive_guardrail_filtered", 0)) > 0


def test_adaptive_guardrails_allow_extra_slots_for_strong_signals(monkeypatch) -> None:

    _install_common_monkeypatches(monkeypatch)
    monkeypatch.setattr(backtest, "get_backtest_portfolio_max_positions", lambda _sd: 1)

    df = _price_df([100.0] * 230, volume=1_000_000.0)

    ctx = backtest.BacktestContext(

        watchlist=["AAA", "BBB"],

        price_data={"AAA": df.copy(), "BBB": df.copy()},

        sector_etf_by_ticker={},

        sector_perf={},

        excluded_tickers=[],

        data_integrity={},

    )

    monkeypatch.setattr(backtest, "_prepare_context", lambda *args, **kwargs: ctx)

    monkeypatch.setattr(

        backtest,

        "_run_mirofish_for_entry",

        lambda ticker, _window, skill_dir=None: {"conviction_score": 75.0},

    )

    monkeypatch.setattr(

        backtest,

        "compute_signal_components",

        lambda _window, mirofish_conviction=None, mirofish_result=None: {

            "score": 75.0,

            "avg_vcp_volume_ratio": 0.65,

        },

    )

    policy = AdaptiveGuardrailPolicy(
        min_signal_score=50.0,
        strong_signal_score=70.0,
        extra_position_slots=1,
        min_size_multiplier=0.5,
        max_size_multiplier=1.5,
        score_buckets=(
            GuardrailBucket(min_value=50.0, max_value=60.0, multiplier=1.0),
            GuardrailBucket(min_value=60.0, max_value=None, multiplier=1.0),
        ),
        vcp_buckets=(
            GuardrailBucket(min_value=float("-inf"), max_value=1.0, multiplier=1.0),
        ),
    )
    monkeypatch.setattr(backtest, "_load_adaptive_guardrail_policy", lambda _sd: policy)

    out = backtest._run_backtest_core(

        tickers=["AAA", "BBB"],

        start_date="2020-01-01",

        end_date="2020-12-31",

        include_all_trades=True,

        intelligence_overlay=BacktestIntelligenceConfig.all_off(),

    )

    assert int(out.get("total_trades") or 0) >= 2
    assert int((out.get("diagnostics") or {}).get("adaptive_guardrail_extra_slot_entries", 0)) >= 1

