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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("Install yfinance: pip install yfinance")
    raise

from config import (
    get_adaptive_stop_base_pct,
    get_adaptive_stop_enabled,
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
from signal_scanner import _evaluate_quality_gates, _load_watchlist
from stage_analysis import add_indicators, check_vcp_volume, compute_signal_components, is_stage_2

SKILL_DIR = Path(__file__).resolve().parent
LOG = logging.getLogger(__name__)

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
    for attempt in range(3):
        try:
            t = yf.Ticker(symbol)
            raw = t.history(start=start_date, end=end_date, auto_adjust=True)
            time.sleep(0.05)
            return _normalize_history(raw)
        except Exception as e:
            msg = str(e)
            if "Too Many Requests" in msg and attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            LOG.warning("History fetch failed for %s: %s", symbol, e)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).rename_axis("date")


def _prepare_context(
    start_date: str,
    end_date: str,
    watchlist: list[str] | None = None,
) -> BacktestContext:
    from sector_strength import SECTOR_ETFS, get_ticker_sector_etf

    universe = watchlist if watchlist is not None else _load_watchlist(SKILL_DIR)
    cleaned = [str(t).strip().upper() for t in universe if str(t).strip()]
    universe = list(dict.fromkeys(cleaned))
    price_data: dict[str, pd.DataFrame] = {}
    sector_etf_by_ticker: dict[str, str | None] = {}
    excluded: list[dict[str, Any]] = []

    for ticker in universe:
        df = _fetch_history(ticker, start_date, end_date)
        if df.empty or len(df) < MIN_BARS:
            excluded.append({"ticker": ticker, "reason": "insufficient_history", "bars": len(df)})
            continue
        price_data[ticker] = add_indicators(df)
        try:
            sector_etf_by_ticker[ticker] = get_ticker_sector_etf(ticker, skill_dir=SKILL_DIR)
        except Exception:
            sector_etf_by_ticker[ticker] = None

    sector_perf: dict[str, pd.DataFrame] = {}
    for sym in sorted(set(SECTOR_ETFS + ["SPY"])):
        sdf = _fetch_history(sym, start_date, end_date)
        if not sdf.empty and len(sdf) >= MIN_BARS:
            sector_perf[sym] = sdf

    return BacktestContext(
        watchlist=sorted(price_data.keys()),
        price_data=price_data,
        sector_etf_by_ticker=sector_etf_by_ticker,
        sector_perf=sector_perf,
        excluded_tickers=excluded,
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


def _run_mirofish_for_entry(ticker: str, seeded_df: pd.DataFrame) -> dict[str, Any] | None:
    if os.environ.get("BACKTEST_SKIP_MIROFISH", "").strip().lower() in ("1", "true", "yes"):
        return None
    try:
        from engine_analysis import MarketSimulation

        sim = MarketSimulation(ticker, seed_df=seeded_df, skill_dir=SKILL_DIR)
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


def _simulate_exit(df: pd.DataFrame, entry_idx: int, hold_days: int, stop_pct: float) -> tuple[float, pd.Timestamp, str]:
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


def _resolve_stop_pct_for_entry(df: pd.DataFrame, entry_idx: int) -> float:
    from config import (
        get_adaptive_stop_atr_mult,
        get_adaptive_stop_base_pct,
        get_adaptive_stop_enabled,
        get_adaptive_stop_max_pct,
        get_adaptive_stop_min_pct,
        get_adaptive_stop_trend_lookback,
    )

    base_pct = float(get_adaptive_stop_base_pct(SKILL_DIR))
    if not get_adaptive_stop_enabled(SKILL_DIR):
        return max(0.01, base_pct)
    min_pct = float(get_adaptive_stop_min_pct(SKILL_DIR))
    max_pct = float(get_adaptive_stop_max_pct(SKILL_DIR))
    atr_mult = float(get_adaptive_stop_atr_mult(SKILL_DIR))
    lookback = max(10, int(get_adaptive_stop_trend_lookback(SKILL_DIR)))

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


def _max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = (1.0 + returns).cumprod()
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return float(dd.min())


def _quality_mode_should_filter(reasons: list[str]) -> bool:
    if not reasons:
        return False
    if "weak_breakout_volume" in reasons:
        return True
    mode = get_quality_gates_mode(SKILL_DIR)
    if mode in {"off", "shadow"}:
        return False
    if mode == "hard":
        return True
    soft_min = max(1, int(get_quality_soft_min_reasons(SKILL_DIR)))
    return len(reasons) >= soft_min


def run_backtest(
    tickers: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    slippage_bps_per_side: float = DEFAULT_SLIPPAGE_BPS_PER_SIDE,
    fee_per_share: float = DEFAULT_FEE_PER_SHARE,
    min_fee_per_order: float = DEFAULT_MIN_FEE_PER_ORDER,
    max_adv_participation: float = DEFAULT_MAX_ADV_PARTICIPATION,
) -> dict[str, Any]:
    end = end_date or datetime.now().strftime("%Y-%m-%d")
    start = start_date or (datetime.now() - timedelta(days=3652)).strftime("%Y-%m-%d")

    requested = [str(t).strip().upper() for t in (tickers or []) if str(t).strip()]
    context = _prepare_context(start, end, watchlist=requested if requested else None)

    all_trades: list[dict[str, Any]] = []
    diagnostics: dict[str, int] = {
        "stage2_fail": 0,
        "vcp_fail": 0,
        "breakout_not_confirmed": 0,
        "sector_not_winning": 0,
        "quality_gates_filtered": 0,
        "forensic_filtered": 0,
        "regime_blocked": 0,
        "entries": 0,
        "liquidity_filtered": 0,
    }

    breakout_enabled = get_breakout_confirm_enabled(SKILL_DIR)
    quality_mode = get_quality_gates_mode(SKILL_DIR)
    adaptive_stop_enabled = get_adaptive_stop_enabled(SKILL_DIR)
    stop_pct_base = float(get_adaptive_stop_base_pct(SKILL_DIR))
    forensic_enabled = get_forensic_enabled(SKILL_DIR)
    forensic_mode = get_forensic_filter_mode(SKILL_DIR)
    forensic_cache_hours = float(get_forensic_cache_hours(SKILL_DIR))
    forensic_sloan_max = float(get_forensic_sloan_max(SKILL_DIR))
    forensic_beneish_max = float(get_forensic_beneish_max(SKILL_DIR))
    forensic_altman_min = float(get_forensic_altman_min(SKILL_DIR))
    pead_enabled = get_pead_enabled(SKILL_DIR)
    pead_lookback_days = int(get_pead_lookback_days(SKILL_DIR))

    # Pre-compute SPY regime (above 200 SMA) for each date
    spy_df = context.sector_perf.get("SPY")
    spy_regime: pd.Series | None = None
    if spy_df is not None and len(spy_df) >= 200:
        spy_with_sma = add_indicators(spy_df)
        spy_regime = spy_with_sma["close"] > spy_with_sma["sma_200"]

    for ticker in context.watchlist:
        df = context.price_data.get(ticker)
        if df is None or df.empty:
            continue
        forensic_snapshot: dict[str, Any] | None = None

        i = 200
        while i < len(df) - 1:
            # Regime gate: skip entry if SPY is below its 200 SMA on this date
            if spy_regime is not None:
                entry_date = df.index[i]
                try:
                    spy_i = spy_regime.index.get_indexer([entry_date], method="pad")[0]
                    if spy_i >= 0 and not spy_regime.iloc[spy_i]:
                        diagnostics["regime_blocked"] += 1
                        i += 1
                        continue
                except Exception:
                    pass

            window = df.iloc[: i + 1].copy()
            if not is_stage_2(window, SKILL_DIR):
                diagnostics["stage2_fail"] += 1
                i += 1
                continue
            if not check_vcp_volume(window, SKILL_DIR):
                diagnostics["vcp_fail"] += 1
                i += 1
                continue
            if breakout_enabled and i >= 1 and float(df["close"].iloc[i]) < float(df["high"].iloc[i - 1]):
                diagnostics["breakout_not_confirmed"] += 1
                i += 1
                continue

            sector_ok, sector_reason = _sector_filter_pass(ticker, i, context)
            if not sector_ok:
                if sector_reason == "sector_not_winning":
                    diagnostics["sector_not_winning"] += 1
                i += 1
                continue

            if forensic_enabled and forensic_mode != "off":
                try:
                    if forensic_snapshot is None:
                        from forensic_accounting import compute_forensic_snapshot

                        forensic_snapshot = compute_forensic_snapshot(
                            ticker,
                            skill_dir=SKILL_DIR,
                            cache_hours=forensic_cache_hours,
                            sloan_max=forensic_sloan_max,
                            beneish_max=forensic_beneish_max,
                            altman_min=forensic_altman_min,
                        )
                    forensic_flags = list((forensic_snapshot or {}).get("forensic_flags", []) or [])
                    if forensic_mode == "hard" and forensic_flags:
                        diagnostics["forensic_filtered"] += 1
                        i += 1
                        continue
                except Exception as e:
                    LOG.debug("Backtest forensic check skipped for %s: %s", ticker, e)

            miro = _run_mirofish_for_entry(ticker, window)
            comps = compute_signal_components(
                window,
                mirofish_conviction=miro.get("conviction_score") if miro else None,
                mirofish_result=miro,
            )
            signal = {
                "ticker": ticker,
                "signal_score": float(comps.get("score", 0) or 0),
                "latest_volume": float(window["volume"].iloc[-1]),
                "avg_vol_50": float(window["avg_vol_50"].iloc[-1]),
                "mirofish_result": miro,
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
                        df.index[i],
                        df=window,
                        lookback_days=pead_lookback_days,
                    )
                except Exception as e:
                    LOG.debug("Backtest PEAD check skipped for %s: %s", ticker, e)
            signal["pead_surprise_pct"] = (pead_info or {}).get("surprise_pct")
            signal["pead_beat"] = (pead_info or {}).get("beat")
            reasons = _evaluate_quality_gates(signal, SKILL_DIR)
            if _quality_mode_should_filter(reasons):
                diagnostics["quality_gates_filtered"] += 1
                i += 1
                continue

            entry_price = float(df["close"].iloc[i])
            entry_date = df.index[i]
            day_volume = float(df["volume"].iloc[i]) if "volume" in df.columns else 0.0
            qty = _estimate_order_qty(entry_price, day_volume, max_adv_participation=max_adv_participation)
            if day_volume > 0 and qty > int(day_volume * max_adv_participation):
                diagnostics["liquidity_filtered"] += 1
                i += 1
                continue
            stop_pct_entry = _resolve_stop_pct_for_entry(df, i)
            exit_price, exit_date, exit_reason = _simulate_exit(df, i, HOLD_DAYS, stop_pct_entry)
            ret = (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0
            net_ret, cost_ctx = _net_return_after_costs(
                entry_price=entry_price,
                exit_price=exit_price,
                qty=qty,
                slippage_bps_per_side=slippage_bps_per_side,
                fee_per_share=fee_per_share,
                min_fee_per_order=min_fee_per_order,
            )

            all_trades.append(
                {
                    "ticker": ticker,
                    "entry_date": pd.Timestamp(entry_date).isoformat(),
                    "exit_date": pd.Timestamp(exit_date).isoformat(),
                    "entry_price": round(entry_price, 4),
                    "exit_price": round(exit_price, 4),
                    "return": float(ret),
                    "net_return": float(net_ret),
                    "exit_reason": exit_reason,
                    "signal_score": signal["signal_score"],
                    "mirofish_conviction": miro.get("conviction_score") if miro else None,
                    "sector_filter": sector_reason,
                    "quality_reasons": reasons,
                    "forensic_sloan": signal.get("forensic_sloan"),
                    "forensic_beneish": signal.get("forensic_beneish"),
                    "forensic_altman": signal.get("forensic_altman"),
                    "forensic_flags": signal.get("forensic_flags"),
                    "pead_beat": signal.get("pead_beat"),
                    "pead_surprise_pct": signal.get("pead_surprise_pct"),
                    "qty_estimate": int(qty),
                    "day_volume": float(day_volume),
                    "slippage_pct": float(cost_ctx["slippage_pct"]),
                    "fees_pct": float(cost_ctx["fees_pct"]),
                    "stop_pct": float(stop_pct_entry),
                }
            )
            diagnostics["entries"] += 1
            i += HOLD_DAYS

    if not all_trades:
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
            "diagnostics": diagnostics,
            "quality_gates_mode": quality_mode,
            "excluded_tickers": context.excluded_tickers[:50],
            "universe_size": len(context.watchlist),
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
    total_ret = float((1.0 + ret).prod() - 1.0)
    total_ret_net = float((1.0 + ret_net).prod() - 1.0)
    years = max(1e-9, (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25)
    cagr = float((1.0 + total_ret) ** (1.0 / years) - 1.0) if total_ret > -1.0 else -1.0
    cagr_net = float((1.0 + total_ret_net) ** (1.0 / years) - 1.0) if total_ret_net > -1.0 else -1.0
    max_dd = _max_drawdown(ret)
    max_dd_net = _max_drawdown(ret_net)

    findings = (
        f"Live-parity backtest generated {total} trades across {len(context.watchlist)} symbols. "
        f"Gross win rate {100.0 * wins / total:.1f}%, net win rate {100.0 * wins_net / total:.1f}%, "
        f"gross return {100.0 * total_ret:.2f}%, net return {100.0 * total_ret_net:.2f}%, "
        f"gross CAGR {100.0 * cagr:.2f}%, net CAGR {100.0 * cagr_net:.2f}%."
    )

    return {
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
        "max_drawdown_net_pct": round(100.0 * max_dd_net, 2),
        "avg_holding_days": HOLD_DAYS,
        "trailing_stop_pct": round(100.0 * stop_pct_base, 2),
        "adaptive_stop_enabled": bool(adaptive_stop_enabled),
        "slippage_bps_per_side": float(slippage_bps_per_side),
        "fee_per_share": float(fee_per_share),
        "min_fee_per_order": float(min_fee_per_order),
        "max_adv_participation": float(max_adv_participation),
        "diagnostics": diagnostics,
        "quality_gates_mode": quality_mode,
        "excluded_tickers": context.excluded_tickers[:50],
        "excluded_count": len(context.excluded_tickers),
        "universe_size": len(context.watchlist),
        "trades_sample": all_trades[:5],
        "findings": findings,
    }


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
