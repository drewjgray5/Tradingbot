#!/usr/bin/env python3
"""
Validate execution shadow mode behavior without placing live orders.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


class _FakeSession:
    def force_refresh(self) -> bool:
        return True


class _FakeAuth:
    def __init__(self, skill_dir: Path | str | None = None):
        self.skill_dir = Path(skill_dir or SKILL_DIR)
        self.account_session = _FakeSession()
        self.market_session = _FakeSession()

    def get_account_token(self) -> str:
        return "fake-account-token"

    def get_market_token(self) -> str:
        return "fake-market-token"


def main() -> int:
    import execution

    os.environ["EXECUTION_SHADOW_MODE"] = "true"
    os.environ["SECTOR_FILTER_ENABLED"] = "false"
    os.environ["EVENT_RISK_MODE"] = "off"

    with (
        patch.object(execution, "DualSchwabAuth", _FakeAuth),
        patch.object(execution, "_get_account_hash_for_orders", return_value="FAKEHASH"),
        patch.object(execution, "send_alert", return_value=True),
        patch.object(execution.GuardrailWrapper, "_check_guardrails", return_value=None),
        patch.object(execution.GuardrailWrapper, "_get_quote_price", return_value=200.0),
        patch("requests.post") as post_mock,
    ):
        result = execution.place_order(
            ticker="AAPL",
            qty=1,
            side="BUY",
            order_type="MARKET",
            skill_dir=SKILL_DIR,
            price_hint=200.0,
        )

    if not isinstance(result, dict):
        print(f"FAIL: Expected dict result in shadow mode, got: {result!r}")
        return 1
    if not result.get("shadow_mode"):
        print(f"FAIL: shadow_mode flag missing in result: {result}")
        return 1
    if result.get("shadow_action") != "would_place_order":
        print(f"FAIL: unexpected shadow_action: {result.get('shadow_action')}")
        return 1
    if post_mock.called:
        print("FAIL: requests.post was called during shadow mode.")
        return 1
    stop_state = (result.get("_stop_protection") or {}).get("status")
    if stop_state not in {"shadow_simulated", "not_applicable"}:
        print(f"FAIL: unexpected stop protection state: {stop_state}")
        return 1

    print("PASS: execution shadow mode validation succeeded")
    print(f"  shadow_action={result.get('shadow_action')}")
    print(f"  stop_protection={result.get('_stop_protection')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

