#!/usr/bin/env python3
"""
Validate Exit Manager v1 idempotent behavior.
"""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


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


class _FakeResponse:
    def __init__(self, order_id: str):
        self.status_code = 201
        self.text = "{}"
        self.headers = {"Location": f"https://api.schwabapi.com/trader/v1/accounts/H/orders/{order_id}"}

    @property
    def ok(self) -> bool:
        return True

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {}


class _FakeCancelResponse:
    status_code = 200
    text = ""
    ok = True


@contextmanager
def _temporary_env(overrides: dict[str, str]):
    old: dict[str, str | None] = {}
    try:
        for key, value in overrides.items():
            old[key] = os.environ.get(key)
            os.environ[key] = str(value)
        yield
    finally:
        for key, prev in old.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev


def _check_partial_once_and_restart_safe(tmp_skill_dir: Path) -> tuple[bool, str]:
    import execution

    auth = _FakeAuth(tmp_skill_dir)
    execution.register_exit_manager_entry(
        skill_dir=tmp_skill_dir,
        ticker="AAPL",
        entry_order_id="ENTRY1",
        qty=10,
        entry_price=100.0,
        stop_order_id="STOP1",
        stop_pct=0.05,
    )

    post_calls: list[str] = []

    def _fake_post(_url, _payload, _auth):
        order_id = "PARTIAL1"
        post_calls.append(order_id)
        return _FakeResponse(order_id)

    with (
        patch.object(execution, "_get_quote_quality_snapshot", return_value={"bid": 105.0, "ask": 105.2, "last": 106.0, "mid": 105.1, "spread_bps": 19.0}),
        patch.object(execution, "_post_order_with_refresh", side_effect=_fake_post),
        patch("order_monitor.start_fill_monitor", return_value=None),
    ):
        execution.run_exit_manager_sweep(auth=auth, skill_dir=tmp_skill_dir, account_hash="H")
        execution.run_exit_manager_sweep(auth=auth, skill_dir=tmp_skill_dir, account_hash="H")

    if len(post_calls) != 1:
        return False, f"partial TP should place once, got {len(post_calls)} calls"

    state = execution._load_exit_manager_state(tmp_skill_dir)
    pos = state.get("positions", {}).get("AAPL:ENTRY1", {})
    if not pos.get("partial_tp_order_id"):
        return False, "partial_tp_order_id missing after first trigger"

    # Simulate restart by reloading state and sweeping again; still no duplicate.
    with (
        patch.object(execution, "_get_quote_quality_snapshot", return_value={"bid": 105.0, "ask": 105.2, "last": 106.0, "mid": 105.1, "spread_bps": 19.0}),
        patch.object(execution, "_post_order_with_refresh", side_effect=_fake_post),
        patch("order_monitor.start_fill_monitor", return_value=None),
    ):
        execution.run_exit_manager_sweep(auth=auth, skill_dir=tmp_skill_dir, account_hash="H")

    if len(post_calls) != 1:
        return False, "restart-safe idempotency failed: duplicate partial order placed"
    return True, "partial triggered once + restart-safe"


def _check_breakeven_move_once(tmp_skill_dir: Path) -> tuple[bool, str]:
    import execution

    auth = _FakeAuth(tmp_skill_dir)
    state = execution._load_exit_manager_state(tmp_skill_dir)
    pos = state.get("positions", {}).get("AAPL:ENTRY1", {})
    partial_id = pos.get("partial_tp_order_id")
    if not partial_id:
        return False, "missing partial order id for breakeven test"

    execution.on_exit_manager_sell_fill(
        skill_dir=tmp_skill_dir,
        ticker="AAPL",
        order_id=partial_id,
        qty=5,
    )

    post_calls: list[str] = []

    def _fake_post(_url, _payload, _auth):
        order_id = "BE1"
        post_calls.append(order_id)
        return _FakeResponse(order_id)

    with (
        patch.object(execution, "_get_quote_quality_snapshot", return_value={"bid": 104.0, "ask": 104.2, "last": 104.1, "mid": 104.1, "spread_bps": 19.0}),
        patch.object(execution, "_post_order_with_refresh", side_effect=_fake_post),
        patch.object(execution, "_cancel_order_with_refresh", return_value=_FakeCancelResponse()),
    ):
        execution.run_exit_manager_sweep(auth=auth, skill_dir=tmp_skill_dir, account_hash="H")
        execution.run_exit_manager_sweep(auth=auth, skill_dir=tmp_skill_dir, account_hash="H")

    if len(post_calls) != 1:
        return False, f"breakeven stop move should place once, got {len(post_calls)}"

    state = execution._load_exit_manager_state(tmp_skill_dir)
    pos = state.get("positions", {}).get("AAPL:ENTRY1", {})
    if not pos.get("breakeven_done"):
        return False, "breakeven_done flag not set"
    return True, "stop move once after partial fill"


def _check_time_stop_once(tmp_skill_dir: Path) -> tuple[bool, str]:
    import execution

    auth = _FakeAuth(tmp_skill_dir)
    execution.register_exit_manager_entry(
        skill_dir=tmp_skill_dir,
        ticker="MSFT",
        entry_order_id="ENTRY2",
        qty=8,
        entry_price=100.0,
        stop_order_id="STOP2",
        stop_pct=0.05,
    )
    state = execution._load_exit_manager_state(tmp_skill_dir)
    pos = state.get("positions", {}).get("MSFT:ENTRY2", {})
    pos["entry_date"] = (date.today() - timedelta(days=20)).isoformat()
    state["positions"]["MSFT:ENTRY2"] = pos
    execution._save_exit_manager_state(tmp_skill_dir, state)

    post_calls: list[str] = []

    def _fake_post(_url, _payload, _auth):
        order_id = "TIME1"
        post_calls.append(order_id)
        return _FakeResponse(order_id)

    with (
        patch.object(execution, "_get_quote_quality_snapshot", return_value={"bid": 99.0, "ask": 99.2, "last": 99.1, "mid": 99.1, "spread_bps": 20.0}),
        patch.object(execution, "_post_order_with_refresh", side_effect=_fake_post),
        patch("order_monitor.start_fill_monitor", return_value=None),
    ):
        execution.run_exit_manager_sweep(auth=auth, skill_dir=tmp_skill_dir, account_hash="H")
        execution.run_exit_manager_sweep(auth=auth, skill_dir=tmp_skill_dir, account_hash="H")

    if len(post_calls) != 1:
        return False, f"time stop should place once, got {len(post_calls)}"
    state = execution._load_exit_manager_state(tmp_skill_dir)
    pos = state.get("positions", {}).get("MSFT:ENTRY2", {})
    if not pos.get("time_stop_done"):
        return False, "time_stop_done flag not set"
    return True, "time stop once"


def main() -> int:
    checks = []
    with tempfile.TemporaryDirectory(prefix="exit_manager_validate_") as td:
        tmp_skill_dir = Path(td)
        with _temporary_env(
            {
                "EXIT_MANAGER_MODE": "live",
                "EXIT_PARTIAL_TP_R_MULT": "1.0",
                "EXIT_PARTIAL_TP_FRACTION": "0.5",
                "EXIT_BREAKEVEN_AFTER_PARTIAL": "true",
                "EXIT_MAX_HOLD_DAYS": "12",
            }
        ):
            checks = [
                _check_partial_once_and_restart_safe(tmp_skill_dir),
                _check_breakeven_move_once(tmp_skill_dir),
                _check_time_stop_once(tmp_skill_dir),
            ]

    failures: list[str] = []
    for ok, label in checks:
        if ok:
            print(f"PASS: {label}")
        else:
            failures.append(label)
            print(f"FAIL: {label}")
    if failures:
        print(f"Exit manager validation failed: {failures}")
        return 1
    print("PASS: exit manager validation checks succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
