"""Unit tests for the backtest intelligence overlay.

These tests exercise each overlay function in isolation against synthetic
data so we can guarantee three properties:

1. ``mode == "off"`` is byte-identical to the legacy backtest behaviour.
2. ``mode == "shadow"`` never changes a trade decision but does emit
   diagnostics counters.
3. ``mode == "live"`` produces the expected trade-altering effect.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from backtest_intelligence import (  # noqa: E402
    BacktestIntelligenceConfig,
    apply_event_risk_overlay,
    apply_exec_quality_overlay,
    apply_meta_policy_overlay,
    evaluate_event_risk_for_backtest,
    simulate_exit_with_manager,
)


def _build_df(prices: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2020-01-02", periods=len(prices), freq="B")
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [1_000_000] * len(prices),
        },
        index=idx,
    )


# --------------------------------------------------------------------------- #
# Config resolution
# --------------------------------------------------------------------------- #


def test_config_all_off_default():
    cfg = BacktestIntelligenceConfig.all_off()
    assert cfg.meta_policy == "off"
    assert cfg.event_risk == "off"
    assert cfg.exit_manager == "off"
    assert cfg.exec_quality == "off"
    assert cfg.any_enabled() is False


def test_config_from_mapping_normalises():
    cfg = BacktestIntelligenceConfig.from_mapping(
        {"meta_policy": "LIVE", "event_risk": "Shadow", "exit_manager": "bogus"}
    )
    assert cfg.meta_policy == "live"
    assert cfg.event_risk == "shadow"
    assert cfg.exit_manager == "off"
    assert cfg.exec_quality == "off"
    assert cfg.any_enabled() is True


def test_config_all_live_serialisable():
    cfg = BacktestIntelligenceConfig.all_live()
    assert cfg.as_dict() == {
        "meta_policy": "live",
        "event_risk": "live",
        "exit_manager": "live",
        "exec_quality": "live",
    }


# --------------------------------------------------------------------------- #
# Meta-policy overlay
# --------------------------------------------------------------------------- #


def test_meta_policy_off_is_noop():
    signal = {"ticker": "AAPL", "signal_score": 70.0}
    diagnostics: dict = {}
    out, allow, mult = apply_meta_policy_overlay(
        signal=signal, diagnostics=diagnostics, skill_dir=SKILL_DIR, mode="off"
    )
    assert out is signal  # same object, not even copied
    assert allow is True
    assert mult == 1.0
    assert diagnostics == {}


def test_meta_policy_shadow_never_blocks():
    # A signal designed to trigger high uncertainty (low advisory confidence,
    # PM uncertainty maxed out) should *not* block in shadow mode.
    signal = {
        "ticker": "AAPL",
        "signal_score": 55.0,
        "advisory": {"confidence_bucket": "low", "p_up_10d": 0.42},
        "prediction_market": {"features": {"pm_uncertainty": 1.0, "pm_market_quality_score": 0.0}},
    }
    diagnostics: dict = {}
    _out, allow, mult = apply_meta_policy_overlay(
        signal=signal, diagnostics=diagnostics, skill_dir=SKILL_DIR, mode="shadow"
    )
    assert allow is True
    assert mult == 1.0  # shadow ignores the multiplier output
    # The wrapped function still bumps its processed counter.
    assert diagnostics.get("meta_policy_processed", 0) >= 1


# --------------------------------------------------------------------------- #
# Event-risk overlay
# --------------------------------------------------------------------------- #


def test_event_risk_pead_close_flags():
    policy = evaluate_event_risk_for_backtest(
        ticker="AAPL",
        entry_date=pd.Timestamp("2024-04-01"),
        pead_info={"days_until_earnings": 1},
        skill_dir=SKILL_DIR,
        mode="live",
    )
    assert policy["flagged"] is True
    assert policy["earnings_near"] is True


def test_event_risk_no_pead_passes():
    policy = evaluate_event_risk_for_backtest(
        ticker="AAPL",
        entry_date=pd.Timestamp("2024-04-01"),
        pead_info=None,
        skill_dir=SKILL_DIR,
        mode="live",
    )
    assert policy["flagged"] is False
    assert policy["earnings_near"] is False


def test_event_risk_overlay_off_passes_through():
    policy = {"flagged": True, "action": "block", "downsize_factor": 0.5}
    diagnostics: dict = {}
    allow, mult = apply_event_risk_overlay(policy=policy, diagnostics=diagnostics, mode="off")
    assert allow is True
    assert mult == 1.0
    assert diagnostics == {}


def test_event_risk_overlay_live_block_drops_entry():
    policy = {"flagged": True, "action": "block", "downsize_factor": 0.5}
    diagnostics: dict = {}
    allow, mult = apply_event_risk_overlay(policy=policy, diagnostics=diagnostics, mode="live")
    assert allow is False
    assert mult == 0.0
    assert diagnostics["event_risk_live_blocked"] == 1


def test_event_risk_overlay_live_downsize_keeps_entry():
    policy = {"flagged": True, "action": "downsize", "downsize_factor": 0.4}
    diagnostics: dict = {}
    allow, mult = apply_event_risk_overlay(policy=policy, diagnostics=diagnostics, mode="live")
    assert allow is True
    assert mult == pytest.approx(0.4)
    assert diagnostics["event_risk_live_downsized"] == 1


def test_event_risk_overlay_shadow_never_blocks_but_counts():
    policy = {"flagged": True, "action": "block", "downsize_factor": 0.5}
    diagnostics: dict = {}
    allow, mult = apply_event_risk_overlay(policy=policy, diagnostics=diagnostics, mode="shadow")
    assert allow is True
    assert mult == 1.0
    assert diagnostics["event_risk_shadow_flagged"] == 1
    assert diagnostics["event_risk_shadow_would_block"] == 1


# --------------------------------------------------------------------------- #
# Exit-manager overlay
# --------------------------------------------------------------------------- #


def test_exit_manager_off_matches_legacy_simulate_exit():
    # Build a price path that triggers a trailing stop on day 5.
    prices = [100.0, 105.0, 110.0, 108.0, 100.0, 95.0, 96.0, 97.0, 98.0]
    df = _build_df(prices)
    px, ts, reason, info = simulate_exit_with_manager(
        df, entry_idx=0, hold_days_default=8, stop_pct=0.05, skill_dir=SKILL_DIR, mode="off"
    )
    # 110 high triggers stop at 110 * 0.95 = 104.5; close 100 on day 4 trips it.
    assert reason == "trailing_stop"
    assert px == pytest.approx(100.0)
    assert info["mode"] == "off"


def test_exit_manager_live_takes_partial_then_breakeven():
    # Strong uptrend: hits +1.5R partial well above breakeven, then drifts down
    # past the new (breakeven) stop on a later day. With partial_r_mult=1.5 and
    # stop_pct=0.05 the partial target is 100 * (1 + 1.5 * 0.05) = 107.5.
    prices = [100.0, 102.0, 105.0, 108.0, 109.0, 102.0, 99.0, 98.0]
    df = _build_df(prices)
    px, _ts, reason, info = simulate_exit_with_manager(
        df, entry_idx=0, hold_days_default=10, stop_pct=0.05, skill_dir=SKILL_DIR, mode="live"
    )
    # Should record a partial fill; the final exit is from breakeven trigger
    # (close 99 trips the breakeven stop at 100) so reason mentions partial.
    assert info["managed"]["partial_done"] is True
    assert reason in {"trailing_stop_after_partial", "partial_then_time_exit"}
    # Equivalent exit price reflects 50/50 of partial @108 and final exit
    # near 100 -> roughly 104. Legacy (single trailing stop on close 102 ->
    # 109 high triggers stop at 103.55, hit on day 5 close 102) would have
    # given ~102. So the manager should improve realised return.
    assert px > 102.0


def test_exit_manager_shadow_returns_legacy_but_records_managed():
    prices = [100.0, 102.0, 105.0, 108.0, 109.0, 102.0, 99.0, 98.0]
    df = _build_df(prices)
    legacy_px, _, legacy_reason, _ = simulate_exit_with_manager(
        df, entry_idx=0, hold_days_default=10, stop_pct=0.05, skill_dir=SKILL_DIR, mode="off"
    )
    shadow_px, _, shadow_reason, info = simulate_exit_with_manager(
        df, entry_idx=0, hold_days_default=10, stop_pct=0.05, skill_dir=SKILL_DIR, mode="shadow"
    )
    assert shadow_px == pytest.approx(legacy_px)
    assert shadow_reason == legacy_reason
    assert "shadow" in info
    assert info["shadow"]["managed_exit_price"] != info["shadow"]["legacy_exit_price"]


def test_exit_manager_time_exit_on_flat_market():
    prices = [100.0] * 20
    df = _build_df(prices)
    px, _ts, reason, info = simulate_exit_with_manager(
        df, entry_idx=0, hold_days_default=15, stop_pct=0.05, skill_dir=SKILL_DIR, mode="live"
    )
    # No partial, no stop trigger -> time exit at exit_date == entry + EXIT_MAX_HOLD_DAYS.
    assert reason == "time_exit"
    assert px == pytest.approx(100.0)
    assert info["managed"]["partial_done"] is False


# --------------------------------------------------------------------------- #
# Exec-quality overlay
# --------------------------------------------------------------------------- #


def test_exec_quality_off_is_noop():
    eff, info = apply_exec_quality_overlay(
        slippage_bps_per_side=15.0,
        day_volume=1_000_000,
        qty=100,
        skill_dir=SKILL_DIR,
        mode="off",
    )
    assert eff == 15.0
    assert info["applied"] is False
    assert info["effective_slippage_bps"] == 15.0


def test_exec_quality_live_liquid_halves_spread():
    eff, info = apply_exec_quality_overlay(
        slippage_bps_per_side=15.0,
        day_volume=10_000_000,  # huge volume -> participation 0.001%
        qty=100,
        skill_dir=SKILL_DIR,
        mode="live",
    )
    assert info["regime"] == "liquid_limit"
    assert eff == pytest.approx(7.5)
    assert info["applied"] is True


def test_exec_quality_live_illiquid_inflates_spread():
    eff, info = apply_exec_quality_overlay(
        slippage_bps_per_side=15.0,
        day_volume=1_000,
        qty=100,  # 10% participation -> illiquid
        skill_dir=SKILL_DIR,
        mode="live",
    )
    assert info["regime"] == "illiquid_market"
    assert eff == pytest.approx(22.5)


def test_exec_quality_shadow_reports_but_keeps_raw():
    eff, info = apply_exec_quality_overlay(
        slippage_bps_per_side=15.0,
        day_volume=10_000_000,
        qty=100,
        skill_dir=SKILL_DIR,
        mode="shadow",
    )
    assert eff == 15.0
    assert info["shadow_effective_slippage_bps"] == pytest.approx(7.5)
    assert info["applied"] is False


# --------------------------------------------------------------------------- #
# End-to-end: backtest call shape preserved when overlays are off
# --------------------------------------------------------------------------- #


def test_backtest_signature_accepts_overlay_argument():
    # Smoke-test only: imports the function and verifies the new keyword is
    # accepted. We don't actually run a backtest here (network-dependent),
    # just confirm the signature.
    import inspect

    from backtest import run_backtest

    sig = inspect.signature(run_backtest)
    assert "intelligence_overlay" in sig.parameters
    # Default must be None so existing call sites are unaffected.
    assert sig.parameters["intelligence_overlay"].default is None


def test_overlay_dataclass_idempotent_serialisation():
    cfg = BacktestIntelligenceConfig(meta_policy="live", event_risk="shadow")
    rebuilt = BacktestIntelligenceConfig.from_mapping(cfg.as_dict())
    assert rebuilt.meta_policy == "live"
    assert rebuilt.event_risk == "shadow"
    assert rebuilt.exit_manager == "off"


# Avoid relying on numpy in case the env is bare; explicitly imported above
# so a missing-package failure is clearly attributed.
def test_environment_has_numpy():
    assert np.__version__
