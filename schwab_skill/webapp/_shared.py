"""Shared helpers used by both `webapp.main` (local) and `webapp.tenant_dashboard`
(SaaS). Extracted to break duplication identified in the codebase audit. Keep
the surface here narrow: only pure-function helpers that take primitive
arguments. Anything that needs request/session state stays in the caller.
"""

from __future__ import annotations

import json
import os
from typing import Any

# NOTE: ``PendingTrade`` is intentionally typed as ``Any`` so this module can be
# imported without depending on the ORM models (avoids circular imports).


def trade_to_dict(row: Any) -> dict[str, Any]:
    """Serialise a `PendingTrade` ORM row to a JSON-safe dict.

    Single source of truth previously duplicated as ``_trade_to_dict`` in
    ``webapp/main.py`` and ``webapp/tenant_dashboard.py``.
    """
    return {
        "id": getattr(row, "id", None),
        "ticker": getattr(row, "ticker", None),
        "qty": getattr(row, "qty", None),
        "price": getattr(row, "price", None),
        "status": getattr(row, "status", None),
        "note": getattr(row, "note", None),
        "signal": json.loads(getattr(row, "signal_json", None) or "{}"),
        "created_at": (
            row.created_at.isoformat()
            if getattr(row, "created_at", None) is not None
            else None
        ),
        "updated_at": (
            row.updated_at.isoformat()
            if getattr(row, "updated_at", None) is not None
            else None
        ),
    }


def build_portfolio_summary(account_status: dict[str, Any]) -> dict[str, Any]:
    """Flatten a Schwab account status payload into a UI-friendly summary."""
    accounts = account_status.get("accounts", []) if isinstance(account_status, dict) else []
    positions: list[dict[str, Any]] = []
    total_value = 0.0
    for acc in accounts:
        sec = acc.get("securitiesAccount", acc) if isinstance(acc, dict) else {}
        for pos in sec.get("positions", []) if isinstance(sec, dict) else []:
            inst = pos.get("instrument", {}) if isinstance(pos, dict) else {}
            sym = inst.get("symbol", "?")
            qty = pos.get("longQuantity", 0) or pos.get("shortQuantity", 0) or 0
            if not qty:
                continue
            mkt_val = float(pos.get("marketValue", 0) or 0)
            day_pl = float(pos.get("currentDayProfitLoss", 0) or 0)
            avg_cost = float(pos.get("averagePrice", 0) or 0)
            last = (mkt_val / qty) if qty else 0.0
            pl_pct = ((last - avg_cost) / avg_cost * 100.0) if avg_cost else 0.0
            total_value += mkt_val
            positions.append(
                {
                    "symbol": sym,
                    "qty": int(qty),
                    "market_value": round(mkt_val, 2),
                    "day_pl": round(day_pl, 2),
                    "avg_cost": round(avg_cost, 4),
                    "last": round(last, 4),
                    "pl_pct": round(pl_pct, 2),
                }
            )
    positions.sort(key=lambda row: abs(float(row.get("market_value", 0))), reverse=True)
    return {
        "account_count": len(accounts),
        "positions_count": len(positions),
        "total_market_value": round(total_value, 2),
        "positions": positions,
    }


def quote_health_hint(meta: dict[str, Any], quote_ok: bool) -> str | None:
    """Translate a `get_current_quote_with_status` meta dict into a UX hint."""
    if quote_ok:
        return None
    reason = str(meta.get("reason") or "") if isinstance(meta, dict) else ""
    detail = str(meta.get("error_detail") or "") if isinstance(meta, dict) else ""
    if reason == "http_error":
        return (
            "Schwab returned an error for the market-data quotes request. "
            "Run `python healthcheck.py` and re-authenticate the market app if it keeps failing."
        )
    if reason == "no_matching_symbol_in_response":
        return (
            "The quotes response did not contain the probe symbol. "
            "Confirm the Schwab API is up and your market token has quotes access."
        )
    if reason == "last_price_not_parseable":
        return (
            "Quote JSON was received but no usable last/mark/close price was found. "
            "If this persists after a Schwab API update, extend extract_schwab_last_price in market_data.py."
        )
    if "circuit" in detail.lower() or reason == "RuntimeError":
        return (
            "Repeated connection failures may have opened the Schwab circuit breaker. "
            "Wait a minute, check network/DNS, then retry."
        )
    if reason:
        return f"Quote check failed ({reason}). See trading_bot.log for details."
    return "Quote check failed for an unknown reason. See trading_bot.log for details."


def manual_jwt_entry_enabled(default: bool) -> bool:
    """Resolve the `WEB_ALLOW_MANUAL_JWT` flag.

    The local app defaults to ``True`` (developers paste JWTs into the
    debug-only manual entry box), the SaaS app defaults to ``False`` (browser
    sign-in only). Operators can override either default explicitly.
    """
    raw = (os.getenv("WEB_ALLOW_MANUAL_JWT") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return bool(default)
