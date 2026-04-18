#!/usr/bin/env python3
"""
Validate execution quality plugin OFF|SHADOW|LIVE behavior.
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
    def __init__(self, skill_dir: Path | str | None = None):
        self.skill_dir = Path(skill_dir or SKILL_DIR)
        self.account_session = _FakeSession()
        self.market_session = _FakeSession()

    def get_account_token(self) -> str:
        return "fake-account-token"

    def get_market_token(self) -> str:
        return "fake-market-token"


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 201,
        payload: dict | None = None,
        text: str = "{}",
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {"Location": "https://api.schwabapi.com/trader/v1/accounts/FAKE/orders/ORDER123"}

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> dict:
        return dict(self._payload)


def _temporary_env(overrides: dict[str, str]):
    return temporary_env(overrides)


def _check_shadow_passthrough() -> tuple[bool, str]:
    import execution

    with _temporary_env(
        {
            "EXEC_QUALITY_MODE": "shadow",
            "SECTOR_FILTER_ENABLED": "false",
            "EXECUTION_SHADOW_MODE": "false",
        }
    ):
        with (
            patch.object(execution, "DualSchwabAuth", _FakeAuth),
            patch.object(execution, "_get_account_hash_for_orders", return_value="FAKEHASH"),
            patch.object(execution.GuardrailWrapper, "_check_guardrails", return_value=None),
            patch.object(execution, "_get_quote_quality_snapshot", return_value={"bid": 99.0, "ask": 101.0, "last": 100.0, "spread_bps": 200.0}),
            patch.object(execution, "send_alert", return_value=True),
            patch("requests.post") as post_mock,
        ):
            post_mock.return_value = _FakeResponse()
            result = execution.place_order(
                ticker="AAPL",
                qty=1,
                side="SELL",
                order_type="MARKET",
                skill_dir=SKILL_DIR,
                auth=_FakeAuth(SKILL_DIR),
            )

    if not isinstance(result, dict):
        return False, f"shadow passthrough expected dict result, got {result!r}"
    if not post_mock.called:
        return False, "shadow mode should still place original order path"
    req_payload = post_mock.call_args.kwargs.get("json") or {}
    if req_payload.get("orderType") != "MARKET":
        return False, f"shadow mode mutated order type: {req_payload.get('orderType')}"
    if not (result.get("_execution_quality", {}) or {}).get("would_block"):
        return False, "shadow mode should record would_block diagnostics on wide spread"
    return True, "execution quality shadow passthrough"


def _check_live_blocks_on_quality() -> tuple[bool, str]:
    import execution

    with _temporary_env(
        {
            "EXEC_QUALITY_MODE": "live",
            "SECTOR_FILTER_ENABLED": "false",
            "EXECUTION_SHADOW_MODE": "false",
            "EXEC_SPREAD_MAX_BPS": "35",
        }
    ):
        with (
            patch.object(execution, "DualSchwabAuth", _FakeAuth),
            patch.object(execution, "_get_account_hash_for_orders", return_value="FAKEHASH"),
            patch.object(execution.GuardrailWrapper, "_check_guardrails", return_value=None),
            patch.object(execution, "_get_quote_quality_snapshot", return_value={"bid": 99.0, "ask": 101.0, "last": 100.0, "spread_bps": 200.0}),
            patch.object(execution, "send_alert", return_value=True),
            patch("requests.post") as post_mock,
        ):
            result = execution.place_order(
                ticker="AAPL",
                qty=1,
                side="SELL",
                order_type="MARKET",
                skill_dir=SKILL_DIR,
                auth=_FakeAuth(SKILL_DIR),
            )

    if not isinstance(result, str) or "EXECUTION QUALITY BLOCK" not in result:
        return False, f"live mode should block on wide spread, got: {result!r}"
    if post_mock.called:
        return False, "live blocked order should not call requests.post"
    return True, "execution quality live block"


def _check_live_limit_upgrade() -> tuple[bool, str]:
    import execution

    with _temporary_env(
        {
            "EXEC_QUALITY_MODE": "live",
            "SECTOR_FILTER_ENABLED": "false",
            "EXECUTION_SHADOW_MODE": "false",
            "EXEC_REPRICE_ATTEMPTS": "0",
            "EXEC_USE_LIMIT_FOR_LIQUID": "true",
            "EXEC_SPREAD_MAX_BPS": "35",
            "EXEC_SLIPPAGE_MAX_BPS": "20",
        }
    ):
        with (
            patch.object(execution, "DualSchwabAuth", _FakeAuth),
            patch.object(execution, "_get_account_hash_for_orders", return_value="FAKEHASH"),
            patch.object(execution.GuardrailWrapper, "_check_guardrails", return_value=None),
            patch.object(execution, "_get_quote_quality_snapshot", return_value={"bid": 99.9, "ask": 100.0, "last": 100.0, "spread_bps": 10.0}),
            patch.object(execution, "send_alert", return_value=True),
            patch("requests.post") as post_mock,
        ):
            post_mock.return_value = _FakeResponse()
            result = execution.place_order(
                ticker="AAPL",
                qty=1,
                side="SELL",
                order_type="MARKET",
                skill_dir=SKILL_DIR,
                auth=_FakeAuth(SKILL_DIR),
            )

    if not isinstance(result, dict):
        return False, f"live limit-upgrade expected dict result, got {result!r}"
    if not post_mock.called:
        return False, "live limit-upgrade should submit order"
    req_payload = post_mock.call_args.kwargs.get("json") or {}
    if req_payload.get("orderType") != "LIMIT":
        return False, f"expected LIMIT upgrade payload, got {req_payload.get('orderType')}"
    if "price" not in req_payload:
        return False, "expected LIMIT payload to include price"
    eq = result.get("_execution_quality", {}) or {}
    if not eq.get("limit_upgrade_applied"):
        return False, "expected limit_upgrade_applied in execution quality diagnostics"
    return True, "execution quality live limit upgrade"


def main() -> int:
    checks = [
        _check_shadow_passthrough,
        _check_live_blocks_on_quality,
        _check_live_limit_upgrade,
    ]
    failures: list[str] = []
    for check in checks:
        ok, label = check()
        if ok:
            print(f"PASS: {label}")
        else:
            failures.append(label)
            print(f"FAIL: {label}")
    if failures:
        print(f"Execution quality validation failed: {failures}")
        return 1
    print("PASS: execution quality validation checks succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
