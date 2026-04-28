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


def build_portfolio_risk_analytics(summary: dict[str, Any], *, skill_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Compute concentration + sector/day-PnL analytics from portfolio summary."""
    from sector_strength import SECTOR_TO_ETF, get_ticker_sector_etf

    positions = summary.get("positions", []) if isinstance(summary, dict) else []
    total_value = float(summary.get("total_market_value", 0) or 0) if isinstance(summary, dict) else 0.0
    if not positions or total_value <= 0:
        return {
            "total_value": 0,
            "position_count": 0,
            "sector_allocation": [],
            "concentration": {},
            "positions_weighted": [],
            "day_pl_total": 0,
            "day_pl_breakdown": [],
            "recommendation": {
                "headline": "Build a diversified starter allocation",
                "reason": "No open positions are currently available for risk analysis.",
                "suggested_action": "When adding positions, target 3-5 sectors and keep each position below 20% of portfolio value.",
                "priority": "low",
            },
        }

    sector_buckets: dict[str, float] = {}
    etf_reverse: dict[str, str] = {}
    for name, etf in SECTOR_TO_ETF.items():
        etf_reverse.setdefault(etf, name.title())

    weighted_positions: list[dict[str, Any]] = []
    day_pl_total = 0.0
    day_pl_breakdown: list[dict[str, Any]] = []

    for pos in positions:
        sym = str(pos.get("symbol") or "")
        mkt = float(pos.get("market_value", 0) or 0)
        weight = round((mkt / total_value) * 100, 2) if total_value > 0 else 0
        day_pl = float(pos.get("day_pl", 0) or 0)
        day_pl_total += day_pl

        sector_etf = get_ticker_sector_etf(sym, skill_dir=skill_dir)
        sector_name = etf_reverse.get(sector_etf, "Unknown") if sector_etf else "Unknown"
        sector_buckets[sector_name] = sector_buckets.get(sector_name, 0) + mkt

        weighted_positions.append(
            {
                "symbol": sym,
                "weight_pct": weight,
                "market_value": mkt,
                "sector": sector_name,
                "sector_etf": sector_etf,
                "pl_pct": pos.get("pl_pct", 0),
                "day_pl": day_pl,
            }
        )
        day_pl_breakdown.append(
            {
                "symbol": sym,
                "day_pl": round(day_pl, 2),
                "contribution_pct": round((day_pl / total_value) * 100, 4) if total_value > 0 else 0,
            }
        )

    sector_allocation = sorted(
        [
            {
                "sector": name,
                "value": round(val, 2),
                "weight_pct": round((val / total_value) * 100, 2),
            }
            for name, val in sector_buckets.items()
        ],
        key=lambda x: x["weight_pct"],
        reverse=True,
    )

    weights = [float(p.get("weight_pct", 0) or 0) for p in weighted_positions]
    hhi = round(sum(w**2 for w in weights), 2)
    top1 = max(weights) if weights else 0
    top5_weight = round(sum(sorted(weights, reverse=True)[:5]), 2)
    sector_count = len([s for s in sector_allocation if float(s.get("weight_pct", 0) or 0) > 0])

    concentration = {
        "hhi": hhi,
        "hhi_label": "Concentrated" if hhi > 2500 else ("Moderate" if hhi > 1500 else "Diversified"),
        "top_position_pct": top1,
        "top_5_pct": top5_weight,
        "sector_count": sector_count,
        "position_count": len(positions),
    }

    day_pl_breakdown.sort(key=lambda x: abs(float(x.get("day_pl", 0) or 0)), reverse=True)
    recommendation = _build_portfolio_recommendation(
        concentration=concentration,
        sector_allocation=sector_allocation,
        positions_weighted=weighted_positions,
        day_pl_breakdown=day_pl_breakdown,
    )
    return {
        "total_value": round(total_value, 2),
        "position_count": len(positions),
        "sector_allocation": sector_allocation,
        "concentration": concentration,
        "positions_weighted": weighted_positions,
        "day_pl_total": round(day_pl_total, 2),
        "day_pl_breakdown": day_pl_breakdown[:10],
        "recommendation": recommendation,
    }


def _build_portfolio_recommendation(
    *,
    concentration: dict[str, Any],
    sector_allocation: list[dict[str, Any]],
    positions_weighted: list[dict[str, Any]],
    day_pl_breakdown: list[dict[str, Any]],
) -> dict[str, str]:
    """Return one high-level portfolio recommendation from risk metrics."""
    top_position_pct = float(concentration.get("top_position_pct", 0) or 0)
    top_5_pct = float(concentration.get("top_5_pct", 0) or 0)
    sector_count = int(concentration.get("sector_count", 0) or 0)
    largest_sector = sector_allocation[0] if sector_allocation else {}
    largest_sector_name = str(largest_sector.get("sector") or "Unknown")
    largest_sector_weight = float(largest_sector.get("weight_pct", 0) or 0)

    largest_position = (
        max(positions_weighted, key=lambda row: float(row.get("weight_pct", 0) or 0))
        if positions_weighted
        else {}
    )
    largest_position_symbol = str(largest_position.get("symbol") or "largest holding")

    if top_position_pct >= 25:
        return {
            "headline": "Reduce single-position concentration",
            "reason": f"{largest_position_symbol} represents {top_position_pct:.2f}% of portfolio value, which raises idiosyncratic drawdown risk.",
            "suggested_action": f"Trim or hedge {largest_position_symbol} and redeploy exposure across additional uncorrelated names.",
            "priority": "high",
        }

    if top_5_pct >= 60:
        return {
            "headline": "Broaden exposure beyond top holdings",
            "reason": f"The top 5 holdings represent {top_5_pct:.2f}% of total portfolio value.",
            "suggested_action": "Add smaller positions outside the current top 5 names to reduce concentration shocks.",
            "priority": "medium",
        }

    if largest_sector_weight >= 35 or (sector_count > 0 and sector_count < 4):
        return {
            "headline": "Improve sector diversification",
            "reason": f"{largest_sector_name} is {largest_sector_weight:.2f}% of portfolio exposure across {sector_count} sectors.",
            "suggested_action": "Rebalance incremental capital toward underrepresented sectors to smooth regime-specific volatility.",
            "priority": "medium",
        }

    biggest_mover = day_pl_breakdown[0] if day_pl_breakdown else {}
    biggest_mover_day_pl = float(biggest_mover.get("day_pl", 0) or 0)
    biggest_mover_contrib = abs(float(biggest_mover.get("contribution_pct", 0) or 0))
    biggest_mover_symbol = str(biggest_mover.get("symbol") or "a single name")
    if biggest_mover_day_pl < 0 and biggest_mover_contrib >= 0.75:
        return {
            "headline": "Limit single-name downside contribution",
            "reason": f"{biggest_mover_symbol} is driving {biggest_mover_contrib:.2f}% of portfolio value in daily downside.",
            "suggested_action": "Use tighter position-size/risk limits for this name and offset with lower-correlation exposure.",
            "priority": "medium",
        }

    return {
        "headline": "Maintain balance and rebalance on schedule",
        "reason": "Current concentration and sector mix appear broadly balanced.",
        "suggested_action": "Keep periodic rebalancing rules in place and cap new positions near current portfolio risk limits.",
        "priority": "low",
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
