"""
Market data pipeline using Market Session (Schwab OHLCV) with yfinance fallback.

Fetches daily historical data with exponential backoff on HTTP 429.
When PREFER_SCHWAB_DATA=true (default), logs warning when yfinance fallback is used.
"""

import logging
import os
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
POLYGON_BASE = "https://api.polygon.io"
MAX_RETRIES = 5
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0
BACKOFF_MULTIPLIER = 2.0
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
SKILL_DIR = Path(__file__).resolve().parent


def _empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS).rename_axis("date")


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
        if resp.status_code in (502, 503, 504) and attempt < MAX_RETRIES - 1:
            time.sleep(backoff)
            backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
            continue
        return resp
    return resp


def _polygon_api_key() -> str:
    return (os.getenv("POLYGON_API_KEY") or "").strip()


def _get_polygon_quote_fallback(ticker: str) -> tuple[dict | None, dict[str, Any]]:
    key = _polygon_api_key()
    meta: dict[str, Any] = {"provider": "polygon", "reason": None, "http_status": None}
    if not key:
        meta["reason"] = "polygon_api_key_missing"
        return None, meta
    symbol = ticker.upper().strip()
    try:
        trade_url = f"{POLYGON_BASE}/v2/last/trade/{symbol}"
        resp = requests.get(trade_url, params={"apiKey": key}, timeout=8)
        meta["http_status"] = resp.status_code
        if resp.ok:
            body = resp.json()
            px = body.get("results", {}).get("p")
            if px is not None:
                return {"symbol": symbol, "lastPrice": float(px), "source": "polygon"}, meta
        prev_url = f"{POLYGON_BASE}/v2/aggs/ticker/{symbol}/prev"
        prev = requests.get(prev_url, params={"adjusted": "true", "apiKey": key}, timeout=8)
        meta["http_status"] = prev.status_code
        if prev.ok:
            body = prev.json()
            rows = body.get("results") or []
            if rows:
                close_px = rows[0].get("c")
                if close_px is not None:
                    return {"symbol": symbol, "lastPrice": float(close_px), "source": "polygon_prev_close"}, meta
        meta["reason"] = "polygon_no_price"
        return None, meta
    except Exception as exc:
        meta["reason"] = f"polygon_error:{type(exc).__name__}"
        meta["error_detail"] = str(exc)[:220]
        return None, meta


def _get_daily_history_yfinance(ticker: str, days: int) -> pd.DataFrame:
    """Fallback when Schwab fails (401, etc.). Returns same format as get_daily_history."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker.upper())
        period = "2y" if days > 365 else "1y"
        df = t.history(period=period, auto_adjust=True)
        if df.empty or len(df) < 2:
            return _empty_ohlcv()
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
        df = df[OHLCV_COLUMNS].sort_index().drop_duplicates()
        df.index = df.index.tz_localize(None).normalize()
        df.index.name = "date"
        return df
    except Exception:
        return _empty_ohlcv()


def get_daily_history_with_meta(
    ticker: str,
    days: int = 300,
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Fetch daily OHLCV and return provider lineage metadata.

    Metadata fields:
    - provider: "schwab" or "yfinance"
    - used_fallback: bool
    - fallback_reason: short reason when fallback is used
    - rows: number of rows returned
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
    meta: dict[str, Any] = {
        "provider": "schwab",
        "used_fallback": False,
        "fallback_reason": None,
        "rows": 0,
    }

    try:
        resp = _request_with_backoff(auth, "GET", url, params=params)
        resp.raise_for_status()
        data = resp.json()
        candles = data.get("candles")
        if not candles:
            out = _empty_ohlcv()
            meta["rows"] = 0
            return out, meta

        df = pd.DataFrame(candles)
        dt_series = pd.to_datetime(df["datetime"], unit="ms", utc=True) if "datetime" in df.columns else pd.NaT
        required = ["open", "high", "low", "close", "volume"]
        for c in required:
            if c not in df.columns:
                raise ValueError(f"API missing column: {c}")
        df = df[required].copy().astype({c: float for c in required})
        df.index = pd.DatetimeIndex(dt_series).tz_localize(None).normalize()
        df.index.name = "date"
        out = df[OHLCV_COLUMNS].sort_index().drop_duplicates()
        meta["rows"] = int(len(out))
        return out, meta
    except Exception as e:
        meta["provider"] = "yfinance"
        meta["used_fallback"] = True
        meta["fallback_reason"] = f"{type(e).__name__}"
        # If the circuit breaker is unstable, we'll very likely hit this path.
        # Keep the fallback behavior safe and non-crashing.
        try:
            from config import get_prefer_schwab_data
            if get_prefer_schwab_data(skill_dir):
                LOG.warning("Schwab data failed for %s (%s), using yfinance fallback", ticker, e)
        except ImportError:
            pass
        out = _get_daily_history_yfinance(ticker, days)
        meta["rows"] = int(len(out))
        return out, meta


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
    df, _meta = get_daily_history_with_meta(
        ticker=ticker,
        days=days,
        auth=auth,
        skill_dir=skill_dir,
    )
    return df


def extract_schwab_last_price(quote: dict[str, Any] | None) -> float | None:
    """Best-effort last trade / mark / prior close from a Schwab quote object (flat or nested)."""
    if not isinstance(quote, dict):
        return None
    paths: tuple[tuple[str, ...], ...] = (
        ("lastPrice",),
        ("quote", "lastPrice"),
        ("quote", "mark"),
        ("quote", "closePrice"),
        ("regular", "regularMarketLastPrice"),
        ("extended", "lastPrice"),
        ("extended", "mark"),
    )
    for path in paths:
        ptr: Any = quote
        ok = True
        for part in path:
            if not isinstance(ptr, dict) or part not in ptr:
                ok = False
                break
            ptr = ptr[part]
        if ok:
            try:
                value = float(ptr)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
    return None


def _select_schwab_quote_payload(data: Any, ticker: str) -> dict | None:
    """Pick the per-symbol quote dict from Schwab /marketdata/v1/quotes JSON."""
    t = ticker.upper().strip()
    if isinstance(data, dict):
        if t in data and isinstance(data[t], dict):
            return data[t]
        for k, v in data.items():
            if isinstance(k, str) and k.upper() == t and isinstance(v, dict):
                return v
        sym = data.get("symbol")
        if isinstance(sym, str) and sym.upper() == t:
            return data
        if "lastPrice" in data:
            return data
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and str(item.get("symbol", "")).upper() == t:
                return item
        if data and isinstance(data[0], dict):
            return data[0]
    return None


def get_current_quote_with_status(
    ticker: str,
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
) -> tuple[dict | None, dict[str, Any]]:
    """
    Fetch quote via Market Session. Returns (quote_dict_or_none, meta) where meta explains failures
    for dashboards and operators (HTTP status, reason codes, key names).
    """
    ticker_u = ticker.upper().strip()
    meta: dict[str, Any] = {
        "symbol": ticker_u,
        "http_status": None,
        "reason": None,
        "top_level_keys": None,
        "quote_keys": None,
        "error_detail": None,
    }
    auth = auth or DualSchwabAuth(skill_dir=skill_dir or SKILL_DIR)
    url = f"{SCHWAB_BASE}/marketdata/v1/quotes"
    try:
        resp = _request_with_backoff(auth, "GET", url, params={"symbols": ticker_u})
        meta["http_status"] = resp.status_code
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            meta["reason"] = "http_error"
            try:
                body = (resp.text or "").strip()[:400]
            except Exception:
                body = ""
            meta["error_detail"] = body or str(e)
            LOG.warning(
                "Schwab quotes HTTP %s for %s: %s",
                resp.status_code,
                ticker_u,
                meta["error_detail"],
            )
            fallback_quote, fallback_meta = _get_polygon_quote_fallback(ticker_u)
            if fallback_quote is not None:
                meta["fallback_provider"] = fallback_meta.get("provider")
                meta["reason"] = "schwab_fallback_polygon"
                px = extract_schwab_last_price(fallback_quote)
                if px is not None:
                    meta["last_price"] = round(px, 6)
                return fallback_quote, meta
            return None, meta
        data = resp.json()
        if isinstance(data, dict):
            meta["top_level_keys"] = sorted(str(k) for k in data.keys())[:32]
        quote = _select_schwab_quote_payload(data, ticker_u)
        if quote is None:
            meta["reason"] = "no_matching_symbol_in_response"
            fallback_quote, fallback_meta = _get_polygon_quote_fallback(ticker_u)
            if fallback_quote is not None:
                meta["fallback_provider"] = fallback_meta.get("provider")
                meta["reason"] = "schwab_fallback_polygon"
                px = extract_schwab_last_price(fallback_quote)
                if px is not None:
                    meta["last_price"] = round(px, 6)
                return fallback_quote, meta
            return None, meta
        meta["quote_keys"] = sorted(str(k) for k in quote.keys())[:32]
        price = extract_schwab_last_price(quote)
        if price is None:
            meta["reason"] = "last_price_not_parseable"
            fallback_quote, fallback_meta = _get_polygon_quote_fallback(ticker_u)
            if fallback_quote is not None:
                meta["fallback_provider"] = fallback_meta.get("provider")
                meta["reason"] = "schwab_fallback_polygon"
                fpx = extract_schwab_last_price(fallback_quote)
                if fpx is not None:
                    meta["last_price"] = round(fpx, 6)
                return fallback_quote, meta
        else:
            meta["last_price"] = round(price, 6)
        return quote, meta
    except Exception as e:
        meta["reason"] = type(e).__name__
        meta["error_detail"] = str(e)[:400]
        LOG.warning("get_current_quote failed for %s: %s", ticker_u, e)
    fallback_quote, fallback_meta = _get_polygon_quote_fallback(ticker_u)
    if fallback_quote is not None:
        meta["fallback_provider"] = fallback_meta.get("provider")
        meta["reason"] = "schwab_fallback_polygon"
        px = extract_schwab_last_price(fallback_quote)
        if px is not None:
            meta["last_price"] = round(px, 6)
        return fallback_quote, meta
    return None, meta


def get_current_quote(
    ticker: str,
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
) -> dict | None:
    """Fetch real-time quote using Market Session."""
    quote, _meta = get_current_quote_with_status(ticker, auth=auth, skill_dir=skill_dir)
    return quote
