#!/usr/bin/env python3
"""
Validate Event-Risk blocker behavior in scanner and execution.
"""

from __future__ import annotations

import sys
from datetime import date
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


def _check_scanner_earnings_live_block() -> tuple[bool, str]:
    import signal_scanner as scanner

    diagnostics = {"event_risk_flagged": 0, "event_risk_blocked": 0, "event_risk_downsized": 0}
    signals = [{"ticker": "AAPL", "signal_score": 80}]
    with _temporary_env(
        {
            "EVENT_RISK_MODE": "live",
            "EVENT_ACTION": "block",
            "EVENT_BLOCK_EARNINGS_DAYS": "2",
            "EVENT_MACRO_BLACKOUT_ENABLED": "false",
        }
    ):
        with patch.object(scanner, "_nearest_earnings_distance_days", return_value=1):
            out = scanner._apply_event_risk_policy_to_signals(signals, diagnostics, SKILL_DIR)
    if out:
        return False, "scanner live/block should suppress earnings-near signal"
    if diagnostics.get("event_risk_flagged") != 1 or diagnostics.get("event_risk_blocked") != 1:
        return False, f"unexpected diagnostics for live/block: {diagnostics}"
    return True, "scanner earnings near-date live/block"


def _check_scanner_shadow_downsize_tag() -> tuple[bool, str]:
    import signal_scanner as scanner

    diagnostics = {"event_risk_flagged": 0, "event_risk_blocked": 0, "event_risk_downsized": 0}
    signals = [{"ticker": "MSFT", "signal_score": 79}]
    with _temporary_env(
        {
            "EVENT_RISK_MODE": "shadow",
            "EVENT_ACTION": "downsize",
            "EVENT_BLOCK_EARNINGS_DAYS": "2",
            "EVENT_MACRO_BLACKOUT_ENABLED": "false",
        }
    ):
        with patch.object(scanner, "_nearest_earnings_distance_days", return_value=1):
            out = scanner._apply_event_risk_policy_to_signals(signals, diagnostics, SKILL_DIR)
    if len(out) != 1:
        return False, "scanner shadow/downsize should keep signal with tag"
    tag = ((out[0].get("event_risk") or {}).get("shadow_action") or "").strip()
    if tag != "would_downsize":
        return False, f"expected shadow would_downsize tag, got {tag!r}"
    if diagnostics.get("event_risk_downsized") != 1:
        return False, "scanner shadow/downsize should increment event_risk_downsized"
    return True, "scanner shadow downsize tag"


def _check_macro_blackout_on_off() -> tuple[bool, str]:
    import signal_scanner as scanner

    with _temporary_env(
        {
            "EVENT_RISK_MODE": "live",
            "EVENT_ACTION": "block",
            "EVENT_MACRO_BLACKOUT_ENABLED": "true",
        }
    ):
        with (
            patch.object(scanner, "_nearest_earnings_distance_days", return_value=None),
            patch.object(scanner, "_load_macro_blackout_dates", return_value={date.today().isoformat()}),
        ):
            on = scanner.evaluate_event_risk_policy("AAPL", SKILL_DIR)
    with _temporary_env(
        {
            "EVENT_RISK_MODE": "live",
            "EVENT_ACTION": "block",
            "EVENT_MACRO_BLACKOUT_ENABLED": "false",
        }
    ):
        with (
            patch.object(scanner, "_nearest_earnings_distance_days", return_value=None),
            patch.object(scanner, "_load_macro_blackout_dates", return_value={date.today().isoformat()}),
        ):
            off = scanner.evaluate_event_risk_policy("AAPL", SKILL_DIR)
    if not on.get("macro_blackout") or not on.get("flagged"):
        return False, f"macro blackout enabled should flag risk: {on}"
    if off.get("macro_blackout") or off.get("flagged"):
        return False, f"macro blackout disabled should not flag alone: {off}"
    return True, "macro blackout on/off behavior"


def _check_execution_live_block_and_downsize() -> tuple[bool, str]:
    import execution

    auth = _FakeAuth(SKILL_DIR)
    # LIVE + block
    with _temporary_env(
        {
            "EVENT_RISK_MODE": "live",
            "EVENT_ACTION": "block",
            "SECTOR_FILTER_ENABLED": "false",
            "EXECUTION_SHADOW_MODE": "false",
            "EXEC_QUALITY_MODE": "off",
        }
    ):
        with (
            patch.object(execution, "DualSchwabAuth", _FakeAuth),
            patch.object(execution, "_get_account_hash_for_orders", return_value="H"),
            patch.object(execution.GuardrailWrapper, "_check_guardrails", return_value=None),
            patch("sector_strength.is_market_regime_bullish", return_value=(True, {"spy_price": 500, "spy_sma_200": 490})),
            patch("signal_scanner.evaluate_event_risk_policy", return_value={"flagged": True, "reasons": ["earnings_within_2d"], "mode": "live", "action": "block", "downsize_factor": 0.5}),
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
    if not isinstance(blocked, str) or "EVENT RISK BLOCK" not in blocked:
        return False, f"execution live/block should reject BUY, got {blocked!r}"
    if post_mock.called:
        return False, "execution live/block should not submit order"

    # LIVE + downsize
    with _temporary_env(
        {
            "EVENT_RISK_MODE": "live",
            "EVENT_ACTION": "downsize",
            "EVENT_DOWNSIZE_FACTOR": "0.5",
            "SECTOR_FILTER_ENABLED": "false",
            "EXECUTION_SHADOW_MODE": "false",
            "EXEC_QUALITY_MODE": "off",
        }
    ):
        with (
            patch.object(execution, "DualSchwabAuth", _FakeAuth),
            patch.object(execution, "_get_account_hash_for_orders", return_value="H"),
            patch.object(execution.GuardrailWrapper, "_check_guardrails", return_value=None),
            patch("sector_strength.is_market_regime_bullish", return_value=(True, {"spy_price": 500, "spy_sma_200": 490})),
            patch("signal_scanner.evaluate_event_risk_policy", return_value={"flagged": True, "reasons": ["earnings_within_2d"], "mode": "live", "action": "downsize", "downsize_factor": 0.5}),
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
        return False, f"execution live/downsize should place order, got {placed!r}"
    event_payload = placed.get("_event_risk", {}) or {}
    if event_payload.get("downsized_qty_after") != 5:
        return False, f"expected downsized qty 5, got {event_payload}"
    return True, "execution live block + downsize"


def main() -> int:
    checks = [
        _check_scanner_earnings_live_block(),
        _check_scanner_shadow_downsize_tag(),
        _check_macro_blackout_on_off(),
        _check_execution_live_block_and_downsize(),
    ]
    failures: list[str] = []
    for ok, label in checks:
        if ok:
            print(f"PASS: {label}")
        else:
            failures.append(label)
            print(f"FAIL: {label}")
    if failures:
        print(f"Event risk validation failed: {failures}")
        return 1
    print("PASS: event risk validation checks succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
