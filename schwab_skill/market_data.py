"""
Market data pipeline using Market Session (Schwab OHLCV) with yfinance fallback.

Fetches daily historical data with exponential backoff on HTTP 429.
When PREFER_SCHWAB_DATA=true (default), logs warning when yfinance fallback is used.
"""

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from circuit_breaker import maybe_trip_breaker, schwab_circuit
from schwab_auth import DualSchwabAuth

LOG = logging.getLogger(__name__)

SCHWAB_BASE = "https://api.schwabapi.com"
MAX_RETRIES = 5
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0
BACKOFF_MULTIPLIER = 2.0
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
SKILL_DIR = Path(__file__).resolve().parent


def _get_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


def _request_with_backoff(
    auth: DualSchwabAuth,
    method: str,
    url: str,
    params: dict | None = None,
    **kwargs: Any,
) -> requests.Response:
    # Prevent per-ticker thrashing when DNS/reads are failing.
    if not schwab_circuit.connection_stable:
        raise RuntimeError("Schwab connection unstable (circuit breaker)")

    token = auth.get_market_token()
    kwargs.setdefault("headers", {}).update(_get_headers(token))
    kwargs.setdefault("timeout", 30)
    backoff = INITIAL_BACKOFF
    refreshed_on_401 = False
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(method, url, params=params, **kwargs)
        except Exception as e:
            maybe_trip_breaker(e, schwab_circuit)
            raise
        if resp.status_code == 401 and not refreshed_on_401:
            if auth.market_session.force_refresh():
                refreshed_on_401 = True
                token = auth.get_market_token()
                kwargs["headers"] = dict(kwargs.get("headers", {}))
                kwargs["headers"].update(_get_headers(token))
                continue
        if resp.status_code == 429 and attempt < MAX_RETRIES - 1:
            time.sleep(backoff)
            backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
            continue
        return resp
    return resp


def _get_daily_history_yfinance(ticker: str, days: int) -> pd.DataFrame:
    """Fallback when Schwab fails (401, etc.). Returns same format as get_daily_history."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker.upper())
        period = "2y" if days > 365 else "1y"
        df = t.history(period=period, auto_adjust=True)
        if df.empty or len(df) < 2:
            return pd.DataFrame(columns=OHLCV_COLUMNS).rename_axis("date")
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
        df = df[OHLCV_COLUMNS].sort_index().drop_duplicates()
        df.index = df.index.tz_localize(None).normalize()
        df.index.name = "date"
        return df
    except Exception:
        return pd.DataFrame(columns=OHLCV_COLUMNS).rename_axis("date")


def get_daily_history(
    ticker: str,
    days: int = 300,
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
) -> pd.DataFrame:
    """
    Fetch daily OHLCV using Schwab Market Session. Falls back to yfinance on 401/errors.
    When PREFER_SCHWAB_DATA=true, logs warning when fallback is used.
    Returns DataFrame with DatetimeIndex and columns: open, high, low, close, volume.
    """
    auth = auth or DualSchwabAuth(skill_dir=skill_dir or SKILL_DIR)
    ticker = ticker.upper().strip()
    skill_dir = Path(skill_dir or SKILL_DIR)
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=days)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    url = f"{SCHWAB_BASE}/marketdata/v1/pricehistory"
    params = {
        "symbol": ticker,
        "periodType": "month",
        "frequencyType": "daily",
        "startDate": start_ms,
        "endDate": end_ms,
    }

    try:
        resp = _request_with_backoff(auth, "GET", url, params=params)
        resp.raise_for_status()
        data = resp.json()
        candles = data.get("candles")
        if not candles:
            return pd.DataFrame(columns=OHLCV_COLUMNS).rename_axis("date")

        df = pd.DataFrame(candles)
        dt_series = pd.to_datetime(df["datetime"], unit="ms", utc=True) if "datetime" in df.columns else pd.NaT
        required = ["open", "high", "low", "close", "volume"]
        for c in required:
            if c not in df.columns:
                raise ValueError(f"API missing column: {c}")
        df = df[required].copy().astype({c: float for c in required})
        df.index = pd.DatetimeIndex(dt_series).tz_localize(None).normalize()
        df.index.name = "date"
        df = df[OHLCV_COLUMNS].sort_index().drop_duplicates()
        return df
    except Exception as e:
        # If the circuit breaker is unstable, we'll very likely hit this path.
        # Keep the fallback behavior safe and non-crashing.
        try:
            from config import get_prefer_schwab_data
            if get_prefer_schwab_data(skill_dir):
                LOG.warning("Schwab data failed for %s (%s), using yfinance fallback", ticker, e)
        except ImportError:
            pass
        return _get_daily_history_yfinance(ticker, days)


def get_current_quote(
    ticker: str,
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
) -> dict | None:
    """Fetch real-time quote using Market Session."""
    auth = auth or DualSchwabAuth(skill_dir=skill_dir or SKILL_DIR)
    ticker = ticker.upper().strip()
    url = f"{SCHWAB_BASE}/marketdata/v1/quotes"
    try:
        resp = _request_with_backoff(auth, "GET", url, params={"symbols": ticker})
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and ticker in data:
            return data[ticker]
        if isinstance(data, dict) and "lastPrice" in data:
            return data
        if isinstance(data, list) and data:
            return data[0]
        return None
    except Exception:
        return None
