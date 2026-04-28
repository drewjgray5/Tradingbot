"""
Backtest runner for Stage 2 + VCP strategy with live-parity leaning rules.

This module favors rule parity (shared Stage 2/VCP checks, breakout confirmation,
quality gates, and sector climate filter) and reports return/risk diagnostics.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import requests

try:
    import yfinance as yf
except ImportError:
    print("Install yfinance: pip install yfinance")
    raise

from backtest_guardrails import AdaptiveGuardrailPolicy, load_adaptive_guardrail_policy
from backtest_intelligence import (
    BacktestIntelligenceConfig,
    apply_event_risk_overlay,
    apply_exec_quality_overlay,
    apply_meta_policy_overlay,
    evaluate_event_risk_for_backtest,
)
from config import (
    get_adaptive_stop_base_pct,
    get_adaptive_stop_enabled,
    get_backtest_adaptive_guardrail_policy_path,
    get_backtest_adaptive_guardrails_enabled,
    get_backtest_portfolio_max_positions,
    get_backtest_portfolio_starting_equity,
    get_breakout_confirm_enabled,
    get_forensic_altman_min,
    get_forensic_beneish_max,
    get_forensic_cache_hours,
    get_forensic_enabled,
    get_forensic_filter_mode,
    get_forensic_sloan_max,
    get_pead_enabled,
    get_pead_lookback_days,
    get_quality_gates_mode,
    get_quality_soft_min_reasons,
)
from env_overrides import temporary_env
from schwab_auth import DualSchwabAuth
from signal_scanner import _evaluate_quality_gates, _load_watchlist
from stage_analysis import add_indicators, check_vcp_volume, compute_signal_components, is_stage_2

SKILL_DIR = Path(__file__).resolve().parent
LOG = logging.getLogger(__name__)


def _runtime_skill_dir() -> Path:
    """Optional runtime override for hosted workers.

    Celery SaaS tasks materialize per-user token files into a temporary skill
    directory. Backtest internals that instantiate DualSchwabAuth directly
    need this override to read tenant-scoped token files instead of the
    repository root.
    """
    raw = (os.getenv("TB_RUNTIME_SKILL_DIR") or "").strip()
    if raw:
        return Path(raw)
    return SKILL_DIR


def _temporary_env(overrides: dict[str, str] | None) -> Iterator[None]:
    # Compatibility wrapper retained for existing imports/call sites.
    return temporary_env(overrides)


def _load_adaptive_guardrail_policy(skill_dir: Path) -> AdaptiveGuardrailPolicy | None:
    if not get_backtest_adaptive_guardrails_enabled(skill_dir):
        return None
    policy_path = get_backtest_adaptive_guardrail_policy_path(skill_dir)
    return load_adaptive_guardrail_policy(skill_dir, policy_path)


HOLD_DAYS = 20
MIN_BARS = 260
SECTOR_LOOKBACK_DAYS = 21
DEFAULT_SLIPPAGE_BPS_PER_SIDE = 15.0
DEFAULT_FEE_PER_SHARE = 0.005
DEFAULT_MIN_FEE_PER_ORDER = 1.0
DEFAULT_MAX_ADV_PARTICIPATION = 0.02


@dataclass
class BacktestContext:
    watchlist: list[str]
    price_data: dict[str, pd.DataFrame]
    sector_etf_by_ticker: dict[str, str | None]
    sector_perf: dict[str, pd.DataFrame]
    excluded_tickers: list[dict[str, Any]]
    data_integrity: dict[str, Any]


@dataclass
class CandidateSignal:
    ticker: str
    idx: int
    signal: dict[str, Any]
    reasons: list[str]
    comps: dict[str, Any]
    sector_reason: str
    entry_price: float
    day_volume: float
    qty_hint: int
    stop_pct: float
    pm_mult: float
    meta_size_mult: float
    event_size_mult: float
    event_policy: dict[str, Any] | None
    meta_payload: dict[str, Any] | None
    adaptive_size_mult: float
    effective_slippage_bps: float
    exec_info: dict[str, Any]
    telemetry: dict[str, Any]


@dataclass
class ActivePosition:
    ticker: str
    entry_idx: int
    entry_date: pd.Timestamp
    entry_price: float
    qty: int
    day_volume: float
    signal: dict[str, Any]
    reasons: list[str]
    comps: dict[str, Any]
    sector_reason: str
    stop_pct: float
    pm_mult: float
    meta_size_mult: float
    event_size_mult: float
    event_policy: dict[str, Any] | None
    meta_payload: dict[str, Any] | None
    effective_slippage_bps: float
    exec_info: dict[str, Any]
    telemetry: dict[str, Any]
    highest_close: float


def _normalize_history(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")
    out = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    cols = ["open", "high", "low", "close", "volume"]
    out = out[[c for c in cols if c in out.columns]].copy()
    for col in cols:
        if col not in out.columns:
            out[col] = 0.0
    out = out[cols].astype(float)
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    out.index.name = "date"
    return out.sort_index().drop_duplicates()


def _fetch_history(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    df, _meta = _fetch_history_with_meta(symbol, start_date, end_date)
    return df


def _is_ohlc_path_logging_enabled() -> bool:
    """Opt-in: include the daily OHLCV slice between entry and exit on each
    trade record. Roughly +2-3 KB per trade and only useful for the Phase 2
    replay engine (alternate exits, tighter stops, partial TPs, trailing
    stops). Default OFF so production runs and the existing chunk schema
    are unaffected."""
    return os.environ.get("BACKTEST_OHLC_PATH", "").lower() in ("1", "true", "yes", "on")


def _compute_mfe_mae(
    df: pd.DataFrame, entry_idx: int, exit_idx: int, entry_price: float
) -> tuple[float | None, float | None]:
    """Maximum Favorable / Adverse Excursion as fractional returns.

    Walks the *intraday* high and low between entry and exit (exclusive of
    entry day, inclusive of exit day) so the result reflects the worst /
    best print the trade ever saw, not just close-to-close moves. Returns
    (None, None) when the window is empty or the entry price is invalid.
    """
    if entry_price <= 0 or exit_idx < entry_idx:
        return None, None
    last = min(exit_idx, len(df) - 1)
    if last <= entry_idx:
        return 0.0, 0.0
    window = df.iloc[entry_idx + 1 : last + 1]
    if window.empty:
        return 0.0, 0.0
    try:
        high_series = window["high"] if "high" in window.columns else window["close"]
        low_series = window["low"] if "low" in window.columns else window["close"]
    except KeyError:
        return None, None
    try:
        max_high = float(high_series.max())
        min_low = float(low_series.min())
    except (TypeError, ValueError):
        return None, None
    return (max_high - entry_price) / entry_price, (min_low - entry_price) / entry_price


def _build_ohlc_path(df: pd.DataFrame, entry_idx: int, exit_idx: int) -> list[dict[str, Any]]:
    """Serialize the inclusive [entry, exit] OHLCV slice as a list of small
    dicts so the path round-trips through chunk JSON. ~80 bytes/day, capped
    by the trade's hold window."""
    if exit_idx < entry_idx:
        return []
    last = min(exit_idx, len(df) - 1)
    window = df.iloc[entry_idx : last + 1]
    if window.empty:
        return []
    out: list[dict[str, Any]] = []
    for ts, row in window.iterrows():
        try:
            out.append(
                {
                    "date": pd.Timestamp(ts).isoformat()[:10],
                    "open": float(row.get("open", 0.0) or 0.0),
                    "high": float(row.get("high", 0.0) or 0.0),
                    "low": float(row.get("low", 0.0) or 0.0),
                    "close": float(row.get("close", 0.0) or 0.0),
                    "volume": float(row.get("volume", 0.0) or 0.0),
                }
            )
        except (TypeError, ValueError):
            continue
    return out


def _fetch_history_with_meta(
    symbol: str,
    start_date: str,
    end_date: str,
    schwab_auth: DualSchwabAuth | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    meta: dict[str, Any] = {
        "provider": "unknown",
        "used_fallback": False,
        "reason": "unknown",
        "rows": 0,
    }
    if (os.getenv("SCHWAB_ONLY_DATA") or "").strip().lower() in {"1", "true", "yes", "on"}:
        out = _fetch_history_schwab(symbol, start_date, end_date, auth=schwab_auth)
        meta["provider"] = "schwab"
        meta["used_fallback"] = False
        meta["rows"] = int(len(out))
        meta["reason"] = "schwab_only"
        return out, meta
    fast_rate_limit = (os.getenv("BACKTEST_FAST_RATE_LIMIT") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    for attempt in range(3):
        try:
            t = yf.Ticker(symbol)
            raw = t.history(start=start_date, end=end_date, auto_adjust=True, timeout=20)
            if raw is None:
                meta["provider"] = "yfinance"
                meta["used_fallback"] = True
                meta["reason"] = "yfinance_history_none"
                return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date"), meta
            time.sleep(0.05)
            out = _normalize_history(raw)
            meta["provider"] = "yfinance"
            meta["used_fallback"] = True
            meta["rows"] = int(len(out))
            meta["reason"] = "yfinance_ok" if not out.empty else "yfinance_empty_after_normalize"
            return out, meta
        except Exception as e:
            msg = str(e)
            if "Too Many Requests" in msg and attempt < 2 and not fast_rate_limit:
                time.sleep(2.0 * (attempt + 1))
                continue
            LOG.warning("History fetch failed for %s: %s", symbol, e)
            meta["provider"] = "yfinance"
            meta["used_fallback"] = True
            if "Too Many Requests" in msg and fast_rate_limit:
                meta["reason"] = "yfinance_rate_limited_fastfail"
            else:
                meta["reason"] = f"yfinance_exception:{type(e).__name__}"
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date"), meta
    meta["provider"] = "yfinance"
    meta["used_fallback"] = True
    meta["reason"] = "yfinance_retries_exhausted"
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date"), meta


def _fetch_history_schwab(
    symbol: str,
    start_date: str,
    end_date: str,
    auth: DualSchwabAuth | None = None,
) -> pd.DataFrame:
    url = "https://api.schwabapi.com/marketdata/v1/pricehistory"
    params = {
        "symbol": str(symbol).upper().strip(),
        "periodType": "month",
        "frequencyType": "daily",
        "startDate": int(datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc).timestamp() * 1000),
        "endDate": int(datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc).timestamp() * 1000),
    }
    try:
        session_auth = auth or DualSchwabAuth(skill_dir=_runtime_skill_dir(), auto_refresh=False)
        token = session_auth.get_market_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code == 401 and session_auth.market_session.force_refresh():
            token = session_auth.get_market_token()
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        candles = payload.get("candles") or []
        if not candles:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")
        df = pd.DataFrame(candles)
        if "datetime" not in df.columns:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")
        dt_series = pd.to_datetime(df["datetime"], unit="ms", utc=True)
        for c in ("open", "high", "low", "close", "volume"):
            if c not in df.columns:
                return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")
        out = df[["open", "high", "low", "close", "volume"]].copy().astype(float)
        out.index = pd.DatetimeIndex(dt_series).tz_localize(None).normalize()
        out.index.name = "date"
        return out.sort_index().drop_duplicates()
    except Exception as e:
        LOG.warning("Schwab-only history fetch failed for %s: %s", symbol, e)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")


def _prepare_context(
    start_date: str,
    end_date: str,
    watchlist: list[str] | None = None,
    skill_dir: Path | None = None,
) -> BacktestContext:
    from sector_strength import SECTOR_ETFS, get_ticker_sector_etf

    sd = skill_dir or SKILL_DIR
    schwab_only = (os.getenv("SCHWAB_ONLY_DATA") or "").strip().lower() in {"1", "true", "yes", "on"}
    schwab_auth: DualSchwabAuth | None = None
    if schwab_only:
        # Reuse one tenant-scoped auth object across the full run to avoid token
        # refresh races and accidental fallback to repository-root token files.
        schwab_auth = DualSchwabAuth(skill_dir=sd, auto_refresh=False)
    universe = watchlist if watchlist is not None else _load_watchlist(sd)
    cleaned = [str(t).strip().upper() for t in universe if str(t).strip()]
    universe = list(dict.fromkeys(cleaned))
    price_data: dict[str, pd.DataFrame] = {}
    sector_etf_by_ticker: dict[str, str | None] = {}
    excluded: list[dict[str, Any]] = []
    data_integrity: dict[str, Any] = {
        "history_fetch_total": 0,
        "history_fetch_empty": 0,
        "history_fetch_too_short": 0,
        "history_provider_schwab": 0,
        "history_provider_yfinance": 0,
        "history_provider_unknown": 0,
        "history_fallback_used": 0,
        "history_reason_counts": {},
    }

    for ticker in universe:
        df, history_meta = _fetch_history_with_meta(ticker, start_date, end_date, schwab_auth=schwab_auth)
        data_integrity["history_fetch_total"] = int(data_integrity.get("history_fetch_total", 0) or 0) + 1
        provider = str(history_meta.get("provider") or "unknown")
        reason = str(history_meta.get("reason") or "unknown")
        reason_counts = dict(data_integrity.get("history_reason_counts") or {})
        reason_counts[reason] = int(reason_counts.get(reason, 0) or 0) + 1
        data_integrity["history_reason_counts"] = reason_counts
        if provider == "schwab":
            data_integrity["history_provider_schwab"] = int(data_integrity.get("history_provider_schwab", 0) or 0) + 1
        elif provider == "yfinance":
            data_integrity["history_provider_yfinance"] = (
                int(data_integrity.get("history_provider_yfinance", 0) or 0) + 1
            )
        else:
            data_integrity["history_provider_unknown"] = int(data_integrity.get("history_provider_unknown", 0) or 0) + 1
        if bool(history_meta.get("used_fallback")):
            data_integrity["history_fallback_used"] = int(data_integrity.get("history_fallback_used", 0) or 0) + 1
        if df.empty or len(df) < MIN_BARS:
            if df.empty:
                data_integrity["history_fetch_empty"] = int(data_integrity.get("history_fetch_empty", 0) or 0) + 1
            else:
                data_integrity["history_fetch_too_short"] = (
                    int(data_integrity.get("history_fetch_too_short", 0) or 0) + 1
                )
            excluded.append({"ticker": ticker, "reason": "insufficient_history", "bars": len(df)})
            continue
        price_data[ticker] = add_indicators(df)
        try:
            sector_etf_by_ticker[ticker] = get_ticker_sector_etf(ticker, skill_dir=sd)
        except Exception:
            sector_etf_by_ticker[ticker] = None

    sector_perf: dict[str, pd.DataFrame] = {}
    for sym in sorted(set(SECTOR_ETFS + ["SPY"])):
        sdf, _meta = _fetch_history_with_meta(sym, start_date, end_date, schwab_auth=schwab_auth)
        if not sdf.empty and len(sdf) >= MIN_BARS:
            sector_perf[sym] = sdf

    return BacktestContext(
        watchlist=sorted(price_data.keys()),
        price_data=price_data,
        sector_etf_by_ticker=sector_etf_by_ticker,
        sector_perf=sector_perf,
        excluded_tickers=excluded,
        data_integrity=data_integrity,
    )


def _window_return(df: pd.DataFrame, idx: int, lookback: int) -> float | None:
    if idx < lookback or idx >= len(df):
        return None
    start_px = float(df["close"].iloc[idx - lookback])
    end_px = float(df["close"].iloc[idx])
    if start_px <= 0:
        return None
    return (end_px - start_px) / start_px


def _sector_filter_pass(ticker: str, entry_idx: int, context: BacktestContext) -> tuple[bool, str]:
    etf = context.sector_etf_by_ticker.get(ticker)
    if not etf:
        return False, "no_sector_etf"

    etf_df = context.sector_perf.get(etf)
    spy_df = context.sector_perf.get("SPY")
    if etf_df is None or spy_df is None:
        return True, "sector_data_unavailable_allow"

    ticker_date = context.price_data[ticker].index[entry_idx]
    try:
        etf_i = etf_df.index.get_indexer([ticker_date], method="pad")[0]
        spy_i = spy_df.index.get_indexer([ticker_date], method="pad")[0]
    except Exception:
        return True, "sector_index_fallback_allow"
    if etf_i < 0 or spy_i < 0:
        return True, "sector_date_missing_allow"

    etf_ret = _window_return(etf_df, etf_i, SECTOR_LOOKBACK_DAYS)
    spy_ret = _window_return(spy_df, spy_i, SECTOR_LOOKBACK_DAYS)
    if etf_ret is None or spy_ret is None:
        return True, "sector_short_window_allow"
    if etf_ret <= spy_ret:
        return False, "sector_not_winning"
    return True, "sector_winning"


def _run_mirofish_for_entry(
    ticker: str,
    seeded_df: pd.DataFrame,
    skill_dir: Path | None = None,
) -> dict[str, Any] | None:
    if os.environ.get("BACKTEST_SKIP_MIROFISH", "").strip().lower() in ("1", "true", "yes"):
        return None
    sd = skill_dir or SKILL_DIR
    try:
        from engine_analysis import MarketSimulation

        sim = MarketSimulation(ticker, seed_df=seeded_df, skill_dir=sd)
        result = sim.run()
        return {
            "conviction_score": result.get("conviction_score"),
            "summary": result.get("summary"),
            "continuation_probability": result.get("continuation_probability"),
            "bull_trap_probability": result.get("bull_trap_probability"),
        }
    except Exception as e:
        LOG.warning("MiroFish sim failed for %s: %s", ticker, e)
        return None


def _safe_telemetry_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    return float(out)


def _safe_telemetry_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _build_telemetry_payload(signal: dict[str, Any], comps: dict[str, Any]) -> dict[str, Any]:
    advisory = signal.get("advisory") if isinstance(signal.get("advisory"), dict) else {}
    meta_policy = signal.get("meta_policy") if isinstance(signal.get("meta_policy"), dict) else {}
    return {
        "mirofish_conviction": _safe_telemetry_float(signal.get("mirofish_conviction")),
        "advisory_prob": _safe_telemetry_float(advisory.get("p_up_10d")),
        "agent_uncertainty": _safe_telemetry_float(meta_policy.get("uncertainty_score")),
        "vcp_volume_ratio": _safe_telemetry_float(comps.get("avg_vcp_volume_ratio")),
        "sector_rs_rank": _safe_telemetry_int(
            signal.get("sector_rs_rank", signal.get("sector_relative_strength_rank"))
        ),
    }


def _simulate_exit(
    df: pd.DataFrame, entry_idx: int, hold_days: int, stop_pct: float
) -> tuple[float, pd.Timestamp, str]:
    entry_price = float(df["close"].iloc[entry_idx])
    highest_close = entry_price
    last_idx = min(entry_idx + hold_days, len(df) - 1)
    for j in range(entry_idx + 1, last_idx + 1):
        px = float(df["close"].iloc[j])
        highest_close = max(highest_close, px)
        trail_stop = highest_close * (1.0 - stop_pct)
        if px <= trail_stop:
            return px, df.index[j], "trailing_stop"
    return float(df["close"].iloc[last_idx]), df.index[last_idx], "time_exit"


def _resolve_stop_pct_for_entry(df: pd.DataFrame, entry_idx: int, skill_dir: Path | None = None) -> float:
    from config import (
        get_adaptive_stop_atr_mult,
        get_adaptive_stop_base_pct,
        get_adaptive_stop_enabled,
        get_adaptive_stop_max_pct,
        get_adaptive_stop_min_pct,
        get_adaptive_stop_trend_lookback,
    )

    sd = skill_dir or SKILL_DIR
    base_pct = float(get_adaptive_stop_base_pct(sd))
    if not get_adaptive_stop_enabled(sd):
        return max(0.01, base_pct)
    min_pct = float(get_adaptive_stop_min_pct(sd))
    max_pct = float(get_adaptive_stop_max_pct(sd))
    atr_mult = float(get_adaptive_stop_atr_mult(sd))
    lookback = max(10, int(get_adaptive_stop_trend_lookback(sd)))

    try:
        price = float(df["close"].iloc[entry_idx])
        atr = float(df["atr_14"].iloc[entry_idx]) if "atr_14" in df.columns else 0.0
        if price <= 0 or atr <= 0:
            return max(min_pct, min(max_pct, base_pct))
        atr_pct = atr / price
        prev_i = max(0, entry_idx - lookback)
        prev_price = float(df["close"].iloc[prev_i])
        trend_lookback = ((price / prev_price) - 1.0) if prev_price > 0 else 0.0
        stop_pct = atr_pct * atr_mult
        if trend_lookback < -0.03:
            stop_pct *= 1.2
        elif trend_lookback > 0.06:
            stop_pct *= 0.9
        return max(min_pct, min(max_pct, stop_pct))
    except Exception:
        return max(min_pct, min(max_pct, base_pct))


def _estimate_order_qty(entry_price: float, day_volume: float, max_adv_participation: float) -> int:
    if entry_price <= 0:
        return 1
    max_by_liq = int(max(1.0, float(day_volume) * float(max_adv_participation)))
    # Keep notional bounded so fees/slippage percent remain realistic and comparable.
    target_notional = 10000.0
    qty_target = int(max(1.0, target_notional / entry_price))
    return max(1, min(max_by_liq, qty_target))


def _net_return_after_costs(
    entry_price: float,
    exit_price: float,
    qty: int,
    slippage_bps_per_side: float,
    fee_per_share: float,
    min_fee_per_order: float,
) -> tuple[float, dict[str, float]]:
    if entry_price <= 0 or qty <= 0:
        return 0.0, {"slippage_pct": 0.0, "fees_pct": 0.0, "gross_return": 0.0}
    gross = (exit_price - entry_price) / entry_price
    slippage_pct = 2.0 * (float(slippage_bps_per_side) / 10000.0)
    entry_fee = max(float(min_fee_per_order), float(fee_per_share) * qty)
    exit_fee = max(float(min_fee_per_order), float(fee_per_share) * qty)
    fees_pct = (entry_fee + exit_fee) / (entry_price * qty)
    net = gross - slippage_pct - fees_pct
    return float(net), {"slippage_pct": float(slippage_pct), "fees_pct": float(fees_pct), "gross_return": float(gross)}


def _simulate_portfolio_equity(
    trades: list[dict[str, Any]],
    *,
    starting_equity: float,
    max_concurrent_positions: int,
    position_size_pct: float,
    risk_per_trade_pct: float,
) -> dict[str, Any]:
    """Replay per-trade returns through a shared equity book.

    Each trade carries ``entry_date``, ``exit_date``, ``net_return`` (per-share
    %), and optionally ``stop_pct`` for risk-based sizing. Trades arriving while
    the book already holds ``max_concurrent_positions`` open positions are
    dropped and counted under ``capacity_filtered`` — they remain in the PF
    numerator/denominator (PF is signal quality, sizing-invariant) but do not
    contribute to the equity curve.

    Sizing per accepted trade:
      * If ``risk_per_trade_pct > 0`` AND ``stop_pct`` is finite & positive,
        allocate ``equity * risk_per_trade_pct / stop_pct`` (capped at 100%
        of equity to avoid implicit leverage).
      * Otherwise fall back to ``equity * position_size_pct``.

    Returns a dict with ``equity_curve`` (list of ``(timestamp, equity)``),
    ``total_return_net_pct``, ``max_drawdown_net_pct``, ``capacity_filtered``,
    ``avg_concurrent``, ``peak_concurrent``, ``risk_sized_count``,
    ``fixed_sized_count``.
    """
    if not trades:
        return {
            "equity_curve": [],
            "total_return_net_pct": 0.0,
            "max_drawdown_net_pct": 0.0,
            "capacity_filtered": 0,
            "avg_concurrent": 0.0,
            "peak_concurrent": 0,
            "risk_sized_count": 0,
            "fixed_sized_count": 0,
            "starting_equity": float(starting_equity),
            "ending_equity": float(starting_equity),
        }

    parsed: list[dict[str, Any]] = []
    for t in trades:
        try:
            ed = pd.Timestamp(t.get("entry_date") or t.get("exit_date"))
            xd = pd.Timestamp(t.get("exit_date") or t.get("entry_date"))
        except Exception:
            continue
        if pd.isna(ed) or pd.isna(xd):
            continue
        if xd < ed:
            ed, xd = xd, ed
        parsed.append(
            {
                "entry_date": ed,
                "exit_date": xd,
                "net_return": float(t.get("net_return", 0.0) or 0.0),
                "stop_pct": float(t.get("stop_pct", 0.0) or 0.0),
            }
        )
    if not parsed:
        return {
            "equity_curve": [],
            "total_return_net_pct": 0.0,
            "max_drawdown_net_pct": 0.0,
            "capacity_filtered": 0,
            "avg_concurrent": 0.0,
            "peak_concurrent": 0,
            "risk_sized_count": 0,
            "fixed_sized_count": 0,
            "starting_equity": float(starting_equity),
            "ending_equity": float(starting_equity),
        }

    parsed.sort(key=lambda t: (t["entry_date"], t["exit_date"]))
    equity = float(starting_equity)
    peak = equity
    worst_dd = 0.0
    open_positions: list[dict[str, Any]] = []
    capacity_filtered = 0
    risk_sized = 0
    fixed_sized = 0
    concurrent_samples: list[int] = []
    peak_concurrent = 0
    curve: list[tuple[str, float]] = [(parsed[0]["entry_date"].isoformat(), equity)]

    def _close_due(now: pd.Timestamp) -> None:
        nonlocal equity, peak, worst_dd
        still_open: list[dict[str, Any]] = []
        ready = [p for p in open_positions if p["exit_date"] <= now]
        ready.sort(key=lambda p: p["exit_date"])
        for p in ready:
            equity += p["allocated"] * p["net_return"]
            if equity > peak:
                peak = equity
            if peak > 0:
                dd = (equity / peak) - 1.0
                if dd < worst_dd:
                    worst_dd = dd
            curve.append((p["exit_date"].isoformat(), equity))
        for p in open_positions:
            if p["exit_date"] > now:
                still_open.append(p)
        open_positions[:] = still_open

    for t in parsed:
        _close_due(t["entry_date"])
        if len(open_positions) >= max_concurrent_positions:
            capacity_filtered += 1
            continue
        if risk_per_trade_pct > 0 and t["stop_pct"] > 0:
            allocated = min(equity, equity * risk_per_trade_pct / t["stop_pct"])
            risk_sized += 1
        else:
            allocated = equity * position_size_pct
            fixed_sized += 1
        if allocated <= 0 or equity <= 0:
            capacity_filtered += 1
            continue
        open_positions.append(
            {
                "exit_date": t["exit_date"],
                "net_return": t["net_return"],
                "allocated": allocated,
            }
        )
        concurrent_samples.append(len(open_positions))
        if len(open_positions) > peak_concurrent:
            peak_concurrent = len(open_positions)

    if open_positions:
        last = max(p["exit_date"] for p in open_positions)
        _close_due(last + pd.Timedelta(days=1))

    total_return = (equity / starting_equity) - 1.0 if starting_equity > 0 else 0.0
    avg_concurrent = sum(concurrent_samples) / len(concurrent_samples) if concurrent_samples else 0.0
    return {
        "equity_curve": curve,
        "total_return_net_pct": round(100.0 * total_return, 4),
        "max_drawdown_net_pct": round(100.0 * worst_dd, 4),
        "capacity_filtered": int(capacity_filtered),
        "avg_concurrent": round(float(avg_concurrent), 3),
        "peak_concurrent": int(peak_concurrent),
        "risk_sized_count": int(risk_sized),
        "fixed_sized_count": int(fixed_sized),
        "starting_equity": float(starting_equity),
        "ending_equity": float(equity),
    }


def _max_drawdown(returns: pd.Series) -> float:
    """Legacy single-asset drawdown helper retained ONLY for callers that
    explicitly opt out of the portfolio simulator. Treats the trade list as a
    sequential 100%-of-equity roll, which is **not** a real portfolio metric;
    do not surface its output as a deployable risk number."""
    if returns.empty:
        return 0.0
    equity = (1.0 + returns).cumprod()
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return float(dd.min())


def _quality_mode_should_filter(reasons: list[str], skill_dir: Path | None = None) -> bool:
    if not reasons:
        return False
    if "weak_breakout_volume" in reasons:
        return True
    sd = skill_dir or SKILL_DIR
    mode = get_quality_gates_mode(sd)
    if mode in {"off", "shadow"}:
        return False
    if mode == "hard":
        return True
    soft_min = max(1, int(get_quality_soft_min_reasons(sd)))
    return len(reasons) >= soft_min


def run_backtest(
    tickers: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    slippage_bps_per_side: float = DEFAULT_SLIPPAGE_BPS_PER_SIDE,
    fee_per_share: float = DEFAULT_FEE_PER_SHARE,
    min_fee_per_order: float = DEFAULT_MIN_FEE_PER_ORDER,
    max_adv_participation: float = DEFAULT_MAX_ADV_PARTICIPATION,
    skill_dir: Path | None = None,
    env_overrides: dict[str, str] | None = None,
    include_all_trades: bool = False,
    prediction_market_snapshot_path: str | None = None,
    intelligence_overlay: dict[str, str] | BacktestIntelligenceConfig | None = None,
) -> dict[str, Any]:
    with _temporary_env(env_overrides):
        return _run_backtest_core(
            tickers=tickers,
            start_date=start_date,
            end_date=end_date,
            slippage_bps_per_side=slippage_bps_per_side,
            fee_per_share=fee_per_share,
            min_fee_per_order=min_fee_per_order,
            max_adv_participation=max_adv_participation,
            skill_dir=skill_dir,
            include_all_trades=include_all_trades,
            prediction_market_snapshot_path=prediction_market_snapshot_path,
            intelligence_overlay=intelligence_overlay,
        )


def _run_backtest_core(
    tickers: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    slippage_bps_per_side: float = DEFAULT_SLIPPAGE_BPS_PER_SIDE,
    fee_per_share: float = DEFAULT_FEE_PER_SHARE,
    min_fee_per_order: float = DEFAULT_MIN_FEE_PER_ORDER,
    max_adv_participation: float = DEFAULT_MAX_ADV_PARTICIPATION,
    skill_dir: Path | None = None,
    include_all_trades: bool = False,
    prediction_market_snapshot_path: str | None = None,
    intelligence_overlay: dict[str, str] | BacktestIntelligenceConfig | None = None,
) -> dict[str, Any]:
    sd = skill_dir or SKILL_DIR
    end = end_date or datetime.now().strftime("%Y-%m-%d")
    start = start_date or (datetime.now() - timedelta(days=3652)).strftime("%Y-%m-%d")

    if isinstance(intelligence_overlay, BacktestIntelligenceConfig):
        overlay_cfg = intelligence_overlay
    elif intelligence_overlay is not None:
        overlay_cfg = BacktestIntelligenceConfig.from_mapping(intelligence_overlay)
    else:
        overlay_cfg = BacktestIntelligenceConfig.from_env(sd)

    requested = [str(t).strip().upper() for t in (tickers or []) if str(t).strip()]
    context = _prepare_context(start, end, watchlist=requested if requested else None, skill_dir=sd)

    all_trades: list[dict[str, Any]] = []
    diagnostics: dict[str, int | float] = {
        "stage2_fail": 0,
        "vcp_fail": 0,
        "breakout_not_confirmed": 0,
        "sector_not_winning": 0,
        "quality_gates_filtered": 0,
        "forensic_filtered": 0,
        "regime_blocked": 0,
        "entries": 0,
        "capital_filtered": 0,
        "position_limit_filtered": 0,
        "liquidity_filtered": 0,
        "adaptive_guardrail_filtered": 0,
        "adaptive_guardrail_downsized": 0,
        "adaptive_guardrail_extra_slot_entries": 0,
        "prediction_market_processed": 0,
        "prediction_market_applied": 0,
        "prediction_market_skipped": 0,
        "prediction_market_errors": 0,
        "meta_policy_processed": 0,
        "meta_policy_suppressed": 0,
        "meta_policy_downsized": 0,
        "event_risk_live_blocked": 0,
        "event_risk_live_downsized": 0,
        "event_risk_live_flagged": 0,
        "event_risk_shadow_flagged": 0,
        "event_risk_shadow_would_block": 0,
        "event_risk_shadow_would_downsize": 0,
        "exit_manager_partial_done": 0,
        "exits_trailing_stop": 0,
        "exits_time_exit": 0,
        "exits_sma50_break": 0,
        "exits_vcp_invalidation": 0,
        "exits_final_liquidation": 0,
    }

    breakout_enabled = get_breakout_confirm_enabled(sd)
    quality_mode = get_quality_gates_mode(sd)
    adaptive_stop_enabled = get_adaptive_stop_enabled(sd)
    stop_pct_base = float(get_adaptive_stop_base_pct(sd))
    forensic_enabled = get_forensic_enabled(sd)
    forensic_mode = get_forensic_filter_mode(sd)
    forensic_cache_hours = float(get_forensic_cache_hours(sd))
    forensic_sloan_max = float(get_forensic_sloan_max(sd))
    forensic_beneish_max = float(get_forensic_beneish_max(sd))
    forensic_altman_min = float(get_forensic_altman_min(sd))
    pead_enabled = get_pead_enabled(sd)
    pead_lookback_days = int(get_pead_lookback_days(sd))
    adaptive_guardrail_policy = _load_adaptive_guardrail_policy(sd)

    starting_equity = float(get_backtest_portfolio_starting_equity(sd))
    max_concurrent_positions = max(1, int(get_backtest_portfolio_max_positions(sd)))
    max_position_size_pct = 0.10
    current_cash = float(starting_equity)
    active_positions: dict[str, ActivePosition] = {}
    equity_curve: list[tuple[str, float]] = []
    concurrent_samples: list[int] = []
    peak_concurrent = 0

    prediction_market_engine = None
    prediction_market_mode = "off"
    prediction_market_provider = "off"
    try:
        from prediction_market import (
            PredictionMarketOverlayEngine,
            apply_overlay_to_signal,
            build_prediction_market_config,
            build_provider,
            load_historical_provider,
        )

        pm_cfg = build_prediction_market_config(skill_dir=sd)
        prediction_market_mode = str(pm_cfg.mode)
        prediction_market_provider = str(pm_cfg.provider)
        if pm_cfg.enabled and pm_cfg.mode == "live":
            if prediction_market_snapshot_path:
                prediction_market_provider = "historical_file"
                pm_provider = load_historical_provider(prediction_market_snapshot_path)
                prediction_market_engine = PredictionMarketOverlayEngine(config=pm_cfg, provider=pm_provider)
            elif os.getenv("BACKTEST_ALLOW_NON_HISTORICAL_PM", "").strip().lower() in {"1", "true", "yes", "on"}:
                pm_provider = build_provider(pm_cfg)
                prediction_market_engine = PredictionMarketOverlayEngine(config=pm_cfg, provider=pm_provider)
            else:
                prediction_market_mode = "off"
                LOG.warning(
                    "Prediction-market backtest disabled: require historical snapshot file for strict PIT evaluation."
                )
    except Exception as e:
        diagnostics["prediction_market_errors"] = int(diagnostics["prediction_market_errors"]) + 1
        LOG.warning("Prediction-market backtest setup skipped: %s", e)

    spy_df = context.sector_perf.get("SPY")
    spy_regime: pd.Series | None = None
    if spy_df is not None and len(spy_df) >= 200:
        spy_with_sma = add_indicators(spy_df)
        spy_regime = spy_with_sma["close"] > spy_with_sma["sma_200"]

    forensic_cache: dict[str, dict[str, Any] | None] = {}
    ticker_date_index: dict[str, dict[pd.Timestamp, int]] = {}
    timeline_dates: set[pd.Timestamp] = set()
    for ticker in context.watchlist:
        df = context.price_data.get(ticker)
        if df is None or df.empty:
            continue
        idx_map: dict[pd.Timestamp, int] = {}
        for idx, ts in enumerate(df.index):
            t = pd.Timestamp(ts)
            idx_map[t] = idx
            if idx >= 200:
                timeline_dates.add(t)
        ticker_date_index[ticker] = idx_map

    timeline = sorted(timeline_dates)
    if not timeline:
        return {
            "start_date": start,
            "end_date": end,
            "total_trades": 0,
            "win_rate": 0.0,
            "total_return_pct": 0.0,
            "total_return_net_pct": 0.0,
            "cagr_pct": 0.0,
            "cagr_net_pct": 0.0,
            "avg_return_pct": 0.0,
            "avg_return_net_pct": 0.0,
            "avg_gain_pct": 0.0,
            "avg_loss_pct": 0.0,
            "profit_factor": 0.0,
            "profit_factor_net": 0.0,
            "max_drawdown_pct": 0.0,
            "max_drawdown_net_pct": 0.0,
            "portfolio_enabled": True,
            "portfolio_summary": {
                "capacity_filtered": 0,
                "avg_concurrent": 0.0,
                "peak_concurrent": 0,
                "risk_sized_count": 0,
                "fixed_sized_count": 0,
                "starting_equity": float(starting_equity),
                "ending_equity": float(starting_equity),
            },
            "diagnostics": diagnostics,
            "quality_gates_mode": quality_mode,
            "prediction_market_mode": prediction_market_mode,
            "prediction_market_provider": prediction_market_provider,
            "intelligence_overlay": overlay_cfg.as_dict(),
            "excluded_tickers": context.excluded_tickers[:50],
            "excluded_count": len(context.excluded_tickers),
            "universe_size": len(context.watchlist),
            "data_integrity": context.data_integrity,
            "findings": "No valid timeline generated over the requested window.",
            "trades_sample": [],
        }

    def _price_at_or_before(ticker: str, ts: pd.Timestamp) -> float | None:
        df = context.price_data.get(ticker)
        if df is None or df.empty:
            return None
        try:
            i = int(df.index.get_indexer([ts], method="pad")[0])
        except Exception:
            return None
        if i < 0:
            return None
        return float(df["close"].iloc[i])

    def _equity_at(ts: pd.Timestamp) -> float:
        mark = float(current_cash)
        for pos in active_positions.values():
            px = _price_at_or_before(pos.ticker, ts)
            if px is not None and px > 0:
                mark += float(pos.qty) * float(px)
            else:
                mark += float(pos.qty) * float(pos.entry_price)
        return mark

    def _candidate_rank_key(candidate: CandidateSignal) -> tuple[float, float, float]:
        miro = candidate.signal.get("mirofish_conviction")
        adv = ((candidate.signal.get("advisory") or {}).get("p_up_10d"))
        primary = _safe_telemetry_float(miro, _safe_telemetry_float(adv))
        return (
            primary,
            _safe_telemetry_float(adv),
            _safe_telemetry_float(candidate.signal.get("signal_score")),
        )

    for day_ts in timeline:
        # Exit pass first: capital from exits is available for same-day entries.
        for ticker, pos in list(active_positions.items()):
            df = context.price_data.get(ticker)
            if df is None or df.empty:
                continue
            idx = ticker_date_index.get(ticker, {}).get(day_ts)
            if idx is None or idx <= pos.entry_idx:
                continue
            px = float(df["close"].iloc[idx])
            pos.highest_close = max(float(pos.highest_close), px)
            trail_stop = float(pos.highest_close) * (1.0 - float(pos.stop_pct))

            exit_reason: str | None = None
            if px <= trail_stop:
                exit_reason = "trailing_stop"
                diagnostics["exits_trailing_stop"] = int(diagnostics["exits_trailing_stop"]) + 1
            elif idx - pos.entry_idx >= HOLD_DAYS:
                exit_reason = "time_exit"
                diagnostics["exits_time_exit"] = int(diagnostics["exits_time_exit"]) + 1
            else:
                window = df.iloc[: idx + 1]
                sma50 = float(window["sma_50"].iloc[-1]) if "sma_50" in window.columns else 0.0
                if sma50 > 0 and px < sma50:
                    exit_reason = "sma50_break"
                    diagnostics["exits_sma50_break"] = int(diagnostics["exits_sma50_break"]) + 1
                else:
                    try:
                        if not check_vcp_volume(window, sd):
                            exit_reason = "vcp_invalidation"
                            diagnostics["exits_vcp_invalidation"] = int(diagnostics["exits_vcp_invalidation"]) + 1
                    except Exception:
                        pass
            if not exit_reason:
                continue

            net_ret, cost_ctx = _net_return_after_costs(
                entry_price=float(pos.entry_price),
                exit_price=float(px),
                qty=int(pos.qty),
                slippage_bps_per_side=float(pos.effective_slippage_bps),
                fee_per_share=fee_per_share,
                min_fee_per_order=min_fee_per_order,
            )
            gross_ret = (float(px) - float(pos.entry_price)) / float(pos.entry_price) if pos.entry_price > 0 else 0.0
            current_cash += float(pos.entry_price) * float(pos.qty) * (1.0 + float(net_ret))
            mfe, mae = _compute_mfe_mae(df, pos.entry_idx, idx, float(pos.entry_price))
            ohlc_path = _build_ohlc_path(df, pos.entry_idx, idx) if _is_ohlc_path_logging_enabled() else []

            all_trades.append(
                {
                    "ticker": pos.ticker,
                    "entry_date": pd.Timestamp(pos.entry_date).isoformat(),
                    "exit_date": pd.Timestamp(day_ts).isoformat(),
                    "entry_price": round(float(pos.entry_price), 4),
                    "exit_price": round(float(px), 4),
                    "return": float(gross_ret),
                    "net_return": float(net_ret),
                    "mfe": mfe,
                    "mae": mae,
                    "ohlc_path": ohlc_path,
                    "exit_reason": exit_reason,
                    "signal_score": pos.signal.get("signal_score"),
                    "mirofish_conviction": pos.signal.get("mirofish_conviction"),
                    "sector_filter": pos.sector_reason,
                    "quality_reasons": pos.reasons,
                    "forensic_sloan": pos.signal.get("forensic_sloan"),
                    "forensic_beneish": pos.signal.get("forensic_beneish"),
                    "forensic_altman": pos.signal.get("forensic_altman"),
                    "forensic_flags": pos.signal.get("forensic_flags"),
                    "pead_beat": pos.signal.get("pead_beat"),
                    "pead_surprise_pct": pos.signal.get("pead_surprise_pct"),
                    "qty_estimate": int(pos.qty),
                    "day_volume": float(pos.day_volume),
                    "slippage_pct": float(cost_ctx["slippage_pct"]),
                    "fees_pct": float(cost_ctx["fees_pct"]),
                    "stop_pct": float(pos.stop_pct),
                    "prediction_market_status": ((pos.signal.get("prediction_market") or {}).get("status")),
                    "prediction_market_reason": ((pos.signal.get("prediction_market") or {}).get("reason")),
                    "prediction_market_size_multiplier": float(pos.pm_mult),
                    "meta_policy_decision": (pos.meta_payload or {}).get("decision") if pos.meta_payload else None,
                    "meta_policy_size_multiplier": float(pos.meta_size_mult),
                    "event_risk_action": (pos.event_policy or {}).get("action") if pos.event_policy else None,
                    "event_risk_size_multiplier": float(pos.event_size_mult),
                    "exit_manager_partial_done": False,
                    "exec_quality_regime": pos.exec_info.get("regime"),
                    "exec_quality_effective_slippage_bps": pos.exec_info.get("effective_slippage_bps"),
                    "telemetry": pos.telemetry,
                }
            )
            active_positions.pop(ticker, None)

        # Entry pass: evaluate all tickers on the same date with point-in-time windows.
        candidates: list[CandidateSignal] = []
        for ticker in context.watchlist:
            if ticker in active_positions:
                continue
            df = context.price_data.get(ticker)
            if df is None or df.empty:
                continue
            idx = ticker_date_index.get(ticker, {}).get(day_ts)
            if idx is None or idx < 200 or idx >= len(df) - 1:
                continue
            window = df.iloc[: idx + 1]

            if spy_regime is not None:
                try:
                    spy_i = int(spy_regime.index.get_indexer([day_ts], method="pad")[0])
                    if spy_i >= 0 and not bool(spy_regime.iloc[spy_i]):
                        diagnostics["regime_blocked"] = int(diagnostics["regime_blocked"]) + 1
                        continue
                except Exception:
                    pass

            if not is_stage_2(window, sd):
                diagnostics["stage2_fail"] = int(diagnostics["stage2_fail"]) + 1
                continue
            if not check_vcp_volume(window, sd):
                diagnostics["vcp_fail"] = int(diagnostics["vcp_fail"]) + 1
                continue
            if breakout_enabled and idx >= 1 and float(df["close"].iloc[idx]) < float(df["high"].iloc[idx - 1]):
                diagnostics["breakout_not_confirmed"] = int(diagnostics["breakout_not_confirmed"]) + 1
                continue

            sector_ok, sector_reason = _sector_filter_pass(ticker, idx, context)
            if not sector_ok:
                if sector_reason == "sector_not_winning":
                    diagnostics["sector_not_winning"] = int(diagnostics["sector_not_winning"]) + 1
                continue

            forensic_snapshot = forensic_cache.get(ticker)
            if forensic_enabled and forensic_mode != "off":
                try:
                    if forensic_snapshot is None:
                        from forensic_accounting import compute_forensic_snapshot

                        forensic_snapshot = compute_forensic_snapshot(
                            ticker,
                            skill_dir=sd,
                            cache_hours=forensic_cache_hours,
                            sloan_max=forensic_sloan_max,
                            beneish_max=forensic_beneish_max,
                            altman_min=forensic_altman_min,
                        )
                        forensic_cache[ticker] = forensic_snapshot
                    forensic_flags = list((forensic_snapshot or {}).get("forensic_flags", []) or [])
                    if forensic_mode == "hard" and forensic_flags:
                        diagnostics["forensic_filtered"] = int(diagnostics["forensic_filtered"]) + 1
                        continue
                except Exception as e:
                    LOG.debug("Backtest forensic check skipped for %s: %s", ticker, e)

            miro = _run_mirofish_for_entry(ticker, window, skill_dir=sd)
            comps = compute_signal_components(
                window,
                mirofish_conviction=miro.get("conviction_score") if miro else None,
                mirofish_result=miro,
            )
            signal: dict[str, Any] = {
                "ticker": ticker,
                "signal_score": float(comps.get("score", 0) or 0),
                "latest_volume": float(window["volume"].iloc[-1]),
                "avg_vol_50": float(window["avg_vol_50"].iloc[-1]),
                "mirofish_result": miro,
                "mirofish_conviction": miro.get("conviction_score") if miro else None,
                "forensic_sloan": ((forensic_snapshot or {}).get("sloan") or {}).get("sloan_ratio"),
                "forensic_beneish": ((forensic_snapshot or {}).get("beneish") or {}).get("m_score"),
                "forensic_altman": ((forensic_snapshot or {}).get("altman") or {}).get("z_score"),
                "forensic_flags": list((forensic_snapshot or {}).get("forensic_flags", []) or []),
            }

            pead_info = None
            if pead_enabled:
                try:
                    from earnings_signal import check_earnings_at_date

                    pead_info = check_earnings_at_date(
                        ticker,
                        day_ts,
                        df=window,
                        lookback_days=pead_lookback_days,
                    )
                except Exception as e:
                    LOG.debug("Backtest PEAD check skipped for %s: %s", ticker, e)
            signal["pead_surprise_pct"] = (pead_info or {}).get("surprise_pct")
            signal["pead_beat"] = (pead_info or {}).get("beat")

            if prediction_market_engine is not None and prediction_market_mode == "live":
                diagnostics["prediction_market_processed"] = int(diagnostics["prediction_market_processed"]) + 1
                try:
                    entry_dt = pd.Timestamp(day_ts).to_pydatetime()
                    entry_dt_utc = (
                        entry_dt.replace(tzinfo=timezone.utc)
                        if entry_dt.tzinfo is None
                        else entry_dt.astimezone(timezone.utc)
                    )
                    regime_for_entry = True
                    if spy_regime is not None:
                        try:
                            spy_i = int(spy_regime.index.get_indexer([day_ts], method="pad")[0])
                            if spy_i >= 0:
                                regime_for_entry = bool(spy_regime.iloc[spy_i])
                        except Exception:
                            pass
                    evaluation = prediction_market_engine.evaluate(
                        ticker=ticker,
                        as_of=entry_dt_utc,
                        regime_is_bullish=regime_for_entry,
                    )
                    signal = apply_overlay_to_signal(signal=signal, evaluation=evaluation, advisory=None)
                    if evaluation.status == "ok" and bool(evaluation.overlay.get("applied")):
                        diagnostics["prediction_market_applied"] = int(diagnostics["prediction_market_applied"]) + 1
                    elif evaluation.status == "error":
                        diagnostics["prediction_market_errors"] = int(diagnostics["prediction_market_errors"]) + 1
                    else:
                        diagnostics["prediction_market_skipped"] = int(diagnostics["prediction_market_skipped"]) + 1
                except Exception as e:
                    diagnostics["prediction_market_errors"] = int(diagnostics["prediction_market_errors"]) + 1
                    LOG.warning("Backtest prediction-market overlay failed for %s: %s", ticker, e)

            reasons = _evaluate_quality_gates(signal, sd)
            if _quality_mode_should_filter(reasons, sd):
                diagnostics["quality_gates_filtered"] = int(diagnostics["quality_gates_filtered"]) + 1
                continue

            meta_size_mult = 1.0
            event_size_mult = 1.0
            adaptive_size_mult = 1.0
            event_policy = None
            meta_payload: dict[str, Any] | None = None
            if overlay_cfg.meta_policy != "off":
                signal, meta_allow, meta_size_mult = apply_meta_policy_overlay(
                    signal=signal,
                    diagnostics=diagnostics,
                    skill_dir=sd,
                    mode=overlay_cfg.meta_policy,
                )
                meta_payload = signal.get("meta_policy")
                if not meta_allow:
                    continue
            if overlay_cfg.event_risk != "off":
                event_policy = evaluate_event_risk_for_backtest(
                    ticker=ticker,
                    entry_date=day_ts,
                    pead_info=pead_info,
                    skill_dir=sd,
                    mode=overlay_cfg.event_risk,
                )
                event_allow, event_size_mult = apply_event_risk_overlay(
                    policy=event_policy,
                    diagnostics=diagnostics,
                    mode=overlay_cfg.event_risk,
                )
                signal["event_risk"] = event_policy
                if not event_allow:
                    continue

            signal_score = _safe_telemetry_float(signal.get("signal_score"), 0.0)
            vcp_volume_ratio = _safe_telemetry_float(comps.get("avg_vcp_volume_ratio"), 0.0)
            if adaptive_guardrail_policy is not None:
                if not adaptive_guardrail_policy.allows_entry(signal_score):
                    diagnostics["adaptive_guardrail_filtered"] = int(diagnostics["adaptive_guardrail_filtered"]) + 1
                    continue
                adaptive_size_mult = adaptive_guardrail_policy.size_multiplier(signal_score, vcp_volume_ratio)
                if adaptive_size_mult < 0.999:
                    diagnostics["adaptive_guardrail_downsized"] = int(diagnostics["adaptive_guardrail_downsized"]) + 1

            entry_price = float(df["close"].iloc[idx])
            day_volume = float(df["volume"].iloc[idx]) if "volume" in df.columns else 0.0
            qty_hint = _estimate_order_qty(entry_price, day_volume, max_adv_participation=max_adv_participation)
            pm_mult = _safe_telemetry_float(signal.get("prediction_market_size_multiplier"), 1.0)
            pm_mult = max(0.85, min(1.15, pm_mult))
            combined_size_mult = pm_mult * float(meta_size_mult) * float(event_size_mult) * float(adaptive_size_mult)
            qty_hint = max(1, int(round(float(qty_hint) * combined_size_mult)))
            liq_cap = int(day_volume * max_adv_participation) if day_volume > 0 else 1
            if qty_hint > liq_cap:
                diagnostics["liquidity_filtered"] = int(diagnostics["liquidity_filtered"]) + 1
                qty_hint = liq_cap
            if qty_hint <= 0:
                diagnostics["liquidity_filtered"] = int(diagnostics["liquidity_filtered"]) + 1
                continue

            stop_pct_entry = _resolve_stop_pct_for_entry(df, idx, skill_dir=sd)
            effective_slippage_bps, exec_info = apply_exec_quality_overlay(
                slippage_bps_per_side=slippage_bps_per_side,
                day_volume=day_volume,
                qty=qty_hint,
                skill_dir=sd,
                mode=overlay_cfg.exec_quality,
            )
            telemetry = _build_telemetry_payload(signal, comps)
            candidates.append(
                CandidateSignal(
                    ticker=ticker,
                    idx=idx,
                    signal=signal,
                    reasons=reasons,
                    comps=comps,
                    sector_reason=sector_reason,
                    entry_price=entry_price,
                    day_volume=day_volume,
                    qty_hint=qty_hint,
                    stop_pct=stop_pct_entry,
                    pm_mult=pm_mult,
                    meta_size_mult=float(meta_size_mult),
                    event_size_mult=float(event_size_mult),
                    event_policy=event_policy,
                    meta_payload=meta_payload,
                    adaptive_size_mult=float(adaptive_size_mult),
                    effective_slippage_bps=float(effective_slippage_bps),
                    exec_info=exec_info,
                    telemetry=telemetry,
                )
            )

        day_equity = _equity_at(day_ts)
        max_position_notional = max(0.0, float(day_equity) * max_position_size_pct)
        for candidate in sorted(candidates, key=_candidate_rank_key, reverse=True):
            max_positions_for_candidate = max_concurrent_positions
            if adaptive_guardrail_policy is not None:
                cand_score = _safe_telemetry_float(candidate.signal.get("signal_score"), 0.0)
                max_positions_for_candidate = adaptive_guardrail_policy.max_positions_for_candidate(
                    max_concurrent_positions,
                    cand_score,
                )
            if len(active_positions) >= max_positions_for_candidate:
                diagnostics["capital_filtered"] = int(diagnostics["capital_filtered"]) + 1
                continue
            if candidate.entry_price <= 0:
                continue
            qty_by_cash = int(max(0.0, min(max_position_notional, current_cash)) / candidate.entry_price)
            qty_by_liq = int(candidate.day_volume * max_adv_participation) if candidate.day_volume > 0 else 1
            qty = min(int(candidate.qty_hint), int(qty_by_cash), int(qty_by_liq))
            if qty <= 0:
                if qty_by_cash <= 0:
                    diagnostics["capital_filtered"] = int(diagnostics["capital_filtered"]) + 1
                else:
                    diagnostics["position_limit_filtered"] = int(diagnostics["position_limit_filtered"]) + 1
                continue
            notional = float(qty) * float(candidate.entry_price)
            if notional > current_cash:
                diagnostics["capital_filtered"] = int(diagnostics["capital_filtered"]) + 1
                continue

            current_cash -= notional
            if max_positions_for_candidate > max_concurrent_positions and len(active_positions) >= max_concurrent_positions:
                diagnostics["adaptive_guardrail_extra_slot_entries"] = (
                    int(diagnostics["adaptive_guardrail_extra_slot_entries"]) + 1
                )
            active_positions[candidate.ticker] = ActivePosition(
                ticker=candidate.ticker,
                entry_idx=candidate.idx,
                entry_date=day_ts,
                entry_price=float(candidate.entry_price),
                qty=int(qty),
                day_volume=float(candidate.day_volume),
                signal=candidate.signal,
                reasons=candidate.reasons,
                comps=candidate.comps,
                sector_reason=candidate.sector_reason,
                stop_pct=float(candidate.stop_pct),
                pm_mult=float(candidate.pm_mult),
                meta_size_mult=float(candidate.meta_size_mult),
                event_size_mult=float(candidate.event_size_mult),
                event_policy=candidate.event_policy,
                meta_payload=candidate.meta_payload,
                effective_slippage_bps=float(candidate.effective_slippage_bps),
                exec_info=candidate.exec_info,
                telemetry=candidate.telemetry,
                highest_close=float(candidate.entry_price),
            )
            diagnostics["entries"] = int(diagnostics["entries"]) + 1

        concurrent_samples.append(len(active_positions))
        peak_concurrent = max(peak_concurrent, len(active_positions))
        equity_curve.append((pd.Timestamp(day_ts).isoformat(), float(_equity_at(day_ts))))

    # Final liquidation of remaining positions at end date.
    final_ts = pd.Timestamp(timeline[-1])
    for ticker, pos in list(active_positions.items()):
        df = context.price_data.get(ticker)
        if df is None or df.empty:
            continue
        try:
            exit_idx = int(df.index.get_indexer([final_ts], method="pad")[0])
        except Exception:
            exit_idx = len(df) - 1
        if exit_idx <= pos.entry_idx:
            exit_idx = min(len(df) - 1, pos.entry_idx + 1)
        exit_price = float(df["close"].iloc[exit_idx])
        exit_date = pd.Timestamp(df.index[exit_idx])
        diagnostics["exits_final_liquidation"] = int(diagnostics["exits_final_liquidation"]) + 1
        net_ret, cost_ctx = _net_return_after_costs(
            entry_price=float(pos.entry_price),
            exit_price=float(exit_price),
            qty=int(pos.qty),
            slippage_bps_per_side=float(pos.effective_slippage_bps),
            fee_per_share=fee_per_share,
            min_fee_per_order=min_fee_per_order,
        )
        gross_ret = (float(exit_price) - float(pos.entry_price)) / float(pos.entry_price) if pos.entry_price > 0 else 0.0
        current_cash += float(pos.entry_price) * float(pos.qty) * (1.0 + float(net_ret))
        mfe, mae = _compute_mfe_mae(df, pos.entry_idx, exit_idx, float(pos.entry_price))
        ohlc_path = _build_ohlc_path(df, pos.entry_idx, exit_idx) if _is_ohlc_path_logging_enabled() else []
        all_trades.append(
            {
                "ticker": pos.ticker,
                "entry_date": pd.Timestamp(pos.entry_date).isoformat(),
                "exit_date": pd.Timestamp(exit_date).isoformat(),
                "entry_price": round(float(pos.entry_price), 4),
                "exit_price": round(float(exit_price), 4),
                "return": float(gross_ret),
                "net_return": float(net_ret),
                "mfe": mfe,
                "mae": mae,
                "ohlc_path": ohlc_path,
                "exit_reason": "final_liquidation",
                "signal_score": pos.signal.get("signal_score"),
                "mirofish_conviction": pos.signal.get("mirofish_conviction"),
                "sector_filter": pos.sector_reason,
                "quality_reasons": pos.reasons,
                "forensic_sloan": pos.signal.get("forensic_sloan"),
                "forensic_beneish": pos.signal.get("forensic_beneish"),
                "forensic_altman": pos.signal.get("forensic_altman"),
                "forensic_flags": pos.signal.get("forensic_flags"),
                "pead_beat": pos.signal.get("pead_beat"),
                "pead_surprise_pct": pos.signal.get("pead_surprise_pct"),
                "qty_estimate": int(pos.qty),
                "day_volume": float(pos.day_volume),
                "slippage_pct": float(cost_ctx["slippage_pct"]),
                "fees_pct": float(cost_ctx["fees_pct"]),
                "stop_pct": float(pos.stop_pct),
                "prediction_market_status": ((pos.signal.get("prediction_market") or {}).get("status")),
                "prediction_market_reason": ((pos.signal.get("prediction_market") or {}).get("reason")),
                "prediction_market_size_multiplier": float(pos.pm_mult),
                "meta_policy_decision": (pos.meta_payload or {}).get("decision") if pos.meta_payload else None,
                "meta_policy_size_multiplier": float(pos.meta_size_mult),
                "event_risk_action": (pos.event_policy or {}).get("action") if pos.event_policy else None,
                "event_risk_size_multiplier": float(pos.event_size_mult),
                "exit_manager_partial_done": False,
                "exec_quality_regime": pos.exec_info.get("regime"),
                "exec_quality_effective_slippage_bps": pos.exec_info.get("effective_slippage_bps"),
                "telemetry": pos.telemetry,
            }
        )
        active_positions.pop(ticker, None)

    ending_equity = float(current_cash)
    equity_curve.append((pd.Timestamp(final_ts).isoformat(), ending_equity))

    if not all_trades:
        return {
            "start_date": start,
            "end_date": end,
            "total_trades": 0,
            "win_rate": 0.0,
            "total_return_pct": round(100.0 * ((ending_equity / starting_equity) - 1.0), 2),
            "total_return_net_pct": round(100.0 * ((ending_equity / starting_equity) - 1.0), 2),
            "cagr_pct": 0.0,
            "cagr_net_pct": 0.0,
            "avg_return_pct": 0.0,
            "avg_return_net_pct": 0.0,
            "avg_gain_pct": 0.0,
            "avg_loss_pct": 0.0,
            "profit_factor": 0.0,
            "profit_factor_net": 0.0,
            "max_drawdown_pct": 0.0,
            "max_drawdown_net_pct": 0.0,
            "portfolio_enabled": True,
            "portfolio_summary": {
                "capacity_filtered": int(diagnostics.get("capital_filtered", 0)),
                "avg_concurrent": round(
                    (sum(concurrent_samples) / len(concurrent_samples)) if concurrent_samples else 0.0,
                    3,
                ),
                "peak_concurrent": int(peak_concurrent),
                "risk_sized_count": 0,
                "fixed_sized_count": int(diagnostics.get("entries", 0)),
                "starting_equity": float(starting_equity),
                "ending_equity": float(ending_equity),
            },
            "avg_holding_days": HOLD_DAYS,
            "trailing_stop_pct": round(100.0 * stop_pct_base, 2),
            "adaptive_stop_enabled": bool(adaptive_stop_enabled),
            "adaptive_guardrails_enabled": bool(adaptive_guardrail_policy is not None),
            "slippage_bps_per_side": float(slippage_bps_per_side),
            "fee_per_share": float(fee_per_share),
            "min_fee_per_order": float(min_fee_per_order),
            "max_adv_participation": float(max_adv_participation),
            "diagnostics": diagnostics,
            "quality_gates_mode": quality_mode,
            "prediction_market_mode": prediction_market_mode,
            "prediction_market_provider": prediction_market_provider,
            "intelligence_overlay": overlay_cfg.as_dict(),
            "excluded_tickers": context.excluded_tickers[:50],
            "excluded_count": len(context.excluded_tickers),
            "universe_size": len(context.watchlist),
            "data_integrity": context.data_integrity,
            "findings": "No trades generated over the requested window.",
            "trades_sample": [],
        }

    trades_df = pd.DataFrame(all_trades)
    ret = trades_df["return"].astype(float)
    ret_net = trades_df["net_return"].astype(float)
    wins = int((ret > 0).sum())
    wins_net = int((ret_net > 0).sum())
    total = int(len(trades_df))
    avg_ret = float(ret.mean())
    avg_ret_net = float(ret_net.mean())
    avg_gain = float(ret[ret > 0].mean()) if (ret > 0).any() else 0.0
    avg_loss = float(ret[ret <= 0].mean()) if (ret <= 0).any() else 0.0
    avg_gain_net = float(ret_net[ret_net > 0].mean()) if (ret_net > 0).any() else 0.0
    avg_loss_net = float(ret_net[ret_net <= 0].mean()) if (ret_net <= 0).any() else 0.0
    gross_profit = float(ret[ret > 0].sum()) if (ret > 0).any() else 0.0
    gross_loss = abs(float(ret[ret <= 0].sum())) if (ret <= 0).any() else 0.0
    gross_profit_net = float(ret_net[ret_net > 0].sum()) if (ret_net > 0).any() else 0.0
    gross_loss_net = abs(float(ret_net[ret_net <= 0].sum())) if (ret_net <= 0).any() else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    profit_factor_net = (gross_profit_net / gross_loss_net) if gross_loss_net > 0 else float("inf")

    total_ret = (ending_equity / starting_equity) - 1.0 if starting_equity > 0 else 0.0
    total_ret_net = total_ret
    years = max(1e-9, (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25)
    cagr = float((1.0 + total_ret) ** (1.0 / years) - 1.0) if total_ret > -1.0 else -1.0
    cagr_net = cagr

    eq_series = pd.Series([float(v) for _d, v in equity_curve]) if equity_curve else pd.Series([starting_equity])
    peak = eq_series.cummax()
    dd = (eq_series / peak) - 1.0
    max_dd = float(dd.min()) if not dd.empty else 0.0

    findings = (
        f"Event-driven portfolio backtest generated {total} trades across {len(context.watchlist)} symbols. "
        f"Gross win rate {100.0 * wins / total:.1f}%, net win rate {100.0 * wins_net / total:.1f}%, "
        f"portfolio net return {100.0 * total_ret_net:.2f}% with max DD {100.0 * max_dd:.2f}%, "
        f"net PF {profit_factor_net if profit_factor_net == float('inf') else round(float(profit_factor_net), 3)}."
    )

    out: dict[str, Any] = {
        "start_date": start,
        "end_date": end,
        "total_trades": total,
        "win_rate": round(100.0 * wins / total, 2),
        "win_rate_net": round(100.0 * wins_net / total, 2),
        "total_return_pct": round(100.0 * total_ret, 2),
        "total_return_net_pct": round(100.0 * total_ret_net, 2),
        "cagr_pct": round(100.0 * cagr, 2),
        "cagr_net_pct": round(100.0 * cagr_net, 2),
        "avg_return_pct": round(100.0 * avg_ret, 3),
        "avg_return_net_pct": round(100.0 * avg_ret_net, 3),
        "avg_gain_pct": round(100.0 * avg_gain, 3),
        "avg_loss_pct": round(100.0 * avg_loss, 3),
        "avg_gain_net_pct": round(100.0 * avg_gain_net, 3),
        "avg_loss_net_pct": round(100.0 * avg_loss_net, 3),
        "profit_factor": round(float(profit_factor), 3) if profit_factor != float("inf") else "inf",
        "profit_factor_net": round(float(profit_factor_net), 3) if profit_factor_net != float("inf") else "inf",
        "max_drawdown_pct": round(100.0 * max_dd, 2),
        "max_drawdown_net_pct": round(100.0 * max_dd, 2),
        "portfolio_enabled": True,
        "portfolio_summary": {
            "capacity_filtered": int(diagnostics.get("capital_filtered", 0)),
            "avg_concurrent": round(
                (sum(concurrent_samples) / len(concurrent_samples)) if concurrent_samples else 0.0,
                3,
            ),
            "peak_concurrent": int(peak_concurrent),
            "risk_sized_count": 0,
            "fixed_sized_count": int(diagnostics.get("entries", 0)),
            "starting_equity": float(starting_equity),
            "ending_equity": float(ending_equity),
        },
        "avg_holding_days": HOLD_DAYS,
        "trailing_stop_pct": round(100.0 * stop_pct_base, 2),
        "adaptive_stop_enabled": bool(adaptive_stop_enabled),
        "adaptive_guardrails_enabled": bool(adaptive_guardrail_policy is not None),
        "slippage_bps_per_side": float(slippage_bps_per_side),
        "fee_per_share": float(fee_per_share),
        "min_fee_per_order": float(min_fee_per_order),
        "max_adv_participation": float(max_adv_participation),
        "diagnostics": diagnostics,
        "quality_gates_mode": quality_mode,
        "prediction_market_mode": prediction_market_mode,
        "prediction_market_provider": prediction_market_provider,
        "intelligence_overlay": overlay_cfg.as_dict(),
        "excluded_tickers": context.excluded_tickers[:50],
        "excluded_count": len(context.excluded_tickers),
        "universe_size": len(context.watchlist),
        "data_integrity": context.data_integrity,
        "trades_sample": all_trades[:5],
        "findings": findings,
    }
    if include_all_trades:
        out["trades"] = all_trades
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    print("Running 10-year live-parity backtest using env/config watchlist...")
    result = run_backtest(start_date=(datetime.now() - timedelta(days=3652)).strftime("%Y-%m-%d"))
    print("\n--- BACKTEST RESULTS ---")
    for k, v in result.items():
        if k not in {"trades_sample", "excluded_tickers", "findings"}:
            print(f"  {k}: {v}")
    print("\n--- FINDINGS ---")
    print(result.get("findings", ""))
    if result.get("trades_sample"):
        print("\nSample trades:", result["trades_sample"][:3])


if __name__ == "__main__":
    main()
