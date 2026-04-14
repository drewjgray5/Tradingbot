"""
Async DataProvider with circuit breaker and automatic Yahoo Finance fallback.

Wraps Schwab quote + history fetches in an async interface. Tracks per-provider
error rates over a rolling window; when Schwab's error rate exceeds a configurable
threshold (default 2%), transparently routes requests to the fallback provider
until Schwab recovers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent

_ROLLING_WINDOW_SEC = 300  # 5-minute window for error rate calculation
_RECOVERY_PROBE_INTERVAL_SEC = 30


class Provider(str, Enum):
    SCHWAB = "schwab"
    YAHOO = "yahoo"


@dataclass
class _RequestOutcome:
    timestamp: float
    success: bool
    provider: str


@dataclass
class ProviderCircuitBreaker:
    """
    Tracks success/failure rates per provider over a rolling window.
    When the primary provider's error rate exceeds ``trip_threshold_pct``,
    the breaker opens and requests route to the fallback until a recovery
    probe succeeds.
    """

    trip_threshold_pct: float = 2.0
    rolling_window_sec: float = _ROLLING_WINDOW_SEC
    recovery_probe_interval_sec: float = _RECOVERY_PROBE_INTERVAL_SEC

    _outcomes: deque[_RequestOutcome] = field(default_factory=deque)
    _tripped_at: float | None = field(default=None, repr=False)
    _last_probe_at: float = field(default=0.0, repr=False)

    def record(self, provider: str, success: bool) -> None:
        now = time.monotonic()
        self._outcomes.append(_RequestOutcome(now, success, provider))
        self._evict_stale(now)

    def _evict_stale(self, now: float) -> None:
        cutoff = now - self.rolling_window_sec
        while self._outcomes and self._outcomes[0].timestamp < cutoff:
            self._outcomes.popleft()

    @property
    def schwab_error_rate_pct(self) -> float:
        self._evict_stale(time.monotonic())
        schwab = [o for o in self._outcomes if o.provider == Provider.SCHWAB]
        if not schwab:
            return 0.0
        failures = sum(1 for o in schwab if not o.success)
        return (failures / len(schwab)) * 100.0

    @property
    def is_tripped(self) -> bool:
        if self._tripped_at is None:
            if self.schwab_error_rate_pct > self.trip_threshold_pct:
                total = sum(1 for o in self._outcomes if o.provider == Provider.SCHWAB)
                if total >= 3:
                    self._tripped_at = time.monotonic()
                    LOG.warning(
                        "DataProvider circuit breaker OPEN: Schwab error rate %.1f%% > %.1f%%",
                        self.schwab_error_rate_pct,
                        self.trip_threshold_pct,
                    )
                    return True
            return False
        return True

    @property
    def should_probe_recovery(self) -> bool:
        if self._tripped_at is None:
            return False
        now = time.monotonic()
        return (now - self._last_probe_at) >= self.recovery_probe_interval_sec

    def record_probe_attempt(self) -> None:
        self._last_probe_at = time.monotonic()

    def reset(self) -> None:
        self._tripped_at = None
        LOG.info("DataProvider circuit breaker CLOSED: Schwab recovered")

    def status(self) -> dict[str, Any]:
        self._evict_stale(time.monotonic())
        schwab_total = sum(1 for o in self._outcomes if o.provider == Provider.SCHWAB)
        yahoo_total = sum(1 for o in self._outcomes if o.provider == Provider.YAHOO)
        return {
            "tripped": self.is_tripped,
            "schwab_error_rate_pct": round(self.schwab_error_rate_pct, 2),
            "schwab_requests": schwab_total,
            "yahoo_requests": yahoo_total,
            "rolling_window_sec": self.rolling_window_sec,
            "trip_threshold_pct": self.trip_threshold_pct,
        }


class DataProvider:
    """
    Async data provider that fetches quotes and history from Schwab with
    automatic fallback to Yahoo Finance when the circuit breaker trips.

    Usage::

        provider = DataProvider(skill_dir=SKILL_DIR)
        quote = await provider.get_quote("AAPL")
        df = await provider.get_history("AAPL", days=300)
    """

    def __init__(
        self,
        skill_dir: Path | str | None = None,
        trip_threshold_pct: float = 2.0,
    ):
        self.skill_dir = Path(skill_dir or SKILL_DIR)
        self.breaker = ProviderCircuitBreaker(trip_threshold_pct=trip_threshold_pct)
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def active_provider(self) -> str:
        return Provider.YAHOO if self.breaker.is_tripped else Provider.SCHWAB

    def status(self) -> dict[str, Any]:
        return {
            "active_provider": self.active_provider,
            **self.breaker.status(),
        }

    async def get_quote(
        self,
        ticker: str,
        auth: Any | None = None,
    ) -> dict[str, Any] | None:
        ticker = ticker.upper().strip()
        if self.breaker.is_tripped:
            if self.breaker.should_probe_recovery:
                self.breaker.record_probe_attempt()
                probe = await self._schwab_quote(ticker, auth)
                if probe is not None:
                    self.breaker.reset()
                    return probe
            return await self._yahoo_quote(ticker)
        result = await self._schwab_quote(ticker, auth)
        if result is not None:
            return result
        return await self._yahoo_quote(ticker)

    async def get_history(
        self,
        ticker: str,
        days: int = 300,
        auth: Any | None = None,
    ) -> pd.DataFrame:
        ticker = ticker.upper().strip()
        if self.breaker.is_tripped:
            if self.breaker.should_probe_recovery:
                self.breaker.record_probe_attempt()
                probe = await self._schwab_history(ticker, days, auth)
                if not probe.empty:
                    self.breaker.reset()
                    return probe
            return await self._yahoo_history(ticker, days)
        result = await self._schwab_history(ticker, days, auth)
        if not result.empty:
            return result
        return await self._yahoo_history(ticker, days)

    async def _schwab_quote(self, ticker: str, auth: Any | None) -> dict[str, Any] | None:
        loop = asyncio.get_event_loop()
        try:
            from market_data import get_current_quote_with_status

            quote, meta = await loop.run_in_executor(
                None, lambda: get_current_quote_with_status(ticker, auth=auth, skill_dir=self.skill_dir)
            )
            if quote is not None:
                self.breaker.record(Provider.SCHWAB, True)
                return quote
            self.breaker.record(Provider.SCHWAB, False)
            LOG.debug("Schwab quote failed for %s: %s", ticker, meta.get("reason"))
            return None
        except Exception as e:
            self.breaker.record(Provider.SCHWAB, False)
            LOG.warning("Schwab quote exception for %s: %s", ticker, e)
            return None

    async def _schwab_history(self, ticker: str, days: int, auth: Any | None) -> pd.DataFrame:
        loop = asyncio.get_event_loop()
        try:
            from market_data import get_daily_history

            df = await loop.run_in_executor(
                None, lambda: get_daily_history(ticker, days=days, auth=auth, skill_dir=self.skill_dir)
            )
            if not df.empty:
                self.breaker.record(Provider.SCHWAB, True)
                return df
            self.breaker.record(Provider.SCHWAB, False)
            return pd.DataFrame()
        except Exception as e:
            self.breaker.record(Provider.SCHWAB, False)
            LOG.warning("Schwab history exception for %s: %s", ticker, e)
            return pd.DataFrame()

    async def _yahoo_quote(self, ticker: str) -> dict[str, Any] | None:
        loop = asyncio.get_event_loop()
        try:
            def _fetch() -> dict[str, Any] | None:
                import yfinance as yf
                t = yf.Ticker(ticker)
                fi = getattr(t, "fast_info", None)
                if fi is None:
                    return None
                last = getattr(fi, "lastPrice", None) or getattr(fi, "last_price", None)
                if last is None and isinstance(fi, dict):
                    last = fi.get("lastPrice") or fi.get("last_price")
                if last is not None and float(last) > 0:
                    return {"symbol": ticker, "lastPrice": float(last), "source": "yahoo"}
                return None

            result = await loop.run_in_executor(None, _fetch)
            self.breaker.record(Provider.YAHOO, result is not None)
            return result
        except Exception as e:
            self.breaker.record(Provider.YAHOO, False)
            LOG.warning("Yahoo quote exception for %s: %s", ticker, e)
            return None

    async def _yahoo_history(self, ticker: str, days: int) -> pd.DataFrame:
        loop = asyncio.get_event_loop()
        try:
            def _fetch() -> pd.DataFrame:
                import yfinance as yf
                t = yf.Ticker(ticker)
                period = "2y" if days > 365 else "1y"
                df = t.history(period=period, auto_adjust=True)
                if df.empty or len(df) < 2:
                    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")
                df = df.rename(columns={
                    "Open": "open", "High": "high", "Low": "low",
                    "Close": "close", "Volume": "volume",
                })
                df = df[["open", "high", "low", "close", "volume"]].sort_index().drop_duplicates()
                df.index = df.index.tz_localize(None).normalize()
                df.index.name = "date"
                return df

            result = await loop.run_in_executor(None, _fetch)
            self.breaker.record(Provider.YAHOO, not result.empty)
            return result
        except Exception as e:
            self.breaker.record(Provider.YAHOO, False)
            LOG.warning("Yahoo history exception for %s: %s", ticker, e)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")
