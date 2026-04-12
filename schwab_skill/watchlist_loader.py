"""
Dynamic watchlist loader: S&P 500 + S&P 400 + S&P 600 + Russell 2000 from Wikipedia/GitHub.
All sectors: large cap, mid cap (~$1–5B), small cap (~$400M–$2B). Cached 24h.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
CACHE_FILE = SKILL_DIR / ".watchlist_cache.json"
CACHE_HOURS = 24


def _utc_calendar_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
LIQUID_ETF_HINTS = {"SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLC", "XLRE"}


def _fetch_sp500() -> list[str]:
    """Fetch S&P 500 tickers from Wikipedia."""
    import pandas as pd
    import requests

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "TradingBot/1.0 (https://github.com/)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    from io import StringIO
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0]
    if "Symbol" in df.columns:
        return [str(s).strip().upper() for s in df["Symbol"].dropna() if len(str(s)) <= 6]
    return []


def _fetch_sp400() -> list[str]:
    """Fetch S&P 400 mid-cap tickers from Wikipedia."""
    import pandas as pd
    import requests

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"
    headers = {"User-Agent": "TradingBot/1.0 (https://github.com/)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    from io import StringIO
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0]
    if "Symbol" in df.columns:
        return [str(s).strip().upper() for s in df["Symbol"].dropna() if len(str(s)) <= 6]
    return []


def _fetch_sp600() -> list[str]:
    """Fetch S&P 600 small-cap tickers from Wikipedia (~$400M-$2B market cap)."""
    import pandas as pd
    import requests

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"
    headers = {"User-Agent": "TradingBot/1.0 (https://github.com/)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    from io import StringIO
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0]
    if "Symbol" in df.columns:
        return [str(s).strip().upper() for s in df["Symbol"].dropna() if len(str(s)) <= 6]
    return []


def _fetch_russell2000() -> list[str]:
    """Fetch Russell 2000 tickers from GitHub CSV (small-cap index)."""
    import csv

    import requests

    url = "https://raw.githubusercontent.com/ikoniaris/Russell2000/master/russell_2000_components.csv"
    headers = {"User-Agent": "TradingBot/1.0 (https://github.com/)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    reader = csv.DictReader(resp.text.strip().splitlines())
    tickers = []
    for row in reader:
        sym = (row.get("Ticker") or row.get("ticker") or "").strip().upper()
        if sym and len(sym) <= 6:
            tickers.append(sym)
    return tickers


def _load_cached() -> tuple[list[str], float, str | None] | None:
    """Load cached watchlist. Returns (tickers, timestamp, as_of_utc_date or None) or None."""
    if not CACHE_FILE.exists():
        return None
    try:
        import json

        data = json.loads(CACHE_FILE.read_text())
        tickers = data.get("tickers", [])
        ts = data.get("timestamp", 0)
        if not tickers or not ts:
            return None
        as_of = data.get("as_of_utc_date")
        as_of_s = as_of.strip() if isinstance(as_of, str) else None
        return tickers, float(ts), as_of_s or None
    except Exception as e:
        LOG.warning("Watchlist cache read failed: %s", e)
    return None


def _save_cache(tickers: list[str]) -> None:
    """Save watchlist to cache (MiroFish uses separate .mirofish_cache.json)."""
    try:
        import json

        data = {
            "tickers": tickers,
            "timestamp": time.time(),
            "as_of_utc_date": _utc_calendar_date(),
        }
        CACHE_FILE.write_text(json.dumps(data, indent=0))
    except Exception as e:
        LOG.warning("Watchlist cache write failed: %s", e)


def load_full_watchlist(force_refresh: bool = False) -> list[str]:
    """
    Load S&P 500 + S&P 400 + S&P 600 + Russell 2000 watchlist.
    Uses cache for the same UTC calendar day (daily refresh), or if the cache file
    predates as_of_utc_date, falls back to the prior <24h timestamp rule.
    Returns deduplicated list of tickers, all sectors.
    """
    if not force_refresh:
        cached = _load_cached()
        if cached:
            tickers, ts, as_of = cached
            today = _utc_calendar_date()
            cache_fresh_for_day = as_of == today
            legacy_fresh = as_of is None and (time.time() - ts) < CACHE_HOURS * 3600
            if cache_fresh_for_day or legacy_fresh:
                LOG.debug("Using cached watchlist (%d tickers)", len(tickers))
                return tickers

    LOG.info("Fetching S&P 500 + S&P 400 + S&P 600 + Russell 2000...")
    try:
        sp500 = _fetch_sp500()
        sp400 = _fetch_sp400()
        sp600 = _fetch_sp600()
        russell2000 = _fetch_russell2000()
    except Exception as e:
        LOG.warning("Watchlist fetch failed: %s. Using fallback.", e)
        return _fallback_watchlist()

    extra = ["IWM"]  # Russell 2000 ETF - small cap basket
    combined = list(dict.fromkeys(sp500 + sp400 + sp600 + russell2000 + extra))
    if combined:
        _save_cache(combined)
        LOG.info("Loaded %d tickers (S&P 500 + 400 + 600 + Russell 2000)", len(combined))
    return combined or _fallback_watchlist()


def _fallback_watchlist() -> list[str]:
    """Minimal fallback if fetch fails."""
    return [
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "JPM", "V", "UNH",
        "XOM", "JNJ", "WMT", "PG", "HD", "DIS", "BAC", "KO", "PEP",
    ]


def prefilter_watchlist(
    tickers: list[str],
    max_tickers: int = 800,
    include_etf_hints: bool = True,
) -> list[str]:
    """
    Deterministic quality prefilter to reduce noisy universe size.
    Keeps plain symbols first, optionally preserving a small set of liquid ETF hints.
    """
    cleaned = [t.strip().upper() for t in tickers if t and t.strip()]
    deduped = list(dict.fromkeys(cleaned))

    plain = [t for t in deduped if t.isalpha() and 1 <= len(t) <= 5]
    extras = []
    if include_etf_hints:
        extras = [t for t in deduped if t in LIQUID_ETF_HINTS and t not in plain]

    merged = plain + extras
    if max_tickers > 0:
        merged = merged[:max_tickers]
    return merged
