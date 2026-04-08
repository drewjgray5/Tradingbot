#!/usr/bin/env python3
"""
Validate OFF/SHADOW/LIVE plugin mode parsing and fallback behavior.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
EMPTY_ENV_SKILL_DIR = SKILL_DIR / "__validation_empty_env__"
sys.path.insert(0, str(SKILL_DIR))

from config import (  # noqa: E402
    get_correlation_guard_max_pair_corr,
    get_correlation_guard_mode,
    get_event_action,
    get_event_block_earnings_days,
    get_event_downsize_factor,
    get_event_macro_blackout_enabled,
    get_event_risk_blackout_minutes,
    get_event_risk_mode,
    get_exec_quality_min_signal_score,
    get_exec_quality_mode,
    get_exit_breakeven_after_partial,
    get_exit_manager_mode,
    get_exit_manager_trail_atr_mult,
    get_exit_max_hold_days,
    get_exit_partial_tp_fraction,
    get_exit_partial_tp_r_mult,
    get_regime_v2_entry_min_score,
    get_regime_v2_min_confidence,
    get_regime_v2_mode,
    get_regime_v2_size_mult_high,
    get_regime_v2_size_mult_low,
    get_regime_v2_size_mult_med,
)

MODE_GETTERS = {
    "EXEC_QUALITY_MODE": get_exec_quality_mode,
    "EXIT_MANAGER_MODE": get_exit_manager_mode,
    "EVENT_RISK_MODE": get_event_risk_mode,
    "CORRELATION_GUARD_MODE": get_correlation_guard_mode,
    "REGIME_V2_MODE": get_regime_v2_mode,
}

THRESHOLD_GETTERS = [
    ("EXEC_QUALITY_MIN_SIGNAL_SCORE", get_exec_quality_min_signal_score, 55),
    ("EXIT_PARTIAL_TP_R_MULT", get_exit_partial_tp_r_mult, 1.5),
    ("EXIT_PARTIAL_TP_FRACTION", get_exit_partial_tp_fraction, 0.5),
    ("EXIT_MAX_HOLD_DAYS", get_exit_max_hold_days, 12),
    ("EXIT_MANAGER_TRAIL_ATR_MULT", get_exit_manager_trail_atr_mult, 2.0),
    ("EVENT_BLOCK_EARNINGS_DAYS", get_event_block_earnings_days, 2),
    ("EVENT_DOWNSIZE_FACTOR", get_event_downsize_factor, 0.5),
    ("EVENT_RISK_BLACKOUT_MINUTES", get_event_risk_blackout_minutes, 30),
    ("CORRELATION_GUARD_MAX_PAIR_CORR", get_correlation_guard_max_pair_corr, 0.85),
    ("REGIME_V2_MIN_CONFIDENCE", get_regime_v2_min_confidence, 0.55),
    ("REGIME_V2_ENTRY_MIN_SCORE", get_regime_v2_entry_min_score, 55),
    ("REGIME_V2_SIZE_MULT_HIGH", get_regime_v2_size_mult_high, 1.0),
    ("REGIME_V2_SIZE_MULT_MED", get_regime_v2_size_mult_med, 0.7),
    ("REGIME_V2_SIZE_MULT_LOW", get_regime_v2_size_mult_low, 0.4),
]


@contextmanager
def _temporary_env(overrides: dict[str, str]):
    old: dict[str, str | None] = {}
    try:
        for key, value in overrides.items():
            old[key] = os.environ.get(key)
            os.environ[key] = str(value)
        yield
    finally:
        for key, previous in old.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def main() -> int:
    for env_key, getter in MODE_GETTERS.items():
        os.environ.pop(env_key, None)
        if getter(EMPTY_ENV_SKILL_DIR) != "off":
            print(f"FAIL: {env_key} default should resolve to off")
            return 1

    for env_key, getter in MODE_GETTERS.items():
        with _temporary_env({env_key: "OFF"}):
            if getter(EMPTY_ENV_SKILL_DIR) != "off":
                print(f"FAIL: {env_key}=OFF should normalize to off")
                return 1
        with _temporary_env({env_key: "shadow"}):
            if getter(EMPTY_ENV_SKILL_DIR) != "shadow":
                print(f"FAIL: {env_key}=shadow should resolve to shadow")
                return 1
        with _temporary_env({env_key: "LiVe"}):
            if getter(EMPTY_ENV_SKILL_DIR) != "live":
                print(f"FAIL: {env_key}=LiVe should normalize to live")
                return 1
        with _temporary_env({env_key: "not-a-mode"}):
            if getter(EMPTY_ENV_SKILL_DIR) != "off":
                print(f"FAIL: {env_key} invalid value should fallback to off")
                return 1

    for env_key, getter, expected_default in THRESHOLD_GETTERS:
        with _temporary_env({env_key: "not-a-number"}):
            resolved = getter(EMPTY_ENV_SKILL_DIR)
            if resolved != expected_default:
                print(f"FAIL: {env_key} invalid numeric should fallback to {expected_default}, got {resolved}")
                return 1

    with _temporary_env({"EXIT_BREAKEVEN_AFTER_PARTIAL": "not-a-bool"}):
        if get_exit_breakeven_after_partial(EMPTY_ENV_SKILL_DIR) is not True:
            print("FAIL: EXIT_BREAKEVEN_AFTER_PARTIAL invalid bool should fallback to true")
            return 1
    with _temporary_env({"EVENT_MACRO_BLACKOUT_ENABLED": "not-a-bool"}):
        if get_event_macro_blackout_enabled(EMPTY_ENV_SKILL_DIR) is not False:
            print("FAIL: EVENT_MACRO_BLACKOUT_ENABLED invalid bool should fallback to false")
            return 1
    with _temporary_env({"EVENT_ACTION": "not-valid"}):
        if get_event_action(EMPTY_ENV_SKILL_DIR) != "block":
            print("FAIL: EVENT_ACTION invalid should fallback to block")
            return 1

    print("PASS: plugin mode and threshold validation succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
