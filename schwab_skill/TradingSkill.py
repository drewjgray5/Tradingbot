"""
TradingSkill - OpenClaw-integrated trading tools.

Imports from market_data, stage_analysis, and execution. Exposes three
@tool-decorated functions for the OpenClaw LLM. On guardrail block,
execute_trade returns the exact plain-text error for the agent to pass
to the user.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from execution import get_account_status, place_order
from market_data import get_current_quote, get_daily_history
from schwab_auth import DualSchwabAuth
from stage_analysis import add_indicators, check_vcp_volume, is_stage_2

SKILL_DIR = Path(__file__).resolve().parent


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """OpenClaw-compatible @tool decorator."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func.__tool_name__ = name
        func.__tool_description__ = description
        func.__tool_parameters__ = parameters or {}
        return func

    return decorator


@tool(
    name="analyze_ticker_trend",
    description="""Analyzes a stock ticker's price and volume to identify high-probability breakout setups (Stage 2 + VCP volume dry-up).

Use when the user asks to analyze a stock, check if it's in an uptrend, evaluate a breakout setup, or screen for Stage 2 conditions.

REQUIRED: ticker (str) - Stock symbol, e.g. 'AAPL', 'MSFT', 'SPY'.
OPTIONAL: days (int, default 300) - Days of history to fetch. At least 252 recommended for 52-week analysis.

Returns JSON with is_stage_2, vcp_volume_ok, current_price, SMAs, and a human-readable summary. Uses Market Session for OHLCV data.""",
    parameters={
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Stock symbol"},
            "days": {"type": "integer", "description": "Days of history", "default": 300},
        },
        "required": ["ticker"],
    },
)
def analyze_ticker_trend(ticker: str, days: int = 300) -> str:
    """Analyze ticker for Stage 2 and VCP volume conditions."""
    try:
        auth = DualSchwabAuth(skill_dir=SKILL_DIR)
        df = get_daily_history(ticker, days=days, auth=auth, skill_dir=SKILL_DIR)
        if df.empty:
            return json.dumps({"error": f"No data for {ticker}", "ticker": ticker.upper()}, indent=2)

        df = add_indicators(df)
        latest = df.iloc[-1]
        stage2 = is_stage_2(df)
        vcp_ok = check_vcp_volume(df)
        quote = get_current_quote(ticker, auth=auth, skill_dir=SKILL_DIR)
        current = float(latest["close"])
        if isinstance(quote, dict) and quote.get("lastPrice") is not None:
            try:
                current = float(quote["lastPrice"])
            except (TypeError, ValueError):
                pass

        # Sector strength (winning sectors only for trading)
        sector_etf = None
        in_winning_sector = None
        try:
            from sector_strength import get_ticker_sector_etf, get_winning_sector_etfs
            sector_etf = get_ticker_sector_etf(ticker.upper())
            winning = get_winning_sector_etfs(auth, SKILL_DIR)
            in_winning_sector = sector_etf in winning if sector_etf else None
        except Exception:
            pass

        summary = f"{ticker.upper()}: Stage 2={stage2}, VCP volume OK={vcp_ok}. "
        if sector_etf:
            summary += f"Sector {sector_etf} "
            if in_winning_sector is True:
                summary += "(winning). "
            elif in_winning_sector is False:
                summary += "(underperforming). "
        summary += "Breakout setup detected." if (stage2 and vcp_ok and in_winning_sector) else "Conditions not met."
        result = {
            "ticker": ticker.upper(),
            "is_stage_2": stage2,
            "vcp_volume_ok": vcp_ok,
            "sector_etf": sector_etf,
            "in_winning_sector": in_winning_sector,
            "current_price": round(current, 2),
            "sma_50": round(float(latest.get("sma_50", 0)), 2),
            "sma_150": round(float(latest.get("sma_150", 0)), 2),
            "sma_200": round(float(latest.get("sma_200", 0)), 2),
            "summary": summary,
        }
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "ticker": ticker.upper()}, indent=2)


@tool(
    name="get_account_status",
    description="""Retrieves Schwab brokerage account status (balances, positions, account IDs).

Use when the user asks about their account, portfolio, or balances.

No parameters. Returns JSON with accounts list and account_ids. Uses Account Session only.""",
    parameters={"type": "object", "properties": {}, "required": []},
)
def get_account_status_tool() -> str:
    """Fetch Schwab account status."""
    try:
        result = get_account_status(skill_dir=SKILL_DIR)
        if isinstance(result, str):
            return json.dumps({"error": result}, indent=2)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@tool(
    name="execute_trade",
    description="""Places an equity order via Schwab, subject to guardrails. Uses Account Session only.

Use ONLY when the user explicitly requests to buy or sell. All orders pass through the Guardrail Wrapper. If blocked (defaults: max $500k total account, $50k/ticker, 20 trades/day—overridable via .env), returns the exact plain-text error. The LLM MUST pass this to the user and log the restriction.

REQUIRED: ticker (str), qty (int), side ('BUY'|'SELL'), order_type ('MARKET'|'LIMIT').
OPTIONAL: limit_price (float) - required for LIMIT orders. BUY orders get a 7%% trailing stop attached. On fill, sends Discord success alert. On guardrail block, sends error alert.""",
    parameters={
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "qty": {"type": "integer"},
            "side": {"type": "string", "enum": ["BUY", "SELL"]},
            "order_type": {"type": "string", "enum": ["MARKET", "LIMIT"]},
            "limit_price": {"type": "number"},
        },
        "required": ["ticker", "qty", "side", "order_type"],
    },
)
def execute_trade_tool(
    ticker: str,
    qty: int,
    side: str,
    order_type: str,
    limit_price: float | None = None,
) -> str:
    """Place order. Returns plain-text error on guardrail block."""
    result = place_order(ticker, qty, side, order_type, limit_price, skill_dir=SKILL_DIR)
    if isinstance(result, str):
        return result
    return json.dumps(result, indent=2, default=str)


class TradingSkill:
    """Orchestrator for OpenClaw tool discovery."""

    @staticmethod
    def analyze_ticker_trend(ticker: str, days: int = 300) -> str:
        return analyze_ticker_trend(ticker, days)

    @staticmethod
    def get_account_status() -> str:
        return get_account_status_tool()

    @staticmethod
    def execute_trade(
        ticker: str,
        qty: int,
        side: str,
        order_type: str,
        limit_price: float | None = None,
    ) -> str:
        return execute_trade_tool(ticker, qty, side, order_type, limit_price)

    @staticmethod
    def get_tools() -> list[dict[str, Any]]:
        """Return tool definitions for framework discovery."""
        def _run_analyze(p: dict) -> str:
            return analyze_ticker_trend(p["ticker"], p.get("days", 300))

        def _run_account(_p: dict) -> str:
            return get_account_status_tool()

        def _run_trade(p: dict) -> str:
            return execute_trade_tool(
                p["ticker"], int(p["qty"]), p["side"].upper(), p["order_type"].upper(),
                p.get("limit_price"),
            )

        return [
            {"name": analyze_ticker_trend.__tool_name__, "description": analyze_ticker_trend.__tool_description__, "parameters": analyze_ticker_trend.__tool_parameters__, "execute": _run_analyze},
            {"name": get_account_status_tool.__tool_name__, "description": get_account_status_tool.__tool_description__, "parameters": get_account_status_tool.__tool_parameters__, "execute": _run_account},
            {"name": execute_trade_tool.__tool_name__, "description": execute_trade_tool.__tool_description__, "parameters": execute_trade_tool.__tool_parameters__, "execute": _run_trade},
        ]
