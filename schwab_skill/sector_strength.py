"""
Sector strength filter: only trade in sectors outperforming SPY (current market climate).

Uses SPDR sector ETFs vs SPY over a configurable lookback. Winning sectors = those
outperforming the market. Ticker sector resolved via yfinance (fallback: block unknown).
"""

from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path
from typing import Any

# Sector name (from yfinance) -> sector ETF
SECTOR_TO_ETF: dict[str, str] = {
    "technology": "XLK",
    "financial services": "XLF",
    "energy": "XLE",
    "healthcare": "XLV",
    "consumer cyclical": "XLY",  # Consumer Discretionary
    "consumer defensives": "XLP",  # Consumer Staples
    "consumer staples": "XLP",
    "basic materials": "XLB",
    "materials": "XLB",
    "industrials": "XLI",
    "utilities": "XLU",
    "communication services": "XLC",
    "real estate": "XLRE",
}

SECTOR_ETFS = list(set(SECTOR_TO_ETF.values()))
LOOKBACK_DAYS = 21  # ~1 month
LOG = logging.getLogger(__name__)
SECTOR_CACHE_FILE = ".sector_map_cache.json"


def _sector_cache_path(skill_dir: Path | None = None) -> Path:
    return (skill_dir or Path(__file__).resolve().parent) / SECTOR_CACHE_FILE


def _load_sector_cache(skill_dir: Path | None = None) -> dict[str, Any]:
    path = _sector_cache_path(skill_dir)
    if not path.exists():
        return {"sector_etf_by_ticker": {}, "unresolved": []}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            data.setdefault("sector_etf_by_ticker", {})
            data.setdefault("unresolved", [])
            return data
    except Exception:
        pass
    return {"sector_etf_by_ticker": {}, "unresolved": []}


def _save_sector_cache(data: dict[str, Any], skill_dir: Path | None = None) -> None:
    path = _sector_cache_path(skill_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _cache_sector_mapping(ticker: str, sector_etf: str | None, skill_dir: Path | None = None) -> None:
    data = _load_sector_cache(skill_dir)
    mapping = data.setdefault("sector_etf_by_ticker", {})
    unresolved = set(data.setdefault("unresolved", []))
    t = ticker.upper().strip()
    if sector_etf:
        mapping[t] = sector_etf
        unresolved.discard(t)
    else:
        mapping[t] = None
        unresolved.add(t)
    data["unresolved"] = sorted(unresolved)
    _save_sector_cache(data, skill_dir)


def get_unresolved_sector_symbols(skill_dir: Path | None = None) -> list[str]:
    data = _load_sector_cache(skill_dir)
    unresolved = data.get("unresolved", [])
    return sorted([s for s in unresolved if isinstance(s, str)])


def _fetch_perf_yfinance(symbol: str, days: int) -> float | None:
    """Fallback: get return via yfinance when Schwab fails."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        df = t.history(period=f"{max(days, 30)}d", auto_adjust=True)
        if df.empty or len(df) < 2:
            return None
        df = df.tail(days + 1)
        if len(df) < 2:
            return None
        start_px = float(df["Close"].iloc[0])
        end_px = float(df["Close"].iloc[-1])
        if start_px <= 0:
            return None
        return (end_px - start_px) / start_px
    except Exception as e:
        LOG.debug("yfinance fallback for %s: %s", symbol, e)
        return None


def _get_sector_performance(
    auth: Any,
    skill_dir: Path,
) -> dict[str, float]:
    """Fetch return (0-1) for each sector ETF and SPY over lookback. Falls back to yfinance if Schwab fails."""
    import time

    from market_data import get_daily_history

    performance: dict[str, float] = {}
    symbols = SECTOR_ETFS + ["SPY"]
    days = LOOKBACK_DAYS + 5

    for sym in symbols:
        time.sleep(0.15)  # Avoid rate limits
        try:
            df = get_daily_history(sym, days=days, auth=auth, skill_dir=skill_dir)
            if df.empty or len(df) < 2:
                ret = _fetch_perf_yfinance(sym, LOOKBACK_DAYS)
                if ret is not None:
                    performance[sym] = ret
                continue
            df = df.tail(LOOKBACK_DAYS + 1)
            if len(df) < 2:
                continue
            start_px = float(df["close"].iloc[0])
            end_px = float(df["close"].iloc[-1])
            if start_px <= 0:
                continue
            performance[sym] = (end_px - start_px) / start_px
        except Exception as e:
            LOG.warning("Failed to get performance for %s: %s", sym, e)
            ret = _fetch_perf_yfinance(sym, LOOKBACK_DAYS)
            if ret is not None:
                performance[sym] = ret

    return performance


def get_winning_sector_etfs(
    auth: Any,
    skill_dir: Path,
) -> set[str]:
    """Return set of sector ETF symbols that outperformed SPY."""
    from notifier import send_alert

    perf = _get_sector_performance(auth, skill_dir)
    spy_ret = perf.get("SPY")
    if spy_ret is None:
        # Full yfinance fallback when Schwab returns nothing
        LOG.info("Schwab sector data missing, trying yfinance fallback")
        for sym in SECTOR_ETFS + ["SPY"]:
            if sym not in perf:
                ret = _fetch_perf_yfinance(sym, LOOKBACK_DAYS)
                if ret is not None:
                    perf[sym] = ret
        spy_ret = perf.get("SPY")
    if spy_ret is None:
        send_alert(
            "Sector filter DATA FAILURE: Could not fetch SPY/sector performance (Schwab or yfinance). "
            "Allowing all sectors.",
            kind="data_failure",
            env_path=skill_dir / ".env",
        )
        return set(SECTOR_ETFS)  # Can't determine - allow all
    failed = [s for s in (SECTOR_ETFS + ["SPY"]) if s not in perf]
    if failed:
        send_alert(
            f"Sector filter: Failed to fetch data for: {', '.join(failed[:5])}. "
            "Using available data.",
            kind="sector_filter_fallback",
            env_path=skill_dir / ".env",
        )
    winning = {etf for etf in SECTOR_ETFS if perf.get(etf, -1) > spy_ret}
    return winning


def get_ticker_sector_etf(ticker: str, skill_dir: Path | None = None) -> str | None:
    """Resolve ticker to sector ETF (e.g. AAPL -> XLK). Returns None if unknown."""
    tkr = ticker.upper().strip()
    cache = _load_sector_cache(skill_dir)
    mapping = cache.get("sector_etf_by_ticker", {})
    if tkr in mapping:
        val = mapping.get(tkr)
        return val if isinstance(val, str) and val else None
    try:
        import yfinance as yf
        t = yf.Ticker(tkr)
        info = t.info
        sector = (info.get("sector") or "").strip().lower()
        if not sector:
            _cache_sector_mapping(tkr, None, skill_dir)
            return None
        sector_etf = SECTOR_TO_ETF.get(sector)
        _cache_sector_mapping(tkr, sector_etf, skill_dir)
        return sector_etf
    except Exception:
        _cache_sector_mapping(tkr, None, skill_dir)
        return None


def get_sector_heatmap(
    auth: Any,
    skill_dir: Path,
) -> dict[str, Any]:
    """
    Build a sector heatmap: each sector ETF's return vs SPY over lookback period.
    Returns dict with 'rows' (list of dicts), 'spy_return', 'winning_count', 'total'.
    """
    perf = _get_sector_performance(auth, skill_dir)
    spy_ret = perf.get("SPY")

    if spy_ret is None:
        for sym in SECTOR_ETFS + ["SPY"]:
            if sym not in perf:
                ret = _fetch_perf_yfinance(sym, LOOKBACK_DAYS)
                if ret is not None:
                    perf[sym] = ret
        spy_ret = perf.get("SPY", 0.0)

    etf_names = {
        "XLK": "Technology", "XLF": "Financials", "XLE": "Energy",
        "XLV": "Healthcare", "XLY": "Consumer Disc", "XLP": "Consumer Staples",
        "XLB": "Materials", "XLI": "Industrials", "XLU": "Utilities",
        "XLC": "Communication", "XLRE": "Real Estate",
    }

    rows = []
    for etf in sorted(set(SECTOR_ETFS)):
        ret = perf.get(etf)
        if ret is None:
            continue
        winning = ret > (spy_ret or 0)
        rows.append({
            "etf": etf,
            "name": etf_names.get(etf, etf),
            "return_pct": round(ret * 100, 2),
            "vs_spy": round((ret - (spy_ret or 0)) * 100, 2),
            "winning": winning,
        })

    rows.sort(key=lambda r: r["return_pct"], reverse=True)
    winning_count = sum(1 for r in rows if r["winning"])

    return {
        "rows": rows,
        "spy_return": round((spy_ret or 0) * 100, 2),
        "winning_count": winning_count,
        "total": len(rows),
    }


def is_market_regime_bullish(auth: Any, skill_dir: Path) -> tuple[bool, dict[str, Any]]:
    """
    Hard binary regime gate: SPY must be above its 200 SMA.
    Not tunable by design — avoids overfitting.
    Returns (is_bullish, context_dict).
    """
    from market_data import get_daily_history
    from stage_analysis import add_indicators

    ctx: dict[str, Any] = {"spy_price": None, "spy_sma_200": None, "bullish": False}
    try:
        df = get_daily_history("SPY", days=220, auth=auth, skill_dir=skill_dir)
        if df.empty or len(df) < 200:
            yf_df = None
            try:
                import yfinance as yf
                yf_df = yf.Ticker("SPY").history(period="1y", auto_adjust=True)
                if yf_df is not None and not yf_df.empty:
                    df = yf_df.rename(columns={
                        "Open": "open", "High": "high", "Low": "low",
                        "Close": "close", "Volume": "volume",
                    })
            except Exception:
                pass
        if df.empty or len(df) < 200:
            LOG.warning("Regime check: insufficient SPY data (%d bars), defaulting to bullish", len(df))
            ctx["bullish"] = True
            return True, ctx
        df = add_indicators(df)
        price = float(df["close"].iloc[-1])
        sma_200 = float(df["sma_200"].iloc[-1])
        ctx["spy_price"] = round(price, 2)
        ctx["spy_sma_200"] = round(sma_200, 2)
        ctx["bullish"] = price > sma_200
        return ctx["bullish"], ctx
    except Exception as e:
        LOG.warning("Regime check failed (%s), defaulting to bullish", e)
        ctx["bullish"] = True
        return True, ctx


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_regime_v2_score_from_inputs(
    *,
    spy_above_200: bool,
    spy_50_above_200: bool,
    spy_50_slope_up: bool,
    vix_value: float | None,
    breadth_ratio: float | None,
    sector_dispersion_pct: float | None,
) -> dict[str, Any]:
    """
    Deterministic composite regime score (0..100) and bucket.
    Components:
    - SPY trend
    - VIX state
    - Breadth proxy
    - Sector leadership dispersion
    """
    trend_score = 0.0
    trend_score += 60.0 if spy_above_200 else 20.0
    trend_score += 20.0 if spy_50_above_200 else 0.0
    trend_score += 20.0 if spy_50_slope_up else 0.0

    if vix_value is None:
        vix_score = 60.0
    elif vix_value <= 15:
        vix_score = 100.0
    elif vix_value <= 20:
        vix_score = 80.0
    elif vix_value <= 25:
        vix_score = 55.0
    elif vix_value <= 30:
        vix_score = 30.0
    else:
        vix_score = 10.0

    if breadth_ratio is None:
        breadth_score = 55.0
    else:
        breadth_score = 100.0 * _clamp01(float(breadth_ratio))

    if sector_dispersion_pct is None:
        dispersion_score = 55.0
    elif sector_dispersion_pct <= 2.0:
        dispersion_score = 90.0
    elif sector_dispersion_pct <= 4.0:
        dispersion_score = 75.0
    elif sector_dispersion_pct <= 6.0:
        dispersion_score = 55.0
    elif sector_dispersion_pct <= 8.0:
        dispersion_score = 35.0
    else:
        dispersion_score = 20.0

    score = (
        (0.35 * trend_score)
        + (0.20 * vix_score)
        + (0.25 * breadth_score)
        + (0.20 * dispersion_score)
    )
    score = max(0.0, min(100.0, score))
    if score >= 70:
        bucket = "high"
    elif score >= 55:
        bucket = "medium"
    else:
        bucket = "low"

    return {
        "score": round(score, 2),
        "bucket": bucket,
        "components": {
            "trend": round(trend_score, 2),
            "vix": round(vix_score, 2),
            "breadth": round(breadth_score, 2),
            "dispersion": round(dispersion_score, 2),
        },
    }


def get_regime_v2_snapshot(auth: Any, skill_dir: Path) -> dict[str, Any]:
    """
    Composite regime context for diagnostics/gating/sizing.
    Falls back to neutral scores when inputs are partially unavailable.
    """
    from market_data import get_daily_history
    from stage_analysis import add_indicators

    ctx: dict[str, Any] = {
        "spy_price": None,
        "spy_sma_200": None,
        "spy_sma_50": None,
        "spy_50_slope_up": None,
        "vix": None,
        "breadth_ratio": None,
        "sector_dispersion_pct": None,
        "available": True,
    }
    try:
        df = get_daily_history("SPY", days=260, auth=auth, skill_dir=skill_dir)
        if df.empty or len(df) < 210:
            import yfinance as yf

            yf_df = yf.Ticker("SPY").history(period="1y", auto_adjust=True)
            if yf_df is not None and not yf_df.empty:
                df = yf_df.rename(
                    columns={
                        "Open": "open",
                        "High": "high",
                        "Low": "low",
                        "Close": "close",
                        "Volume": "volume",
                    }
                )
        if df.empty or len(df) < 210:
            raise ValueError("insufficient SPY bars for regime_v2")

        df = add_indicators(df)
        spy_price = float(df["close"].iloc[-1])
        sma_200 = float(df["sma_200"].iloc[-1])
        sma_50 = float(df["sma_50"].iloc[-1])
        prev_sma_50 = float(df["sma_50"].iloc[-6]) if len(df) >= 56 else float(df["sma_50"].iloc[-2])
        spy_50_slope_up = sma_50 >= prev_sma_50
        ctx["spy_price"] = round(spy_price, 2)
        ctx["spy_sma_200"] = round(sma_200, 2)
        ctx["spy_sma_50"] = round(sma_50, 2)
        ctx["spy_50_slope_up"] = bool(spy_50_slope_up)

        perf = _get_sector_performance(auth, skill_dir)
        sector_rets = [float(perf[e]) for e in SECTOR_ETFS if e in perf]
        spy_ret = perf.get("SPY")
        if sector_rets:
            dispersion = statistics.pstdev([r * 100.0 for r in sector_rets])
            ctx["sector_dispersion_pct"] = round(float(dispersion), 2)
        if spy_ret is not None and sector_rets:
            winners = sum(1 for r in sector_rets if r > float(spy_ret))
            ctx["breadth_ratio"] = round(winners / max(1, len(sector_rets)), 4)

        try:
            vix_df = get_daily_history("VIX", days=20, auth=auth, skill_dir=skill_dir)
            if vix_df.empty:
                import yfinance as yf

                vix_df = yf.Ticker("^VIX").history(period="1mo", auto_adjust=True)
                if vix_df is not None and not vix_df.empty:
                    vix_df = vix_df.rename(
                        columns={
                            "Open": "open",
                            "High": "high",
                            "Low": "low",
                            "Close": "close",
                            "Volume": "volume",
                        }
                    )
            if vix_df is not None and not vix_df.empty:
                ctx["vix"] = round(float(vix_df["close"].iloc[-1]), 2)
        except Exception:
            pass

        computed = compute_regime_v2_score_from_inputs(
            spy_above_200=spy_price > sma_200,
            spy_50_above_200=sma_50 > sma_200,
            spy_50_slope_up=bool(spy_50_slope_up),
            vix_value=ctx.get("vix"),
            breadth_ratio=ctx.get("breadth_ratio"),
            sector_dispersion_pct=ctx.get("sector_dispersion_pct"),
        )
        computed["context"] = ctx
        return computed
    except Exception as e:
        LOG.warning("Regime v2 snapshot failed (%s), using neutral fallback", e)
        ctx["available"] = False
        fallback = compute_regime_v2_score_from_inputs(
            spy_above_200=True,
            spy_50_above_200=True,
            spy_50_slope_up=True,
            vix_value=None,
            breadth_ratio=None,
            sector_dispersion_pct=None,
        )
        fallback["context"] = ctx
        return fallback


def is_ticker_in_winning_sector(
    ticker: str,
    winning_etfs: set[str],
    skill_dir: Path | None = None,
) -> tuple[bool, str]:
    """
    Check if ticker is in a winning sector.
    Returns (ok, message). ok=True means allow trade.
    """
    etf = get_ticker_sector_etf(ticker, skill_dir=skill_dir)
    if etf is None:
        return False, f"Sector filter: Could not resolve sector for {ticker}. Blocking (use sector ETFs or known tickers)."
    if etf not in winning_etfs:
        return False, f"Sector filter: {ticker} is in sector {etf} which is underperforming SPY. Only trading winning sectors."
    return True, f"{ticker} in winning sector {etf}"
