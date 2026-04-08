"""
Operator-facing data quality signals: quote freshness, bar staleness, SEC cache
recency, and optional cross-checks when multiple providers are available.

Rolls up to data_quality: ok | degraded | stale | conflict with human-readable reasons.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent


def _safe_float(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


def _quote_epoch_ms(quote: dict[str, Any] | None) -> float | None:
    if not isinstance(quote, dict):
        return None
    nested = quote.get("quote") if isinstance(quote.get("quote"), dict) else {}
    for layer in (quote, nested):
        for key in (
            "quoteTimeInLong",
            "tradeTimeInLong",
            "regularMarketTradeTimeInLong",
            "lastUpdateTimeInLong",
        ):
            raw = layer.get(key)
            if raw is not None:
                try:
                    ms = float(raw)
                    if ms > 1e12:
                        return ms
                    if ms > 1e9:
                        return ms * 1000.0
                except (TypeError, ValueError):
                    continue
    return None


def _quote_age_sec(quote: dict[str, Any] | None) -> float | None:
    ms = _quote_epoch_ms(quote)
    if ms is None:
        return None
    now_ms = time.time() * 1000.0
    return max(0.0, (now_ms - ms) / 1000.0)


def _load_sec_cache_max_ts(skill_dir: Path) -> float | None:
    path = skill_dir / ".sec_cache.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    tickers = (data or {}).get("tickers") if isinstance(data, dict) else None
    if not isinstance(tickers, dict):
        return None
    best = 0.0
    for _tk, entry in tickers.items():
        if not isinstance(entry, dict):
            continue
        ts = _safe_float(entry.get("timestamp"))
        if ts is not None and ts > best:
            best = ts
    return best if best > 0 else None


def _cross_check_last_vs_quote(
    ticker: str,
    last_close: float | None,
    quote_last: float | None,
    max_rel_diff: float,
) -> tuple[bool, str | None]:
    if last_close is None or quote_last is None or last_close <= 0 or quote_last <= 0:
        return False, None
    rel = abs(quote_last - last_close) / last_close
    if rel > max_rel_diff:
        return True, (
            f"provider_cross_check: last {quote_last:.4f} vs daily_close {last_close:.4f} "
            f"(rel_diff {rel:.4%} > max {max_rel_diff:.4%})"
        )
    return False, None


def assess_symbol_data_health(
    ticker: str,
    auth: Any,
    skill_dir: Path | str | None = None,
) -> dict[str, Any]:
    """
    Assess quote age, daily bar freshness, SEC cache (global), optional yfinance cross-check.

    Returns a dict including data_quality, reasons (list[str]), and detail fields suitable
    for logs and operator payloads.
    """
    skill_dir = Path(skill_dir or SKILL_DIR)
    reasons: list[str] = []
    details: dict[str, Any] = {"ticker": ticker.upper()}

    try:
        from config import (
            get_data_bar_max_staleness_days,
            get_data_crosscheck_enabled,
            get_data_crosscheck_max_rel_diff,
            get_data_edgar_max_age_hours,
            get_data_quote_max_age_sec,
            get_sec_enrichment_enabled,
        )
    except ImportError:
        quote_max_age = 600.0
        bar_days = 7
        edgar_hours = 72.0
        cross_enabled = False
        cross_diff = 0.012
        sec_enabled = False
    else:
        quote_max_age = float(get_data_quote_max_age_sec(skill_dir))
        bar_days = int(get_data_bar_max_staleness_days(skill_dir))
        edgar_hours = float(get_data_edgar_max_age_hours(skill_dir))
        cross_enabled = bool(get_data_crosscheck_enabled(skill_dir))
        cross_diff = float(get_data_crosscheck_max_rel_diff(skill_dir))
        sec_enabled = bool(get_sec_enrichment_enabled(skill_dir))

    quote: dict[str, Any] | None = None
    try:
        from market_data import get_current_quote

        quote = get_current_quote(ticker, auth=auth, skill_dir=skill_dir)
    except Exception as e:
        LOG.debug("assess_symbol_data_health quote fetch failed: %s", e)
        quote = None

    q_age = _quote_age_sec(quote)
    details["quote_age_sec"] = round(q_age, 2) if q_age is not None else None
    if quote is None:
        reasons.append("quote_unavailable")
    elif q_age is None:
        reasons.append("quote_timestamp_missing")
    elif q_age > quote_max_age:
        reasons.append(f"quote_stale_age_sec_{q_age:.0f}_gt_{quote_max_age:.0f}")

    last_close: float | None = None
    bar_last_date: str | None = None
    try:
        from market_data import get_daily_history

        df = get_daily_history(ticker, days=max(40, bar_days + 10), auth=auth, skill_dir=skill_dir)
        if df is not None and not df.empty and "close" in df.columns:
            last_close = _safe_float(df["close"].iloc[-1])
            idx = df.index[-1]
            if hasattr(idx, "strftime"):
                bar_last_date = idx.strftime("%Y-%m-%d")
            else:
                bar_last_date = str(idx)[:10]
    except Exception as e:
        LOG.debug("assess_symbol_data_health history failed: %s", e)

    details["bar_last_date"] = bar_last_date
    details["last_daily_close"] = last_close

    if bar_last_date:
        try:
            last_dt = datetime.strptime(bar_last_date, "%Y-%m-%d").date()
            today = datetime.now(timezone.utc).date()
            gap = (today - last_dt).days
            details["bar_age_calendar_days"] = gap
            if gap > bar_days:
                reasons.append(f"bars_stale_last_bar_{bar_last_date}_age_days_{gap}_gt_{bar_days}")
        except Exception:
            reasons.append("bar_date_parse_failed")
    else:
        reasons.append("bars_missing_or_empty")

    quote_last = None
    if isinstance(quote, dict):
        for layer in (quote, quote.get("quote") if isinstance(quote.get("quote"), dict) else {}):
            for key in ("lastPrice", "mark", "regularMarketLastPrice", "closePrice"):
                v = _safe_float(layer.get(key))
                if v is not None and v > 0:
                    quote_last = v
                    break
            if quote_last is not None:
                break
    details["quote_last"] = quote_last

    sec_ts = _load_sec_cache_max_ts(skill_dir)
    details["sec_cache_latest_ts"] = sec_ts
    if sec_enabled:
        now = time.time()
        if sec_ts is None:
            reasons.append("sec_cache_empty_or_missing")
        elif (now - sec_ts) / 3600.0 > edgar_hours:
            reasons.append(
                f"sec_cache_stale_age_h_{(now - sec_ts) / 3600.0:.1f}_gt_{edgar_hours:.1f}"
            )

    alt_last: float | None = None
    if cross_enabled:
        try:
            import yfinance as yf

            fi = getattr(yf.Ticker(ticker.upper()), "fast_info", None)
            if fi is not None:
                lp = getattr(fi, "last_price", None) or getattr(fi, "lastPrice", None)
                alt_last = _safe_float(lp)
                if alt_last is None and isinstance(fi, dict):
                    alt_last = _safe_float(fi.get("lastPrice") or fi.get("last_price"))
        except Exception as e:
            LOG.debug("data_health cross-check yfinance failed: %s", e)
    details["crosscheck_alt_last"] = alt_last

    conflict = False
    if cross_enabled and quote_last is not None:
        ref = alt_last if alt_last is not None and alt_last > 0 else last_close
        if ref is not None and ref > 0:
            is_conflict, msg = _cross_check_last_vs_quote(ticker, ref, quote_last, cross_diff)
            if is_conflict and msg:
                conflict = True
                reasons.append(msg)

    if conflict:
        status = "conflict"
    elif any(
        r.startswith("quote_stale") or r.startswith("bars_stale") or r == "quote_timestamp_missing"
        for r in reasons
    ):
        status = "stale"
    elif reasons:
        status = "degraded"
    else:
        status = "ok"

    out = {
        "data_quality": status,
        "reasons": reasons,
        "details": details,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }
    if status != "ok":
        LOG.info("data_health %s %s: %s", ticker.upper(), status, "; ".join(reasons[:5]))
    return out


def assess_scan_session_data_health(
    auth: Any,
    skill_dir: Path | str | None = None,
) -> dict[str, Any]:
    """SPY-proxied session health for scanner / operator summaries."""
    return assess_symbol_data_health("SPY", auth=auth, skill_dir=skill_dir)


# Stable names for tests / CI scripts (avoid importing private helpers).
parse_quote_epoch_ms = _quote_epoch_ms
parse_quote_age_seconds = _quote_age_sec


def merge_operator_payload(data_quality: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten for JSON reports / web payloads."""
    if not data_quality:
        return {"data_quality": "unknown", "data_quality_reasons": []}
    return {
        "data_quality": data_quality.get("data_quality"),
        "data_quality_reasons": list(data_quality.get("reasons") or []),
        "data_quality_details": data_quality.get("details") or {},
    }
