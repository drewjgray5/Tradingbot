"""
Signal scanner: find Stage 2 + VCP + winning sector setups.
Sends Discord notification with prompt to execute.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
QUALITY_METRICS_FILE = ".signal_quality_metrics.json"
MACRO_BLACKOUT_FILE = ".macro_event_blackouts.json"

# Default watchlist: every sector, liquid names. Override via SIGNAL_WATCHLIST in .env
DEFAULT_WATCHLIST = [
    # Technology (XLK)
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "AVGO", "ADBE", "CRM",
    "CSCO", "ORCL", "IBM", "AMD", "INTC", "QCOM", "TXN", "AMAT", "NOW", "INTU",
    "ADI", "LRCX", "KLAC", "SNPS", "CDNS", "MRVL", "NXPI", "MU",
    # Financial Services (XLF)
    "JPM", "BAC", "WFC", "GS", "MS", "C", "AXP", "V", "MA", "SPGI", "BLK",
    "SCHW", "CB", "PGR", "MMC", "ICE", "CME", "AON", "AJG", "COF",
    # Energy (XLE)
    "XOM", "CVX", "COP", "SLB", "EOG", "PXD", "MPC", "VLO", "OXY", "PSX",
    "DVN", "HES", "FANG", "HAL", "KMI", "WMB", "OKE",
    # Healthcare (XLV)
    "UNH", "JNJ", "PFE", "ABBV", "MRK", "LLY", "TMO", "ABT", "DHR", "BMY",
    "AMGN", "GILD", "MDT", "SYK", "ISRG", "ZTS", "VRTX", "REGN", "IQV",
    "ELV", "CI", "HUM", "MCK", "CAH", "BSX",
    # Consumer Discretionary (XLY)
    "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "TJX", "BKNG", "ORLY", "AZO",
    "DHI", "LEN", "NVR", "F", "GM", "YUM", "CMG", "DPZ", "MAR", "HLT",
    "WYNN", "LVS", "EBAY", "ETSY", "ROST", "DRI",
    # Consumer Staples (XLP)
    "PG", "KO", "PEP", "COST", "WMT", "PM", "MO", "CL", "MDLZ", "KMB",
    "GIS", "K", "SJM", "HSY", "STZ", "BF.B", "TAP", "KHC", "CPB", "MKC",
    # Materials (XLB)
    "LIN", "APD", "SHW", "ECL", "NEM", "FCX", "NUE", "VMC", "DOW", "PPG",
    "DD", "CE", "FMC", "CF", "MOS", "ALB", "LYB", "EMN",
    # Industrials (XLI)
    "UNP", "HON", "UPS", "RTX", "CAT", "GE", "LMT", "BA", "DE", "MMM",
    "WM", "RSG", "FDX", "LUV", "DAL", "NOC", "GD", "PH", "ROK", "ITW",
    "EMR", "ETN", "CARR", "OTIS", "JCI", "PCAR", "CTAS", "FAST",
    # Utilities (XLU)
    "NEE", "DUK", "SO", "D", "AEP", "SRE", "EXC", "XEL", "ED", "WEC",
    "AWK", "ES", "EVRG", "DTE", "AEE", "CMS", "EIX", "PEG", "CEG",
    # Communication Services (XLC)
    "NFLX", "DIS", "CMCSA", "VZ", "T", "TMUS", "CHTR", "EA", "TTWO",
    "WBD", "PARA", "LYV", "MTCH", "FOXA", "FOX",
    # Real Estate (XLRE)
    "PLD", "AMT", "EQIX", "PSA", "O", "WELL", "SPG", "DLR", "CCI",
    "SBAC", "AVB", "EQR", "VTR", "ARE", "VICI", "MAA",
]


def _maybe_prefilter_watchlist(skill_dir: Path, watchlist: list[str]) -> list[str]:
    try:
        from config import (
            get_quality_watchlist_prefilter_enabled,
            get_quality_watchlist_prefilter_max,
        )
        if get_quality_watchlist_prefilter_enabled(skill_dir):
            from watchlist_loader import prefilter_watchlist
            pre_max = get_quality_watchlist_prefilter_max(skill_dir)
            before = len(watchlist)
            watchlist = prefilter_watchlist(watchlist, max_tickers=pre_max, include_etf_hints=True)
            LOG.info("Watchlist quality prefilter applied: %d -> %d tickers", before, len(watchlist))
    except Exception as e:
        LOG.debug("Watchlist prefilter skipped: %s", e)
    return watchlist


def _apply_universe_focus(skill_dir: Path, watchlist: list[str]) -> list[str]:
    try:
        from config import get_signal_universe_mode, get_signal_universe_target_size
        from watchlist_loader import prefilter_watchlist

        mode = get_signal_universe_mode(skill_dir)
        if mode != "focused":
            return watchlist
        target = max(20, int(get_signal_universe_target_size(skill_dir)))
        before = len(watchlist)
        focused = prefilter_watchlist(watchlist, max_tickers=target, include_etf_hints=True)
        LOG.info("Universe focus applied: %d -> %d tickers (mode=%s)", before, len(focused), mode)
        return focused
    except Exception as e:
        LOG.debug("Universe focus skipped: %s", e)
        return watchlist


def _load_watchlist(skill_dir: Path) -> list[str]:
    """
    Load watchlist:
    - If SIGNAL_WATCHLIST is set in .env (non-empty), it overrides everything else (custom list).
    - Else if USE_STATIC_WATCHLIST is true: DEFAULT_WATCHLIST (~sector basket).
    - Else: watchlist_loader.load_full_watchlist() (S&P 500 + 400 + 600 + Russell 2000 + IWM),
      refreshed from source at least once per UTC day. When SIGNAL_SCAN_FULL_UNIVERSE is true
      (default), that dynamic list is not shortened by quality prefilter or focused universe.
    """
    import os

    env_path = skill_dir / ".env"
    use_static = os.environ.get("USE_STATIC_WATCHLIST", "").strip().lower() in ("1", "true", "yes")
    custom: list[str] | None = None
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("SIGNAL_WATCHLIST=") and custom is None:
                val = line.split("=", 1)[1].strip().strip('"\'')
                if val:
                    custom = [s.strip().upper() for s in val.split(",") if s.strip()]
            if line.startswith("USE_STATIC_WATCHLIST="):
                use_static = line.split("=", 1)[1].strip().lower() in ("1", "true", "yes")
    if custom is not None:
        custom = _maybe_prefilter_watchlist(skill_dir, custom)
        LOG.info("Watchlist mode=custom (SIGNAL_WATCHLIST) tickers=%d", len(custom))
        return custom
    if use_static:
        static_wl = _maybe_prefilter_watchlist(skill_dir, DEFAULT_WATCHLIST)
        static_wl = _apply_universe_focus(skill_dir, static_wl)
        LOG.info("Watchlist mode=static (USE_STATIC_WATCHLIST) tickers=%d", len(static_wl))
        return static_wl
    from config import get_signal_scan_full_universe
    from watchlist_loader import load_full_watchlist

    wl = load_full_watchlist()
    if get_signal_scan_full_universe(skill_dir):
        LOG.info(
            "Watchlist mode=full (watchlist_loader: S&P500+400+600+Russell2000+IWM) tickers=%d "
            "(SIGNAL_SCAN_FULL_UNIVERSE: no prefilter/focus)",
            len(wl),
        )
        return wl
    wl = _maybe_prefilter_watchlist(skill_dir, wl)
    wl = _apply_universe_focus(skill_dir, wl)
    LOG.info(
        "Watchlist mode=full (watchlist_loader: S&P500+400+600+Russell2000+IWM) tickers=%d",
        len(wl),
    )
    return wl


def _quality_metrics_path(skill_dir: Path) -> Path:
    return skill_dir / QUALITY_METRICS_FILE


def _load_quality_metrics(skill_dir: Path) -> dict[str, Any]:
    path = _quality_metrics_path(skill_dir)
    if not path.exists():
        return {"days": {}}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and isinstance(data.get("days"), dict):
            return data
    except Exception:
        pass
    return {"days": {}}


def _save_quality_metrics(skill_dir: Path, data: dict[str, Any]) -> None:
    path = _quality_metrics_path(skill_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _record_quality_snapshot(skill_dir: Path, diagnostics: dict[str, Any], signals: list[dict[str, Any]]) -> None:
    today = date.today().isoformat()
    data = _load_quality_metrics(skill_dir)
    days = data.setdefault("days", {})
    day_bucket = days.setdefault(today, {"scans": []})

    avg_score = round(
        sum(float(s.get("signal_score", 0) or 0) for s in signals) / len(signals), 2
    ) if signals else 0.0
    avg_conv = round(
        sum(float(s.get("mirofish_conviction", 0) or 0) for s in signals) / len(signals), 2
    ) if signals else 0.0
    day_bucket.setdefault("scans", []).append({
        "signals_found": len(signals),
        "avg_score": avg_score,
        "avg_conviction": avg_conv,
        "diagnostics": diagnostics,
    })

    cutoff = date.today() - timedelta(days=45)
    stale = [k for k in days.keys() if k < cutoff.isoformat()]
    for k in stale:
        days.pop(k, None)
    _save_quality_metrics(skill_dir, data)


def get_signal_quality_summary(skill_dir: Path | str | None = None, days: int = 7) -> dict[str, Any]:
    skill_dir = Path(skill_dir or SKILL_DIR)
    data = _load_quality_metrics(skill_dir)
    day_map = data.get("days", {})
    keys = sorted(day_map.keys())[-max(1, int(days)) :]

    total_scans = 0
    total_signals = 0
    score_sum = 0.0
    conv_sum = 0.0
    diag_sum: dict[str, int] = {}
    for k in keys:
        scans = (day_map.get(k, {}) or {}).get("scans", [])
        for scan in scans:
            total_scans += 1
            total_signals += int(scan.get("signals_found", 0) or 0)
            score_sum += float(scan.get("avg_score", 0) or 0)
            conv_sum += float(scan.get("avg_conviction", 0) or 0)
            for dk, dv in (scan.get("diagnostics", {}) or {}).items():
                if isinstance(dv, (int, float)):
                    diag_sum[dk] = diag_sum.get(dk, 0) + int(dv)

    return {
        "window_days": max(1, int(days)),
        "scan_count": total_scans,
        "signals_total": total_signals,
        "avg_signal_score": round(score_sum / total_scans, 2) if total_scans else 0.0,
        "avg_conviction": round(conv_sum / total_scans, 2) if total_scans else 0.0,
        "diagnostics": diag_sum,
    }


def _load_macro_blackout_dates(skill_dir: Path) -> set[str]:
    import os

    dates: set[str] = set()
    raw = os.environ.get("EVENT_MACRO_BLACKOUT_DATES", "").strip()
    if raw:
        for token in raw.split(","):
            t = token.strip()
            if t:
                dates.add(t)
    path = skill_dir / MACRO_BLACKOUT_FILE
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                vals = data.get("dates", [])
            elif isinstance(data, list):
                vals = data
            else:
                vals = []
            for item in vals:
                if isinstance(item, str) and item.strip():
                    dates.add(item.strip())
        except Exception:
            pass
    return dates


def _nearest_earnings_distance_days(ticker: str) -> int | None:
    try:
        import pandas as pd
        import yfinance as yf

        df = yf.Ticker(str(ticker or "").upper()).earnings_dates
        if not isinstance(df, pd.DataFrame) or df.empty:
            return None
        idx = pd.to_datetime(df.index, errors="coerce")
        idx = idx[~idx.isna()]
        if len(idx) == 0:
            return None
        idx = idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx
        today = pd.Timestamp.now(tz=None).normalize()
        diffs = [abs((d.normalize() - today).days) for d in idx]
        return min(diffs) if diffs else None
    except Exception:
        return None


def evaluate_event_risk_policy(
    ticker: str,
    skill_dir: Path | str | None = None,
) -> dict[str, Any]:
    skill_dir = Path(skill_dir or SKILL_DIR)
    try:
        from config import (
            get_event_action,
            get_event_block_earnings_days,
            get_event_downsize_factor,
            get_event_macro_blackout_enabled,
            get_event_risk_mode,
        )

        mode = str(get_event_risk_mode(skill_dir) or "off").strip().lower()
        earnings_days = int(get_event_block_earnings_days(skill_dir))
        macro_enabled = bool(get_event_macro_blackout_enabled(skill_dir))
        action = str(get_event_action(skill_dir) or "block").strip().lower()
        downsize_factor = float(get_event_downsize_factor(skill_dir))
    except Exception:
        mode = "off"
        earnings_days = 2
        macro_enabled = False
        action = "block"
        downsize_factor = 0.5

    earnings_distance = _nearest_earnings_distance_days(ticker)
    earnings_near = earnings_distance is not None and earnings_distance <= max(0, earnings_days)

    macro_blackout = False
    if macro_enabled:
        today_key = date.today().isoformat()
        macro_blackout = today_key in _load_macro_blackout_dates(skill_dir)

    reasons: list[str] = []
    if earnings_near:
        reasons.append(f"earnings_within_{earnings_days}d")
    if macro_blackout:
        reasons.append("macro_blackout")

    return {
        "mode": mode,
        "action": action if action in {"block", "downsize"} else "block",
        "downsize_factor": max(0.10, min(1.0, downsize_factor)),
        "earnings_distance_days": earnings_distance,
        "earnings_near": earnings_near,
        "macro_blackout": macro_blackout,
        "flagged": bool(reasons),
        "reasons": reasons,
    }


def _apply_event_risk_policy_to_signals(
    signals: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    skill_dir: Path,
) -> list[dict[str, Any]]:
    if not signals:
        return signals
    out: list[dict[str, Any]] = []
    for sig in signals:
        ticker = str(sig.get("ticker") or "")
        policy = evaluate_event_risk_policy(ticker, skill_dir=skill_dir)
        mode = policy.get("mode", "off")
        action = policy.get("action", "block")
        flagged = bool(policy.get("flagged"))
        if not flagged:
            out.append(sig)
            continue

        diagnostics["event_risk_flagged"] = int(diagnostics.get("event_risk_flagged", 0) or 0) + 1
        enriched = dict(sig)
        enriched["event_risk"] = policy

        if mode == "live" and action == "block":
            diagnostics["event_risk_blocked"] = int(diagnostics.get("event_risk_blocked", 0) or 0) + 1
            continue
        if (mode == "live" and action == "downsize") or (mode == "shadow" and action == "downsize"):
            diagnostics["event_risk_downsized"] = int(diagnostics.get("event_risk_downsized", 0) or 0) + 1
            enriched["event_risk"]["shadow_action"] = "would_downsize" if mode == "shadow" else "downsize"
        elif mode == "shadow" and action == "block":
            diagnostics["event_risk_blocked"] = int(diagnostics.get("event_risk_blocked", 0) or 0) + 1
            enriched["event_risk"]["shadow_action"] = "would_block"
        out.append(enriched)
    return out


def _evaluate_quality_gates(signal: dict[str, Any], skill_dir: Path) -> list[str]:
    from config import (
        get_forensic_altman_min,
        get_forensic_beneish_max,
        get_forensic_filter_mode,
        get_forensic_sloan_max,
        get_quality_breakout_volume_min_ratio,
        get_quality_min_signal_score,
        get_quality_require_breakout_volume,
    )
    reasons: list[str] = []
    score = float(signal.get("signal_score", 0) or 0)
    if score < float(get_quality_min_signal_score(skill_dir)):
        reasons.append("low_signal_score")

    # continuation_prob / bull_trap_prob removed: already baked into signal_score
    # via MiroFish conviction. Separate thresholds were double-counting the same signal.

    if get_quality_require_breakout_volume(skill_dir):
        latest_vol = signal.get("latest_volume")
        avg_vol = signal.get("avg_vol_50")
        min_ratio = signal.get("breakout_volume_min_ratio")
        if min_ratio is None:
            min_ratio = get_quality_breakout_volume_min_ratio(skill_dir)
        try:
            if (
                latest_vol is None
                or avg_vol is None
                or float(avg_vol) <= 0
                or (float(latest_vol) / float(avg_vol)) < float(min_ratio)
            ):
                reasons.append("weak_breakout_volume")
        except (TypeError, ValueError):
            reasons.append("weak_breakout_volume")

    forensic_mode = get_forensic_filter_mode(skill_dir)
    if forensic_mode != "off":
        sloan_val = signal.get("forensic_sloan")
        beneish_val = signal.get("forensic_beneish")
        altman_val = signal.get("forensic_altman")
        if sloan_val is not None:
            try:
                if float(sloan_val) > float(get_forensic_sloan_max(skill_dir)):
                    reasons.append("forensic_sloan_high")
            except (TypeError, ValueError):
                pass
        if beneish_val is not None:
            try:
                if float(beneish_val) > float(get_forensic_beneish_max(skill_dir)):
                    reasons.append("forensic_beneish_manipulator")
            except (TypeError, ValueError):
                pass
        if altman_val is not None:
            try:
                if float(altman_val) < float(get_forensic_altman_min(skill_dir)):
                    reasons.append("forensic_altman_distress")
            except (TypeError, ValueError):
                pass

    pead_surprise = signal.get("pead_surprise_pct")
    pead_beat = signal.get("pead_beat")
    if pead_surprise is not None:
        try:
            if pead_beat is False and float(pead_surprise) < -0.05:
                reasons.append("pead_negative_surprise")
        except (TypeError, ValueError):
            pass
    return reasons


def _quality_mode_should_filter(reasons: list[str], skill_dir: Path) -> bool:
    from config import (
        get_forensic_filter_mode,
        get_quality_gates_mode,
        get_quality_soft_min_reasons,
    )

    if not reasons:
        return False
    # Breakout volume is a hard gate: always filter regardless of mode
    if "weak_breakout_volume" in reasons:
        return True
    forensic_reasons = {
        "forensic_sloan_high",
        "forensic_beneish_manipulator",
        "forensic_altman_distress",
    }
    if any(r in forensic_reasons for r in reasons):
        forensic_mode = get_forensic_filter_mode(skill_dir)
        if forensic_mode == "hard":
            return True
        if forensic_mode in {"off", "shadow"}:
            reasons = [r for r in reasons if r not in forensic_reasons]
            if not reasons:
                return False
    mode = get_quality_gates_mode(skill_dir)
    if mode in {"off", "shadow"}:
        return False
    if mode == "hard":
        return True
    soft_min = max(1, int(get_quality_soft_min_reasons(skill_dir)))
    return len(reasons) >= soft_min


def _sec_score_hint_delta(risk_tag: str, filing_recency_days: int | None) -> float:
    """
    Small bounded adjustment from SEC context.
    Positive for low-risk/fresh filings, negative for high-risk recent events.
    """
    tag = (risk_tag or "unknown").strip().lower()
    recency = filing_recency_days if isinstance(filing_recency_days, int) else None
    if tag == "high":
        return -3.0 if recency is not None and recency <= 14 else -2.0
    if tag == "medium":
        return -1.0 if recency is not None and recency <= 10 else 0.0
    if tag == "low":
        return 1.0 if recency is not None and recency <= 30 else 0.0
    return 0.0


def _compute_stage_a_shortlist_limit(
    total_candidates: int,
    top_n: int,
    multiplier: float,
    cap: int,
) -> int:
    if total_candidates <= 0:
        return 0
    base = top_n if top_n > 0 else total_candidates
    widened = max(base, int(round(float(base) * max(1.0, float(multiplier)))))
    if cap > 0:
        widened = min(widened, int(cap))
    return max(1, min(total_candidates, widened))


def _scan_stage_a_one(
    ticker: str,
    auth: Any,
    winning_etfs: set[str],
    skill_dir: Path,
    breakout_enabled: bool,
    breakout_min_time: int,
) -> dict[str, Any]:
    from market_data import extract_schwab_last_price, get_current_quote, get_daily_history
    from sector_strength import get_ticker_sector_etf
    from stage_analysis import (
        add_indicators,
        check_vcp_volume,
        compute_signal_components,
        is_stage_2,
    )

    try:
        df = get_daily_history(ticker, days=300, auth=auth, skill_dir=skill_dir)
        if df.empty:
            return {"ok": False, "reason": "df_empty"}
        if len(df) < 50:
            return {"ok": False, "reason": "too_few_candles"}
        df = add_indicators(df)
        if not is_stage_2(df, skill_dir):
            return {"ok": False, "reason": "stage2_fail"}
        if not check_vcp_volume(df, skill_dir):
            return {"ok": False, "reason": "vcp_fail"}

        sector_etf = get_ticker_sector_etf(ticker, skill_dir=skill_dir)
        if sector_etf is None:
            return {"ok": False, "reason": "no_sector_etf"}
        if sector_etf not in winning_etfs:
            return {"ok": False, "reason": "sector_not_winning"}

        quote = get_current_quote(ticker, auth=auth, skill_dir=skill_dir)
        price = float(df["close"].iloc[-1])
        live = extract_schwab_last_price(quote) if isinstance(quote, dict) else None
        if live is not None:
            price = live

        prior_high = (
            float(df["high"].iloc[-2])
            if len(df) >= 2
            else float(df["high"].iloc[-1])
            if len(df) >= 1
            else price
        )
        if breakout_enabled:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            now_et = datetime.now(ZoneInfo("America/New_York"))
            current_minutes = now_et.hour * 60 + now_et.minute
            if current_minutes >= breakout_min_time and price < prior_high:
                return {"ok": False, "reason": "breakout_not_confirmed"}

        components = compute_signal_components(df)
        stage_a_score = float(components.get("score", 0) or 0)
        candidate = {
            "ticker": ticker,
            "df": df,
            "price": price,
            "sector_etf": sector_etf,
            "breakout_confirmed": bool(price >= prior_high),
            "latest_volume": float(df["volume"].iloc[-1]) if "volume" in df.columns else None,
            "avg_vol_50": float(df["avg_vol_50"].iloc[-1]) if "avg_vol_50" in df.columns else None,
            "sma_50": round(float(df["sma_50"].iloc[-1]), 2),
            "sma_200": round(float(df["sma_200"].iloc[-1]), 2),
            "stage_a_score": stage_a_score,
            "score_components_stage_a": components,
        }
        return {"ok": True, "candidate": candidate}
    except Exception as e:
        LOG.warning("Scan stage A error for %s: %s", ticker, e)
        return {"ok": False, "reason": "exceptions", "error": f"{ticker}: {e}"}


def _scan_stage_b_enrich(
    candidate: dict[str, Any],
    auth: Any,
    skill_dir: Path,
    pullback_mode: str,
    sec_enrichment_enabled: bool,
    sec_tagging_enabled: bool,
    sec_shadow_mode: bool,
    sec_score_hint_enabled: bool,
    sec_cache_hours: float,
    edgar_user_agent: str,
    forensic_enabled: bool,
    forensic_filter_mode: str,
    forensic_cache_hours: float,
    forensic_sloan_max: float,
    forensic_beneish_max: float,
    forensic_altman_min: float,
    pead_enabled: bool,
    pead_lookback_days: int,
    pead_score_boost: float,
    pead_score_boost_large: float,
    pead_score_penalty: float,
    guidance_score_enabled: bool,
    guidance_score_boost: float,
    guidance_score_penalty: float,
    sec_filing_cache_hours: float,
    sec_filing_max_chars: int,
) -> dict[str, Any]:
    from stage_analysis import compute_signal_components

    ticker = str(candidate.get("ticker") or "").upper()
    df = candidate.get("df")
    if df is None:
        return {"ok": False, "error": f"{ticker}: missing candidate dataframe", "diag": {"stage_b_exceptions": 1}}

    diag_delta: dict[str, int] = {
        "stage_b_processed": 1,
        "sec_tagged_signals": 0,
        "sec_recent_8k_count": 0,
        "sec_high_risk_tag_count": 0,
        "sec_data_failures": 0,
        "sec_score_hint_shadow_adjustments": 0,
        "sec_score_hint_applied_count": 0,
        "advisory_scored": 0,
        "advisory_high_confidence": 0,
        "advisory_medium_confidence": 0,
        "advisory_low_confidence": 0,
        "low_breakout_volume": 0,
        "weak_mirofish_alignment": 0,
        "forensic_sloan_flags": 0,
        "forensic_beneish_flags": 0,
        "forensic_altman_flags": 0,
        "pead_boosted": 0,
        "pead_penalized": 0,
        "guidance_boosted": 0,
        "stage_b_exceptions": 0,
    }
    try:
        # MiroFish simulation: use cache if fresh, else run and cache
        mirofish_result = None
        try:
            from engine_analysis import (
                MarketSimulation,
                cache_conviction,
                compute_seed_fingerprint,
                get_cached_conviction,
            )

            seed_fingerprint = compute_seed_fingerprint(df, ticker, skill_dir)
            cached = get_cached_conviction(
                ticker,
                skill_dir=skill_dir,
                max_age_hours=12,
                seed_fingerprint=seed_fingerprint,
            )
            if cached:
                mirofish_result = {
                    "conviction_score": cached.get("conviction_score"),
                    "summary": cached.get("summary"),
                    "agent_votes": cached.get("agent_votes", []),
                    "simulation_id": cached.get("simulation_id"),
                    "continuation_probability": cached.get("continuation_probability"),
                    "bull_trap_probability": cached.get("bull_trap_probability"),
                }
            else:
                sim = MarketSimulation(ticker, seed_df=df, auth=auth, skill_dir=skill_dir)
                result = sim.run()
                cache_conviction(ticker, result, skill_dir=skill_dir)
                mirofish_result = {
                    "conviction_score": result.get("conviction_score"),
                    "summary": result.get("summary"),
                    "agent_votes": result.get("agent_votes", []),
                    "simulation_id": result.get("simulation_id"),
                    "continuation_probability": result.get("continuation_probability"),
                    "bull_trap_probability": result.get("bull_trap_probability"),
                }
        except Exception as e:
            LOG.warning("MiroFish for %s: %s", ticker, e)

        components = compute_signal_components(
            df,
            mirofish_conviction=mirofish_result.get("conviction_score") if mirofish_result else None,
            mirofish_result=mirofish_result,
        )
        score = float(components["score"])
        latest_volume = candidate.get("latest_volume")
        avg_vol_50 = candidate.get("avg_vol_50")
        sec_risk_tag = "unknown"
        sec_recent_8k = False
        sec_filing_recency_days = None
        sec_risk_reasons: list[str] = []
        sec_score_hint_delta = 0.0

        if sec_enrichment_enabled and sec_tagging_enabled:
            try:
                from sec_enrichment import fetch_sec_snapshot

                sec_snapshot = fetch_sec_snapshot(
                    ticker,
                    skill_dir=skill_dir,
                    user_agent=edgar_user_agent,
                    cache_hours=sec_cache_hours,
                    enabled=True,
                )
                if sec_snapshot.get("ok"):
                    diag_delta["sec_tagged_signals"] += 1
                    sec_risk_tag = sec_snapshot.get("risk_tag", "unknown") or "unknown"
                    sec_recent_8k = bool(sec_snapshot.get("recent_8k", False))
                    sec_filing_recency_days = sec_snapshot.get("filing_recency_days")
                    sec_risk_reasons = sec_snapshot.get("risk_reasons", []) or []
                    if sec_recent_8k:
                        diag_delta["sec_recent_8k_count"] += 1
                    if sec_risk_tag == "high":
                        diag_delta["sec_high_risk_tag_count"] += 1
                    if sec_score_hint_enabled:
                        sec_score_hint_delta = _sec_score_hint_delta(sec_risk_tag, sec_filing_recency_days)
                        if sec_score_hint_delta != 0:
                            if sec_shadow_mode:
                                diag_delta["sec_score_hint_shadow_adjustments"] += 1
                            else:
                                score = max(0.0, min(100.0, score + sec_score_hint_delta))
                                diag_delta["sec_score_hint_applied_count"] += 1
                else:
                    diag_delta["sec_data_failures"] += 1
            except Exception as e:
                LOG.debug("SEC tag failed for %s: %s", ticker, e)
                diag_delta["sec_data_failures"] += 1

        forensic_snapshot = None
        forensic_flags: list[str] = []
        forensic_sloan = None
        forensic_beneish = None
        forensic_altman = None
        if forensic_enabled:
            try:
                from forensic_accounting import compute_forensic_snapshot

                forensic_snapshot = compute_forensic_snapshot(
                    ticker,
                    skill_dir=skill_dir,
                    cache_hours=forensic_cache_hours,
                    sloan_max=forensic_sloan_max,
                    beneish_max=forensic_beneish_max,
                    altman_min=forensic_altman_min,
                )
                forensic_flags = list(forensic_snapshot.get("forensic_flags", []) or [])
                sloan_payload = forensic_snapshot.get("sloan") or {}
                beneish_payload = forensic_snapshot.get("beneish") or {}
                altman_payload = forensic_snapshot.get("altman") or {}
                forensic_sloan = sloan_payload.get("sloan_ratio")
                forensic_beneish = beneish_payload.get("m_score")
                forensic_altman = altman_payload.get("z_score")
                if "sloan_high" in forensic_flags:
                    diag_delta["forensic_sloan_flags"] += 1
                if "beneish_manipulator" in forensic_flags:
                    diag_delta["forensic_beneish_flags"] += 1
                if "altman_distress" in forensic_flags:
                    diag_delta["forensic_altman_flags"] += 1
            except Exception as e:
                LOG.debug("Forensic enrichment failed for %s: %s", ticker, e)

        pead_info = None
        pead_surprise_pct = None
        pead_beat = None
        pead_score_delta = 0.0
        if pead_enabled:
            try:
                from earnings_signal import check_recent_earnings

                pead_info = check_recent_earnings(ticker, lookback_days=pead_lookback_days)
                if pead_info and pead_info.get("had_recent_earnings"):
                    pead_surprise_pct = pead_info.get("surprise_pct")
                    pead_beat = pead_info.get("beat")
                    if pead_surprise_pct is not None:
                        s = float(pead_surprise_pct)
                        if s > 0.15:
                            pead_score_delta = float(pead_score_boost_large)
                        elif s > 0.05:
                            pead_score_delta = float(pead_score_boost)
                        elif s < 0:
                            pead_score_delta = -abs(float(pead_score_penalty))
            except Exception as e:
                LOG.debug("PEAD enrichment failed for %s: %s", ticker, e)

        guidance_signal = "neutral"
        guidance_score_delta = 0.0
        if guidance_score_enabled and sec_enrichment_enabled:
            try:
                from sec_filing_compare import analyze_latest_filing_for_ticker

                filing_analysis = analyze_latest_filing_for_ticker(
                    ticker=ticker,
                    form_type="10-Q",
                    user_agent=edgar_user_agent,
                    skill_dir=skill_dir,
                    cache_hours=sec_filing_cache_hours,
                    max_chars=sec_filing_max_chars,
                    enable_llm=False,
                )
                if filing_analysis.get("ok"):
                    guidance_signal = str(filing_analysis.get("guidance_signal", "neutral") or "neutral").lower()
                    if guidance_signal == "positive":
                        guidance_score_delta = float(guidance_score_boost)
                    elif guidance_signal == "negative":
                        guidance_score_delta = -abs(float(guidance_score_penalty))
            except Exception as e:
                LOG.debug("Guidance score enrichment failed for %s: %s", ticker, e)

        total_new_delta = pead_score_delta + guidance_score_delta
        if total_new_delta != 0:
            if sec_shadow_mode or forensic_filter_mode == "shadow":
                diag_delta["sec_score_hint_shadow_adjustments"] += 1
            else:
                score = max(0.0, min(100.0, score + total_new_delta))
                if pead_score_delta > 0:
                    diag_delta["pead_boosted"] += 1
                elif pead_score_delta < 0:
                    diag_delta["pead_penalized"] += 1
                if guidance_score_delta != 0:
                    diag_delta["guidance_boosted"] += 1

        if avg_vol_50 and latest_volume and latest_volume < avg_vol_50:
            diag_delta["low_breakout_volume"] += 1
        if mirofish_result:
            bt = mirofish_result.get("bull_trap_probability")
            cont = mirofish_result.get("continuation_probability")
            try:
                if bt is not None and cont is not None and float(bt) >= float(cont):
                    diag_delta["weak_mirofish_alignment"] += 1
            except (TypeError, ValueError):
                pass

        signal_row = {
            "ticker": ticker,
            "price": round(float(candidate.get("price") or 0.0), 2),
            "sector_etf": candidate.get("sector_etf"),
            "sma_50": candidate.get("sma_50"),
            "sma_200": candidate.get("sma_200"),
            "mirofish_summary": mirofish_result.get("summary") if mirofish_result else None,
            "mirofish_conviction": mirofish_result.get("conviction_score") if mirofish_result else None,
            "mirofish_result": mirofish_result,
            "signal_score": score,
            "score_components": components,
            "latest_volume": latest_volume,
            "avg_vol_50": avg_vol_50,
            "recent_8k": sec_recent_8k,
            "filing_recency_days": sec_filing_recency_days,
            "sec_risk_tag": sec_risk_tag,
            "sec_risk_reasons": sec_risk_reasons,
            "sec_score_hint_delta": sec_score_hint_delta,
            "forensic_sloan": forensic_sloan,
            "forensic_beneish": forensic_beneish,
            "forensic_altman": forensic_altman,
            "forensic_flags": forensic_flags,
            "pead_surprise_pct": pead_surprise_pct,
            "pead_beat": pead_beat,
            "pead_score_delta": pead_score_delta,
            "guidance_signal": guidance_signal,
            "guidance_score_delta": guidance_score_delta,
            "breakout_confirmed": bool(candidate.get("breakout_confirmed")),
        }
        try:
            from strategy_plugins import build_default_strategy_plugins

            signal_row["strategy_plugins"] = build_default_strategy_plugins(
                signal=signal_row,
                candidate=candidate,
                pullback_mode=pullback_mode,
            )
        except Exception as e:
            LOG.debug("Strategy plugin evaluation skipped for %s: %s", ticker, e)

        try:
            from advisory_model import score_signal_advisory

            advisory = score_signal_advisory(signal_row, skill_dir=skill_dir)
            if advisory is not None:
                signal_row["advisory"] = advisory.to_dict()
                diag_delta["advisory_scored"] += 1
                bucket = str(signal_row["advisory"].get("confidence_bucket", "low")).lower()
                if bucket == "high":
                    diag_delta["advisory_high_confidence"] += 1
                elif bucket == "medium":
                    diag_delta["advisory_medium_confidence"] += 1
                else:
                    diag_delta["advisory_low_confidence"] += 1
        except Exception as e:
            LOG.debug("Advisory scoring skipped for %s: %s", ticker, e)

        return {"ok": True, "signal": signal_row, "diag": diag_delta}
    except Exception as e:
        diag_delta["stage_b_exceptions"] += 1
        return {"ok": False, "error": f"{ticker}: {e}", "diag": diag_delta}


def scan_for_signals_detailed(
    skill_dir: Path | None = None,
    env_overrides: dict[str, str] | None = None,
    watchlist_override: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Like scan_for_signals, but also returns lightweight diagnostics counters.

    Diagnostics are intended to explain why you might get 0 results without
    dumping full per-ticker logs to Discord.

    env_overrides: applied for this scan only (same keys as backtest StrategyOverrides).
    watchlist_override: if set, scan these tickers instead of _load_watchlist.
    """
    if env_overrides:
        from backtest import _temporary_env

        with _temporary_env(env_overrides):
            return scan_for_signals_detailed(
                skill_dir=skill_dir,
                env_overrides=None,
                watchlist_override=watchlist_override,
            )

    from notifier import send_alert
    from sector_strength import get_winning_sector_etfs

    skill_dir = skill_dir or SKILL_DIR
    auth = None
    winning_etfs = None
    signals: list[dict[str, Any]] = []
    data_failures: list[str] = []

    diagnostics: dict[str, Any] = {
        "scan_blocked": 0,
        "scan_blocked_reason": None,
        "watchlist_size": 0,
        "df_empty": 0,
        "too_few_candles": 0,
        "stage2_fail": 0,
        "vcp_fail": 0,
        "no_sector_etf": 0,
        "sector_not_winning": 0,
        "breakout_not_confirmed": 0,
        "exceptions": 0,
        "self_study_filtered": 0,
        "quality_gates_filtered": 0,
        "forensic_filtered": 0,
        "quality_gates_would_filter": 0,
        "weak_mirofish_alignment": 0,
        "low_breakout_volume": 0,
        "forensic_sloan_flags": 0,
        "forensic_beneish_flags": 0,
        "forensic_altman_flags": 0,
        "pead_boosted": 0,
        "pead_penalized": 0,
        "guidance_boosted": 0,
        "sec_tagged_signals": 0,
        "sec_recent_8k_count": 0,
        "sec_high_risk_tag_count": 0,
        "sec_data_failures": 0,
        "sec_score_hint_shadow_adjustments": 0,
        "sec_score_hint_applied_count": 0,
        "advisory_scored": 0,
        "advisory_high_confidence": 0,
        "advisory_medium_confidence": 0,
        "advisory_low_confidence": 0,
        "top_n_applied": 0,
        "event_risk_flagged": 0,
        "event_risk_blocked": 0,
        "event_risk_downsized": 0,
        "data_failure_count": 0,
        "unresolved_sector_symbols": 0,
        "stage_a_candidates": 0,
        "stage_a_shortlisted": 0,
        "stage_a_pruned": 0,
        "stage_a_timeouts": 0,
        "stage_b_processed": 0,
        "stage_b_exceptions": 0,
        "stage_b_timeouts": 0,
        "scan_stage_a_ms": 0,
        "scan_stage_b_ms": 0,
        "regime_v2_score": None,
        "regime_v2_bucket": "unknown",
        "regime_v2_blocked": 0,
        "strategy_plugins_evaluated": 0,
        "strategy_pullback_evaluated": 0,
        "strategy_pullback_triggered": 0,
        "data_quality": None,
        "data_quality_reasons": [],
        "session_data_health": None,
    }

    try:
        from schwab_auth import DualSchwabAuth
        auth = DualSchwabAuth(skill_dir=skill_dir)
    except Exception as e:
        data_failures.append(f"Auth failed: {e}")
        diagnostics["data_failure_count"] = len(data_failures)
        send_alert(f"Signal scan DATA FAILURE: Auth error: {e}", kind="data_failure", env_path=skill_dir / ".env")
        return [], diagnostics

    try:
        from data_health import assess_scan_session_data_health

        session_dq = assess_scan_session_data_health(auth, skill_dir)
        diagnostics["data_quality"] = session_dq.get("data_quality")
        diagnostics["data_quality_reasons"] = list(session_dq.get("reasons") or [])
        diagnostics["session_data_health"] = session_dq
        LOG.info(
            "Scan session data_quality=%s reasons=%s",
            diagnostics.get("data_quality"),
            diagnostics.get("data_quality_reasons"),
        )
    except Exception as e:
        LOG.debug("Scan session data health skipped: %s", e)

    # DEMO_SIGNAL=1: inject a test signal (with MiroFish) to verify full flow; no real order.
    # Keep this before regime/sector checks so smoke tests do not depend on live market state.
    import os
    if os.environ.get("DEMO_SIGNAL", "").strip().lower() in ("1", "true", "yes"):
        from market_data import extract_schwab_last_price, get_current_quote, get_daily_history
        demo_ticker = "AAPL"
        try:
            df = get_daily_history(demo_ticker, days=300, auth=auth, skill_dir=skill_dir)
            price = float(df["close"].iloc[-1]) if not df.empty else 220.0
            quote = get_current_quote(demo_ticker, auth=auth, skill_dir=skill_dir)
            live = extract_schwab_last_price(quote) if isinstance(quote, dict) else None
            if live is not None:
                price = live
        except Exception:
            price = 220.0
        mirofish_result = None
        try:
            from engine_analysis import MarketSimulation, cache_conviction
            sim = MarketSimulation(demo_ticker, auth=auth, skill_dir=skill_dir)
            result = sim.run()
            cache_conviction(demo_ticker, result, skill_dir=skill_dir)
            mirofish_result = {
                "conviction_score": result.get("conviction_score"),
                "summary": result.get("summary"),
                "agent_votes": result.get("agent_votes", []),
                "simulation_id": result.get("simulation_id"),
            }
        except Exception as e:
            LOG.warning("Demo MiroFish: %s", e)
        signals.append({
            "ticker": demo_ticker,
            "price": round(price, 2),
            "sector_etf": "XLK",
            "sma_50": round(price * 0.98, 2),
            "sma_200": round(price * 0.95, 2),
            "mirofish_summary": mirofish_result.get("summary") if mirofish_result else None,
            "mirofish_conviction": mirofish_result.get("conviction_score") if mirofish_result else None,
            "mirofish_result": mirofish_result,
            "signal_score": 75.0,
            "_demo": True,
        })
        LOG.info("DEMO_SIGNAL: injected test signal for %s", demo_ticker)
        return signals, diagnostics

    # Regime gate: SPY must be above 200 SMA unless explicitly overridden.
    try:
        from config import get_scan_allow_bear_regime
        from sector_strength import is_market_regime_bullish

        allow_bear_regime = bool(get_scan_allow_bear_regime(skill_dir))
        regime_bullish, regime_ctx = is_market_regime_bullish(auth, skill_dir)
        diagnostics["regime_bullish"] = regime_bullish
        diagnostics["scan_allow_bear_regime"] = allow_bear_regime
        diagnostics["spy_price"] = regime_ctx.get("spy_price")
        diagnostics["spy_sma_200"] = regime_ctx.get("spy_sma_200")
        if not regime_bullish and not allow_bear_regime:
            diagnostics["scan_blocked"] = 1
            diagnostics["scan_blocked_reason"] = "bear_regime_spy_below_200sma"
            msg = (
                f"Scan skipped: **bear regime** (SPY ${regime_ctx.get('spy_price', '?')} "
                f"< 200 SMA ${regime_ctx.get('spy_sma_200', '?')}). No new entries."
            )
            send_alert(msg, kind="regime_bearish", env_path=skill_dir / ".env")
            LOG.info("Regime gate blocked scan: SPY below 200 SMA")
            return [], diagnostics
        if not regime_bullish and allow_bear_regime:
            LOG.info("Regime gate override active: scan continues while SPY below 200 SMA")
    except Exception as e:
        LOG.warning("Regime check failed, proceeding with scan: %s", e)

    # Sector performance - notify on failure
    try:
        winning_etfs = get_winning_sector_etfs(auth, skill_dir)
    except Exception as e:
        data_failures.append(f"Sector data: {e}")
        diagnostics["data_failure_count"] = len(data_failures)
        send_alert(
            f"Signal scan DATA FAILURE: Could not fetch sector performance: {e}",
            kind="data_failure",
            env_path=skill_dir / ".env",
        )
        return [], diagnostics

    from config import (
        get_breakout_confirm_enabled,
        get_breakout_confirm_min_time,
        get_edgar_user_agent,
        get_forensic_altman_min,
        get_forensic_beneish_max,
        get_forensic_cache_hours,
        get_forensic_enabled,
        get_forensic_filter_mode,
        get_forensic_sloan_max,
        get_guidance_score_boost,
        get_guidance_score_enabled,
        get_guidance_score_penalty,
        get_pead_enabled,
        get_pead_lookback_days,
        get_pead_score_boost,
        get_pead_score_boost_large,
        get_pead_score_penalty,
        get_regime_v2_entry_min_score,
        get_regime_v2_mode,
        get_scan_stage_a_max_workers,
        get_scan_stage_a_shortlist_cap,
        get_scan_stage_a_shortlist_multiplier,
        get_scan_stage_b_max_workers,
        get_scan_stage_task_timeout_sec,
        get_sec_cache_hours,
        get_sec_enrichment_enabled,
        get_sec_filing_cache_hours,
        get_sec_filing_max_chars,
        get_sec_score_hint_enabled,
        get_sec_shadow_mode,
        get_sec_tagging_enabled,
        get_signal_top_n,
        get_strategy_pullback_mode,
    )

    sec_enrichment_enabled = get_sec_enrichment_enabled(skill_dir)
    sec_tagging_enabled = get_sec_tagging_enabled(skill_dir)
    sec_shadow_mode = get_sec_shadow_mode(skill_dir)
    sec_score_hint_enabled = get_sec_score_hint_enabled(skill_dir)
    sec_cache_hours = get_sec_cache_hours(skill_dir)
    edgar_user_agent = get_edgar_user_agent(skill_dir)
    forensic_enabled = get_forensic_enabled(skill_dir)
    forensic_filter_mode = get_forensic_filter_mode(skill_dir)
    forensic_cache_hours = get_forensic_cache_hours(skill_dir)
    forensic_sloan_max = get_forensic_sloan_max(skill_dir)
    forensic_beneish_max = get_forensic_beneish_max(skill_dir)
    forensic_altman_min = get_forensic_altman_min(skill_dir)
    pead_enabled = get_pead_enabled(skill_dir)
    pead_lookback_days = get_pead_lookback_days(skill_dir)
    pead_score_boost = get_pead_score_boost(skill_dir)
    pead_score_boost_large = get_pead_score_boost_large(skill_dir)
    pead_score_penalty = get_pead_score_penalty(skill_dir)
    guidance_score_enabled = get_guidance_score_enabled(skill_dir)
    guidance_score_boost = get_guidance_score_boost(skill_dir)
    guidance_score_penalty = get_guidance_score_penalty(skill_dir)
    sec_filing_cache_hours = get_sec_filing_cache_hours(skill_dir)
    sec_filing_max_chars = get_sec_filing_max_chars(skill_dir)
    pullback_mode = get_strategy_pullback_mode(skill_dir)

    if watchlist_override is not None:
        watchlist = [str(t).strip().upper() for t in watchlist_override if str(t).strip()]
    else:
        watchlist = _load_watchlist(skill_dir)
    diagnostics["watchlist_size"] = len(watchlist)
    top_n = get_signal_top_n(skill_dir)
    stage_a_workers = get_scan_stage_a_max_workers(skill_dir)
    stage_b_workers = get_scan_stage_b_max_workers(skill_dir)
    diagnostics["scan_parallelism"] = {
        "stage_a_max_workers": int(stage_a_workers),
        "stage_b_max_workers": int(stage_b_workers),
    }
    shortlist_multiplier = get_scan_stage_a_shortlist_multiplier(skill_dir)
    shortlist_cap = get_scan_stage_a_shortlist_cap(skill_dir)
    task_timeout_sec = max(5.0, float(get_scan_stage_task_timeout_sec(skill_dir)))
    breakout_enabled = get_breakout_confirm_enabled(skill_dir)
    breakout_min_time = get_breakout_confirm_min_time(skill_dir)
    regime_v2_mode = get_regime_v2_mode(skill_dir)
    regime_v2_entry_min_score = get_regime_v2_entry_min_score(skill_dir)

    # Optional composite regime diagnostics/gate.
    regime_v2_snapshot: dict[str, Any] | None = None
    try:
        from sector_strength import get_regime_v2_snapshot

        regime_v2_snapshot = get_regime_v2_snapshot(auth, skill_dir)
        diagnostics["regime_v2_mode"] = regime_v2_mode
        diagnostics["regime_v2_score"] = regime_v2_snapshot.get("score")
        diagnostics["regime_v2_bucket"] = regime_v2_snapshot.get("bucket")
        diagnostics["regime_v2_entry_min_score"] = regime_v2_entry_min_score
        if regime_v2_mode == "live" and float(regime_v2_snapshot.get("score", 0) or 0) < float(regime_v2_entry_min_score):
            diagnostics["regime_v2_blocked"] = 1
            send_alert(
                (
                    "Scan skipped: regime v2 score "
                    f"{regime_v2_snapshot.get('score')} below entry threshold {regime_v2_entry_min_score}."
                ),
                kind="regime_bearish",
                env_path=skill_dir / ".env",
            )
            return [], diagnostics
    except Exception as e:
        LOG.debug("Regime v2 scan diagnostics skipped: %s", e)

    # Stage A: fast structural filter on broad universe.
    stage_a_start = time.perf_counter()
    stage_a_candidates: list[dict[str, Any]] = []
    stage_a_reason_keys = {
        "df_empty",
        "too_few_candles",
        "stage2_fail",
        "vcp_fail",
        "no_sector_etf",
        "sector_not_winning",
        "breakout_not_confirmed",
        "exceptions",
    }
    future_map_a: dict[cf.Future[Any], str] = {}
    with cf.ThreadPoolExecutor(max_workers=max(1, stage_a_workers)) as ex:
        for ticker in watchlist:
            fut = ex.submit(
                _scan_stage_a_one,
                ticker,
                auth,
                set(winning_etfs or []),
                skill_dir,
                breakout_enabled,
                breakout_min_time,
            )
            future_map_a[fut] = ticker
        try:
            for fut in cf.as_completed(
                future_map_a.keys(),
                timeout=max(task_timeout_sec, task_timeout_sec * max(1, len(future_map_a))),
            ):
                ticker = future_map_a[fut]
                try:
                    out = fut.result()
                    if out.get("ok"):
                        stage_a_candidates.append(out["candidate"])
                        continue
                    reason = str(out.get("reason") or "exceptions")
                    if reason in stage_a_reason_keys:
                        diagnostics[reason] = int(diagnostics.get(reason, 0) or 0) + 1
                    if out.get("error"):
                        data_failures.append(str(out["error"]))
                except Exception as e:
                    diagnostics["exceptions"] += 1
                    data_failures.append(f"{ticker}: {e}")
        except cf.TimeoutError:
            pending = [future_map_a[f] for f in future_map_a if not f.done()]
            diagnostics["stage_a_timeouts"] += len(pending)
            diagnostics["exceptions"] += len(pending)
            for ticker in pending[:10]:
                data_failures.append(f"{ticker}: stage_a timeout")
    diagnostics["scan_stage_a_ms"] = int((time.perf_counter() - stage_a_start) * 1000)

    diagnostics["stage_a_candidates"] = len(stage_a_candidates)
    stage_a_candidates.sort(key=lambda c: c.get("stage_a_score", 0), reverse=True)
    shortlist_limit = _compute_stage_a_shortlist_limit(
        total_candidates=len(stage_a_candidates),
        top_n=top_n,
        multiplier=shortlist_multiplier,
        cap=shortlist_cap,
    )
    shortlist = stage_a_candidates[:shortlist_limit]
    diagnostics["stage_a_shortlisted"] = len(shortlist)
    diagnostics["stage_a_pruned"] = max(0, len(stage_a_candidates) - len(shortlist))

    # Stage B: expensive enrichment/ranking on shortlist only.
    stage_b_start = time.perf_counter()
    future_map_b: dict[cf.Future[Any], str] = {}
    with cf.ThreadPoolExecutor(max_workers=max(1, stage_b_workers)) as ex:
        for candidate in shortlist:
            ticker = str(candidate.get("ticker") or "")
            fut = ex.submit(
                _scan_stage_b_enrich,
                candidate,
                auth,
                skill_dir,
                pullback_mode,
                sec_enrichment_enabled,
                sec_tagging_enabled,
                sec_shadow_mode,
                sec_score_hint_enabled,
                sec_cache_hours,
                edgar_user_agent,
                forensic_enabled,
                forensic_filter_mode,
                forensic_cache_hours,
                forensic_sloan_max,
                forensic_beneish_max,
                forensic_altman_min,
                pead_enabled,
                pead_lookback_days,
                pead_score_boost,
                pead_score_boost_large,
                pead_score_penalty,
                guidance_score_enabled,
                guidance_score_boost,
                guidance_score_penalty,
                sec_filing_cache_hours,
                sec_filing_max_chars,
            )
            future_map_b[fut] = ticker
        try:
            for fut in cf.as_completed(
                future_map_b.keys(),
                timeout=max(task_timeout_sec, task_timeout_sec * max(1, len(future_map_b))),
            ):
                ticker = future_map_b[fut]
                try:
                    out = fut.result()
                    diag_delta = out.get("diag") or {}
                    for k, v in diag_delta.items():
                        diagnostics[k] = int(diagnostics.get(k, 0) or 0) + int(v or 0)
                    if out.get("ok"):
                        signals.append(out["signal"])
                    else:
                        diagnostics["exceptions"] += 1
                        diagnostics["stage_b_exceptions"] += 1
                        if out.get("error"):
                            data_failures.append(str(out["error"]))
                except Exception as e:
                    diagnostics["exceptions"] += 1
                    diagnostics["stage_b_exceptions"] += 1
                    data_failures.append(f"{ticker}: {e}")
        except cf.TimeoutError:
            pending = [future_map_b[f] for f in future_map_b if not f.done()]
            diagnostics["stage_b_timeouts"] += len(pending)
            diagnostics["exceptions"] += len(pending)
            diagnostics["stage_b_exceptions"] += len(pending)
            for ticker in pending[:10]:
                data_failures.append(f"{ticker}: stage_b timeout")
    diagnostics["scan_stage_b_ms"] = int((time.perf_counter() - stage_b_start) * 1000)

    diagnostics["data_failure_count"] = len(data_failures)
    if data_failures:
        send_alert(
            "Signal scan had data issues (some tickers skipped):\n" + "\n".join(data_failures[:5]),
            kind="scan_data_issues",
            env_path=skill_dir / ".env",
        )
    try:
        from sector_strength import get_unresolved_sector_symbols
        diagnostics["unresolved_sector_symbols"] = len(get_unresolved_sector_symbols(skill_dir=skill_dir))
    except Exception:
        pass

    # Self-study: optionally filter by learned min conviction
    try:
        from self_study import get_learned_min_conviction
        min_conv = get_learned_min_conviction(skill_dir)
        if min_conv is not None:
            before = len(signals)
            signals = [s for s in signals if (s.get("mirofish_conviction") or 0) >= min_conv]
            if before > len(signals):
                diagnostics["self_study_filtered"] = before - len(signals)
                LOG.info(
                    "Self-study: filtered %d signals (min_conviction=%d)",
                    diagnostics["self_study_filtered"],
                    min_conv,
                )
    except Exception as e:
        LOG.debug("Self-study filter skipped: %s", e)

    # Optional quality gates (default off). When disabled, count would-filter diagnostics only.
    try:
        from config import get_quality_gates_mode
        quality_mode = get_quality_gates_mode(skill_dir)
        diagnostics["quality_gates_mode"] = quality_mode
        gated: list[dict[str, Any]] = []
        for s in signals:
            reasons = _evaluate_quality_gates(s, skill_dir)
            if not reasons:
                gated.append(s)
                continue
            for r in reasons:
                key = f"quality_reason_{r}"
                diagnostics[key] = int(diagnostics.get(key, 0) or 0) + 1
            has_forensic_reason = any(
                r in {"forensic_sloan_high", "forensic_beneish_manipulator", "forensic_altman_distress"}
                for r in reasons
            )
            if _quality_mode_should_filter(reasons, skill_dir):
                diagnostics["quality_gates_filtered"] += 1
                if has_forensic_reason:
                    diagnostics["forensic_filtered"] += 1
                continue
            diagnostics["quality_gates_would_filter"] += 1
            gated.append(s)
        signals = gated
    except Exception as e:
        LOG.debug("Quality gate evaluation skipped: %s", e)

    # Event-risk policy: can tag, suppress, or mark downsize intent.
    try:
        signals = _apply_event_risk_policy_to_signals(signals, diagnostics, skill_dir)
    except Exception as e:
        LOG.debug("Event-risk policy evaluation skipped: %s", e)

    # Strategy plugin ensemble (shadow by default): rank-ready score + attribution diagnostics.
    try:
        from strategy_plugins import apply_strategy_ensemble

        signals = apply_strategy_ensemble(
            signals=signals,
            diagnostics=diagnostics,
            regime_v2_snapshot=regime_v2_snapshot,
            skill_dir=skill_dir,
        )
    except Exception as e:
        LOG.debug("Strategy ensemble evaluation skipped: %s", e)

    # Rank by score and take top N
    top_n = get_signal_top_n(skill_dir)
    signals.sort(key=lambda s: s.get("ensemble_score", s.get("signal_score", 0)), reverse=True)
    if top_n > 0 and len(signals) > top_n:
        diagnostics["top_n_applied"] = len(signals) - top_n
        signals = signals[:top_n]

    if regime_v2_snapshot is not None:
        for s in signals:
            s["regime_v2"] = {
                "score": regime_v2_snapshot.get("score"),
                "bucket": regime_v2_snapshot.get("bucket"),
                "mode": regime_v2_mode,
            }

    try:
        _record_quality_snapshot(skill_dir, diagnostics, signals)
    except Exception as e:
        LOG.debug("Quality metrics snapshot skipped: %s", e)

    return signals, diagnostics


def scan_for_signals(skill_dir: Path | None = None) -> list[dict[str, Any]]:
    """Compatibility wrapper around scan_for_signals_detailed (signals only)."""
    signals, _diagnostics = scan_for_signals_detailed(skill_dir)
    return signals


def _classify_alert_tier(signal: dict[str, Any], skill_dir: Path) -> str:
    """Classify a signal as HIGH, MEDIUM, or LOW based on conviction + score."""
    from config import get_alert_min_conviction, get_alert_ping_conviction, get_alert_ping_score

    conviction = signal.get("mirofish_conviction") or 0
    score = signal.get("signal_score") or 0
    ping_conv = get_alert_ping_conviction(skill_dir)
    ping_score = get_alert_ping_score(skill_dir)
    min_conv = get_alert_min_conviction(skill_dir)

    if conviction >= ping_conv and score >= ping_score:
        return "HIGH"
    if conviction >= min_conv or score >= 40:
        return "MEDIUM"
    return "LOW"


def send_signal_alert(signal: dict[str, Any], skill_dir: Path) -> None:
    """Send Discord alert for a signal with conviction-based tiers. HIGH pings user, LOW is suppressed."""
    from alert_history import get_alert_label, record_alert_sent
    from config import get_alert_min_conviction
    from discord_confirm import request_trade_confirmation
    from execution import get_position_size_usd
    from notifier import send_alert

    t = signal["ticker"]
    tier = _classify_alert_tier(signal, skill_dir)

    min_conv = get_alert_min_conviction(skill_dir)
    conviction = signal.get("mirofish_conviction") or 0
    score = signal.get("signal_score") or 0
    if tier == "LOW" and conviction < min_conv and score < 40:
        LOG.info("Signal %s suppressed (tier=LOW, conviction=%s, score=%s)", t, conviction, score)
        return

    label = get_alert_label(t, skill_dir)
    signal_with_label = dict(signal, _alert_label=label, _alert_tier=tier)
    if signal.get("_demo"):
        signal_with_label["_mock"] = True

    if request_trade_confirmation(signal_with_label, skill_dir):
        return
    p = signal["price"]
    sector = signal["sector_etf"]
    pos_usd = get_position_size_usd(ticker=t, price=p, skill_dir=skill_dir)
    qty = max(1, int(pos_usd / p)) if p > 0 else 10

    msg = (
        f"**Signal: Buy {t}** {label} [{tier}]\n"
        f"Price: **${p}** | Sector: **{sector}** (winning)\n"
        f"Setup: Stage 2 + VCP confirmed.\n\n"
        f"*If confirm bot is offline, execute manually:*\n"
        f"`python scripts/execute_signal.py {t} {qty}`"
    )
    op_ctx: dict[str, Any] = {}
    try:
        from data_health import assess_symbol_data_health, merge_operator_payload
        from schwab_auth import DualSchwabAuth

        auth_dq = DualSchwabAuth(skill_dir=skill_dir)
        dq = assess_symbol_data_health(t, auth_dq, skill_dir=skill_dir)
        op_ctx["data_quality_payload"] = merge_operator_payload(dq)
    except Exception:
        pass

    try:
        from config import get_hypothesis_ledger_enabled
        from hypothesis_ledger import append_hypothesis, record_from_signal

        if get_hypothesis_ledger_enabled(skill_dir):
            append_hypothesis(record_from_signal(signal, skill_dir=skill_dir), skill_dir=skill_dir)
    except Exception as e:
        LOG.debug("Hypothesis ledger append skipped: %s", e)

    if send_alert(msg, kind="signal", env_path=skill_dir / ".env", operator_context=op_ctx or None):
        record_alert_sent(t, skill_dir)


def _build_comparison_embed(signals: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build a fielded embed comparing 2+ signals, sorted by score descending."""
    from datetime import datetime, timezone

    if len(signals) < 2:
        return None

    embed: dict[str, Any] = {
        "title": f"Scan Results - {len(signals)} signal(s)",
        "color": 0x3498DB,
        "fields": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Approve/Reject buttons below each signal"},
    }

    for s in signals:
        score = s.get("signal_score", 0)
        conv = s.get("mirofish_conviction")
        conv_str = f"{conv:+d}" if conv is not None else "N/A"
        sector = s.get("sector_etf", "?")
        embed["fields"].append({
            "name": f"{s['ticker']}  Score: {score:.0f}",
            "value": f"Conv: {conv_str} | Sector: {sector}",
            "inline": True,
        })
    if len(embed["fields"]) > 12:
        embed["fields"] = embed["fields"][:12]
        embed["fields"].append(
            {
                "name": "More Signals",
                "value": f"+{len(signals) - 12} additional signal(s) not shown in comparison card.",
                "inline": False,
            }
        )

    return embed


def _send_comparison_embed(signals: list[dict[str, Any]], skill_dir: Path) -> None:
    """Send comparison embed via webhook before individual alerts."""
    embed = _build_comparison_embed(signals)
    if not embed:
        return
    from notifier import send_embed_alert
    send_embed_alert(embed, env_path=skill_dir / ".env")


def run_scan_and_notify(skill_dir: Path | None = None, send_summary: bool = True) -> int:
    """Scan, send Discord for each signal. Returns count of signals found."""
    from notifier import send_alert

    skill_dir = skill_dir or SKILL_DIR
    signals, diagnostics = scan_for_signals_detailed(skill_dir)
    if signals:
        from discord_confirm import ensure_bot_ready
        ensure_bot_ready(timeout=15)

    if len(signals) >= 2:
        _send_comparison_embed(signals, skill_dir)

    for sig in signals:
        send_signal_alert(sig, skill_dir)
    if send_summary:
        if signals:
            tickers = ", ".join(s["ticker"] for s in signals)
            send_alert(
                f"Scan complete: **{len(signals)}** signal(s) found.\nTickers: {tickers}",
                kind="scan_complete",
                env_path=skill_dir / ".env",
                operator_context={
                    "data_quality": diagnostics.get("data_quality"),
                    "data_quality_reasons": diagnostics.get("data_quality_reasons") or [],
                },
            )
        else:
            msg = (
                "Scan complete: No signals found.\n"
                "Diagnostics: "
                f"watchlist={diagnostics.get('watchlist_size', 0)} "
                f"df_empty={diagnostics.get('df_empty', 0)} "
                f"too_few={diagnostics.get('too_few_candles', 0)} "
                f"stage2_fail={diagnostics.get('stage2_fail', 0)} "
                f"vcp_fail={diagnostics.get('vcp_fail', 0)} "
                f"sector_not_winning={diagnostics.get('sector_not_winning', 0)} "
                f"breakout_not_confirmed={diagnostics.get('breakout_not_confirmed', 0)} "
                f"exceptions={diagnostics.get('exceptions', 0)}"
            )
            send_alert(
                msg,
                kind="scan_complete",
                env_path=skill_dir / ".env",
                operator_context={
                    "data_quality": diagnostics.get("data_quality"),
                    "data_quality_reasons": diagnostics.get("data_quality_reasons") or [],
                },
            )
    return len(signals)


if __name__ == "__main__":
    import sys
    import time

    from logger_setup import setup_logging
    setup_logging()
    n = run_scan_and_notify()
    print(f"Found {n} signals, notifications sent.")
    if n > 0:
        # Bot must stay connected to receive Approve/Reject clicks (buttons expire in 10 min)
        print("Bot staying connected for 10 min to receive Approve/Reject. Press Ctrl+C to exit early.")
        try:
            time.sleep(600)
        except KeyboardInterrupt:
            print("\nExiting.")
    sys.exit(0 if n >= 0 else 1)
