from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from execution import GuardrailWrapper, get_execution_safety_summary


class _FakeAuth:
    def get_account_token(self) -> str:
        return "token"


def _write_env(tmp_path: Path, body: str) -> None:
    (tmp_path / ".env").write_text(body)


def test_guardrail_blocks_when_max_trades_exceeded(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "\n".join(
            [
                "MAX_TOTAL_ACCOUNT_VALUE=500000",
                "MAX_POSITION_PER_TICKER=50000",
                "MAX_TRADES_PER_DAY=1",
            ]
        ),
    )
    wrapper: Any = GuardrailWrapper(cast(Any, _FakeAuth()), skill_dir=tmp_path)
    wrapper._trades_today = lambda: 1
    wrapper._get_account_balances = lambda _token: (1000.0, {"AAPL": 0.0})

    err = wrapper._check_guardrails(
        ticker="AAPL",
        quantity=1,
        order={"orderLegCollection": [{"instruction": "BUY"}]},
        order_value_usd=100.0,
    )
    assert err is not None
    assert "Maximum daily trades" in err


def test_guardrail_blocks_when_position_limit_exceeded(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "\n".join(
            [
                "MAX_TOTAL_ACCOUNT_VALUE=500000",
                "MAX_POSITION_PER_TICKER=500",
                "MAX_TRADES_PER_DAY=20",
            ]
        ),
    )
    wrapper: Any = GuardrailWrapper(cast(Any, _FakeAuth()), skill_dir=tmp_path)
    wrapper._trades_today = lambda: 0
    wrapper._get_account_balances = lambda _token: (1000.0, {"AAPL": 450.0})

    err = wrapper._check_guardrails(
        ticker="AAPL",
        quantity=1,
        order={"orderLegCollection": [{"instruction": "BUY"}]},
        order_value_usd=100.0,
    )
    assert err is not None
    assert "Position size for AAPL" in err


def test_sell_order_bypasses_entry_guardrails(tmp_path: Path) -> None:
    _write_env(tmp_path, "MAX_TRADES_PER_DAY=0")
    wrapper: Any = GuardrailWrapper(cast(Any, _FakeAuth()), skill_dir=tmp_path)
    wrapper._trades_today = lambda: 999
    wrapper._get_account_balances = lambda _token: (9999999.0, {"AAPL": 9999999.0})

    err = wrapper._check_guardrails(
        ticker="AAPL",
        quantity=10,
        order={"orderLegCollection": [{"instruction": "SELL"}]},
        order_value_usd=1000.0,
    )
    assert err is None


def test_data_quality_policy_blocks_buy_when_not_ok(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "\n".join(
            [
                "MAX_TOTAL_ACCOUNT_VALUE=500000",
                "MAX_POSITION_PER_TICKER=50000",
                "MAX_TRADES_PER_DAY=20",
                "DATA_QUALITY_EXEC_POLICY=block_risk_increasing",
            ]
        ),
    )
    wrapper: Any = GuardrailWrapper(cast(Any, _FakeAuth()), skill_dir=tmp_path)
    wrapper._trades_today = lambda: 0
    wrapper._get_account_balances = lambda _token: (1000.0, {"AAPL": 0.0})

    with patch(
        "data_health.assess_symbol_data_health",
        return_value={"data_quality": "stale", "reasons": ["quote_stale"], "details": {}},
    ):
        err = wrapper._check_guardrails(
            ticker="AAPL",
            quantity=1,
            order={"orderLegCollection": [{"instruction": "BUY"}]},
            order_value_usd=100.0,
        )
    assert err is not None
    assert "Data quality" in err


def test_data_quality_warn_policy_allows_buy(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "\n".join(
            [
                "MAX_TOTAL_ACCOUNT_VALUE=500000",
                "MAX_POSITION_PER_TICKER=50000",
                "MAX_TRADES_PER_DAY=20",
                "DATA_QUALITY_EXEC_POLICY=warn",
            ]
        ),
    )
    wrapper: Any = GuardrailWrapper(cast(Any, _FakeAuth()), skill_dir=tmp_path)
    wrapper._trades_today = lambda: 0
    wrapper._get_account_balances = lambda _token: (1000.0, {"AAPL": 0.0})

    degraded = {
        "data_quality": "degraded",
        "reasons": ["sec_cache_empty_or_missing"],
        "details": {},
    }
    with patch("data_health.assess_symbol_data_health", return_value=degraded):
        err = wrapper._check_guardrails(
            ticker="AAPL",
            quantity=1,
            order={"orderLegCollection": [{"instruction": "BUY"}]},
            order_value_usd=100.0,
        )
    assert err is None


def test_execution_summary_reports_recorded_events(tmp_path: Path) -> None:
    _write_env(tmp_path, "MAX_TRADES_PER_DAY=1")
    wrapper: Any = GuardrailWrapper(cast(Any, _FakeAuth()), skill_dir=tmp_path)
    wrapper._trades_today = lambda: 1
    wrapper._get_account_balances = lambda _token: (1000.0, {"AAPL": 0.0})

    _ = wrapper._check_guardrails(
        ticker="AAPL",
        quantity=1,
        order={"orderLegCollection": [{"instruction": "BUY"}]},
        order_value_usd=100.0,
    )

    summary = get_execution_safety_summary(skill_dir=tmp_path, days=1)
    assert summary["events"].get("guardrail_block_max_trades", 0) >= 1
