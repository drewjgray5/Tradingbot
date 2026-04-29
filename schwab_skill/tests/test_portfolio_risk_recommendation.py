from __future__ import annotations

from unittest.mock import patch

from webapp._shared import build_portfolio_risk_analytics


def _summary(positions: list[dict[str, float | str]]) -> dict[str, object]:
    total = round(sum(float(p["market_value"]) for p in positions), 2)
    return {
        "positions": positions,
        "total_market_value": total,
    }


def test_risk_recommendation_flags_single_position_concentration() -> None:
    summary = _summary(
        [
            {"symbol": "AAPL", "market_value": 30000.0, "day_pl": -120.0, "pl_pct": 8.2},
            {"symbol": "MSFT", "market_value": 12000.0, "day_pl": 25.0, "pl_pct": 4.1},
            {"symbol": "XOM", "market_value": 8000.0, "day_pl": 12.0, "pl_pct": 1.8},
        ]
    )
    with patch("sector_strength.get_ticker_sector_etf", side_effect=["XLK", "XLK", "XLE"]):
        out = build_portfolio_risk_analytics(summary, skill_dir=".")
    rec = out["recommendation"]
    assert rec["headline"] == "Reduce single-position concentration"
    assert rec["priority"] == "high"


def test_risk_recommendation_flags_top5_concentration() -> None:
    summary = _summary(
        [
            {"symbol": "A", "market_value": 12000.0, "day_pl": -40.0, "pl_pct": 2.0},
            {"symbol": "B", "market_value": 11000.0, "day_pl": 12.0, "pl_pct": 1.2},
            {"symbol": "C", "market_value": 10000.0, "day_pl": 18.0, "pl_pct": 1.6},
            {"symbol": "D", "market_value": 9000.0, "day_pl": -9.0, "pl_pct": 0.8},
            {"symbol": "E", "market_value": 8000.0, "day_pl": 4.0, "pl_pct": 1.3},
            {"symbol": "F", "market_value": 7000.0, "day_pl": -6.0, "pl_pct": 0.7},
            {"symbol": "G", "market_value": 6000.0, "day_pl": 5.0, "pl_pct": 0.9},
        ]
    )
    with patch("sector_strength.get_ticker_sector_etf", side_effect=["XLK", "XLV", "XLF", "XLI", "XLY", "XLC", "XLB"]):
        out = build_portfolio_risk_analytics(summary, skill_dir=".")
    rec = out["recommendation"]
    assert rec["headline"] == "Broaden exposure beyond top holdings"
    assert rec["priority"] == "medium"


def test_risk_recommendation_flags_sector_diversification_gap() -> None:
    summary = _summary(
        [
            {"symbol": "AAPL", "market_value": 10000.0, "day_pl": -6.0, "pl_pct": 1.1},
            {"symbol": "MSFT", "market_value": 10000.0, "day_pl": 7.0, "pl_pct": 0.9},
            {"symbol": "NVDA", "market_value": 10000.0, "day_pl": -5.0, "pl_pct": 1.6},
            {"symbol": "ADBE", "market_value": 10000.0, "day_pl": 3.0, "pl_pct": 0.6},
            {"symbol": "JPM", "market_value": 10000.0, "day_pl": 2.0, "pl_pct": 0.4},
            {"symbol": "XOM", "market_value": 10000.0, "day_pl": -3.0, "pl_pct": 0.5},
            {"symbol": "JNJ", "market_value": 10000.0, "day_pl": 2.0, "pl_pct": 0.4},
            {"symbol": "CAT", "market_value": 10000.0, "day_pl": 1.0, "pl_pct": 0.3},
            {"symbol": "NEE", "market_value": 10000.0, "day_pl": -2.0, "pl_pct": 0.5},
            {"symbol": "AMT", "market_value": 10000.0, "day_pl": 1.0, "pl_pct": 0.4},
        ]
    )
    with patch("sector_strength.get_ticker_sector_etf", side_effect=["XLK", "XLK", "XLK", "XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLRE"]):
        out = build_portfolio_risk_analytics(summary, skill_dir=".")
    rec = out["recommendation"]
    assert rec["headline"] == "Improve sector diversification"
    assert rec["priority"] == "medium"


def test_risk_recommendation_returns_starter_guidance_for_empty_portfolio() -> None:
    out = build_portfolio_risk_analytics({"positions": [], "total_market_value": 0}, skill_dir=".")
    rec = out["recommendation"]
    assert rec["headline"] == "Build a diversified starter allocation"
    assert rec["priority"] == "low"
