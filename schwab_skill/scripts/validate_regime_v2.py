#!/usr/bin/env python3
"""
Validate Regime v2 composite scoring and execution integration.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from env_overrides import temporary_env  # noqa: E402


class _FakeSession:
    def force_refresh(self) -> bool:
        return True


class _FakeAuth:
    def __init__(self, skill_dir: Path):
        self.skill_dir = skill_dir
        self.account_session = _FakeSession()
        self.market_session = _FakeSession()

    def get_account_token(self) -> str:
        return "fake-token"

    def get_market_token(self) -> str:
        return "fake-market-token"


class _FakeResponse:
    status_code = 201
    text = "{}"
    headers = {"Location": "https://api.schwabapi.com/trader/v1/accounts/H/orders/O1"}

    @property
    def ok(self) -> bool:
        return True

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {}


def _temporary_env(overrides: dict[str, str]):
    return temporary_env(overrides)


def _check_deterministic_scoring() -> tuple[bool, str]:
    from sector_strength import compute_regime_v2_score_from_inputs

    kwargs = dict(
        spy_above_200=True,
        spy_50_above_200=True,
        spy_50_slope_up=True,
        vix_value=17.5,
        breadth_ratio=0.64,
        sector_dispersion_pct=3.2,
    )
    a = compute_regime_v2_score_from_inputs(**kwargs)
    b = compute_regime_v2_score_from_inputs(**kwargs)
    if a != b:
        return False, f"deterministic scoring mismatch: {a} != {b}"
    return True, "regime v2 deterministic score"


def _check_bucket_classification() -> tuple[bool, str]:
    from sector_strength import compute_regime_v2_score_from_inputs

    high = compute_regime_v2_score_from_inputs(
        spy_above_200=True,
        spy_50_above_200=True,
        spy_50_slope_up=True,
        vix_value=14.0,
        breadth_ratio=0.82,
        sector_dispersion_pct=2.2,
    )
    med = compute_regime_v2_score_from_inputs(
        spy_above_200=True,
        spy_50_above_200=False,
        spy_50_slope_up=True,
        vix_value=22.0,
        breadth_ratio=0.55,
        sector_dispersion_pct=5.5,
    )
    low = compute_regime_v2_score_from_inputs(
        spy_above_200=False,
        spy_50_above_200=False,
        spy_50_slope_up=False,
        vix_value=33.0,
        breadth_ratio=0.18,
        sector_dispersion_pct=9.0,
    )
    if high.get("bucket") != "high":
        return False, f"expected high bucket, got {high}"
    if med.get("bucket") != "medium":
        return False, f"expected medium bucket, got {med}"
    if low.get("bucket") != "low":
        return False, f"expected low bucket, got {low}"
    return True, "regime v2 high/medium/low buckets"


def _check_execution_gate_and_size() -> tuple[bool, str]:
    import execution

    auth = _FakeAuth(SKILL_DIR)
    with _temporary_env(
        {
            "REGIME_V2_MODE": "live",
            "REGIME_V2_ENTRY_MIN_SCORE": "55",
            "REGIME_V2_SIZE_MULT_HIGH": "1.0",
            "REGIME_V2_SIZE_MULT_MED": "0.7",
            "REGIME_V2_SIZE_MULT_LOW": "0.4",
            "SECTOR_FILTER_ENABLED": "false",
            "EXECUTION_SHADOW_MODE": "false",
            "EVENT_RISK_MODE": "off",
            "EXEC_QUALITY_MODE": "off",
        }
    ):
        # Low bucket blocks.
        with (
            patch.object(execution, "DualSchwabAuth", _FakeAuth),
            patch.object(execution, "_get_account_hash_for_orders", return_value="H"),
            patch.object(execution.GuardrailWrapper, "_check_guardrails", return_value=None),
            patch("sector_strength.is_market_regime_bullish", return_value=(True, {"spy_price": 500, "spy_sma_200": 490})),
            patch("sector_strength.get_regime_v2_snapshot", return_value={"score": 40.0, "bucket": "low", "components": {}}),
            patch.object(execution, "send_alert", return_value=True),
            patch("requests.post") as post_mock,
        ):
            blocked = execution.place_order(
                ticker="AAPL",
                qty=10,
                side="BUY",
                order_type="MARKET",
                skill_dir=SKILL_DIR,
                auth=auth,
            )
        if not isinstance(blocked, str) or "REGIME V2 BLOCK" not in blocked:
            return False, f"expected regime v2 block, got {blocked!r}"
        if post_mock.called:
            return False, "blocked regime should not submit live order"

        # Medium bucket downsizes by multiplier.
        with (
            patch.object(execution, "DualSchwabAuth", _FakeAuth),
            patch.object(execution, "_get_account_hash_for_orders", return_value="H"),
            patch.object(execution.GuardrailWrapper, "_check_guardrails", return_value=None),
            patch("sector_strength.is_market_regime_bullish", return_value=(True, {"spy_price": 500, "spy_sma_200": 490})),
            patch("sector_strength.get_regime_v2_snapshot", return_value={"score": 60.0, "bucket": "medium", "components": {}}),
            patch.object(execution, "_post_order_with_refresh", return_value=_FakeResponse()),
            patch.object(execution, "send_alert", return_value=True),
            patch("order_monitor.start_fill_monitor", return_value=None),
        ):
            placed = execution.place_order(
                ticker="AAPL",
                qty=10,
                side="BUY",
                order_type="MARKET",
                skill_dir=SKILL_DIR,
                auth=auth,
            )
        if not isinstance(placed, dict):
            return False, f"expected placement result, got {placed!r}"
        regime_payload = placed.get("_regime_v2", {}) or {}
        if regime_payload.get("qty_after") != 7:
            return False, f"expected qty_after=7 for medium bucket, got {regime_payload}"
    return True, "execution regime v2 gate + sizing"


def _check_scan_live_gate() -> tuple[bool, str]:
    import signal_scanner as scanner

    with _temporary_env(
        {
            "REGIME_V2_MODE": "live",
            "REGIME_V2_ENTRY_MIN_SCORE": "55",
        }
    ):
        with (
            patch("sector_strength.is_market_regime_bullish", return_value=(True, {"spy_price": 500, "spy_sma_200": 490})),
            patch("sector_strength.get_regime_v2_snapshot", return_value={"score": 42.0, "bucket": "low", "components": {}}),
            patch("schwab_auth.DualSchwabAuth", _FakeAuth),
            patch("sector_strength.get_winning_sector_etfs", return_value={"XLK", "XLF"}),
            patch.object(scanner, "_load_watchlist", return_value=[]),
            patch("notifier.send_alert", return_value=True),
        ):
            signals, diagnostics = scanner.scan_for_signals_detailed(skill_dir=SKILL_DIR)
    if signals:
        return False, "scan should return no signals when regime v2 live gate blocks"
    if int(diagnostics.get("regime_v2_blocked", 0) or 0) != 1:
        return False, f"expected regime_v2_blocked=1, got {diagnostics.get('regime_v2_blocked')}"
    return True, "scan regime v2 live gate"


def main() -> int:
    checks = [
        _check_deterministic_scoring(),
        _check_bucket_classification(),
        _check_execution_gate_and_size(),
        _check_scan_live_gate(),
    ]
    failures: list[str] = []
    for ok, label in checks:
        if ok:
            print(f"PASS: {label}")
        else:
            failures.append(label)
            print(f"FAIL: {label}")
    if failures:
        print(f"Regime v2 validation failed: {failures}")
        return 1
    print("PASS: regime v2 validation checks succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
