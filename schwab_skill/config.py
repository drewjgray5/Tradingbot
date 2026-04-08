"""
Load configurable parameters from .env for Stage 2, VCP, signal scoring, and data.
"""

from __future__ import annotations

import os
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent


def _load_env(skill_dir: Path | None = None) -> dict[str, str]:
    path = (skill_dir or SKILL_DIR) / ".env"
    if not path.exists():
        return {}
    vals: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip('"\'')
    return vals


def _env_value(key: str, env: dict[str, str]) -> str:
    """
    Resolve config with process override precedence.

    Process env overrides let scripts tune parameters per run without editing
    local `.env`.
    """
    raw = os.environ.get(key)
    if raw is not None and str(raw).strip() != "":
        return str(raw)
    return str(env.get(key, ""))


def _get_float(key: str, default: float, skill_dir: Path | None = None) -> float:
    env = _load_env(skill_dir)
    v = _env_value(key, env)
    if not v:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _get_int(key: str, default: int, skill_dir: Path | None = None) -> int:
    env = _load_env(skill_dir)
    v = _env_value(key, env)
    if not v:
        return default
    try:
        return max(1, int(float(v)))
    except (ValueError, TypeError):
        return default


def _get_bool(key: str, default: bool, skill_dir: Path | None = None) -> bool:
    env = _load_env(skill_dir)
    v = _env_value(key, env).strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return default


def _get_mode(
    key: str,
    allowed: set[str],
    default: str,
    skill_dir: Path | None = None,
) -> str:
    env = _load_env(skill_dir)
    raw = _env_value(key, env).strip().lower()
    if raw in allowed:
        return raw
    return default


PLUGIN_MODE_VALUES = {"off", "shadow", "live"}


# Stage 2: price must be within this fraction of 52-week high (0.85 = within 15%)
def get_stage2_52w_pct(skill_dir: Path | None = None) -> float:
    return _get_float("STAGE2_52W_PCT", 0.85, skill_dir)


# Stage 2: 200 SMA must be upward for this many days
def get_stage2_sma_upward_days(skill_dir: Path | None = None) -> int:
    return _get_int("STAGE2_SMA_UPWARD_DAYS", 20, skill_dir)


# VCP: number of consecutive days volume below 50d avg
def get_vcp_days(skill_dir: Path | None = None) -> int:
    return _get_int("VCP_DAYS", 4, skill_dir)


# Signal ranking: max number of signals to send (0 = no limit)
def get_signal_top_n(skill_dir: Path | None = None) -> int:
    return _get_int("SIGNAL_TOP_N", 5, skill_dir)


# Scanner: bounded workers for fast filter stage
def get_scan_stage_a_max_workers(skill_dir: Path | None = None) -> int:
    return _get_int("SCAN_STAGE_A_MAX_WORKERS", 8, skill_dir)


# Scanner: bounded workers for heavy enrichment stage
def get_scan_stage_b_max_workers(skill_dir: Path | None = None) -> int:
    return _get_int("SCAN_STAGE_B_MAX_WORKERS", 2, skill_dir)


# Scanner: shortlist width relative to top-N final output size
def get_scan_stage_a_shortlist_multiplier(skill_dir: Path | None = None) -> float:
    return _get_float("SCAN_STAGE_A_SHORTLIST_MULTIPLIER", 3.0, skill_dir)


# Scanner: hard cap for Stage A shortlist candidates
def get_scan_stage_a_shortlist_cap(skill_dir: Path | None = None) -> int:
    return _get_int("SCAN_STAGE_A_SHORTLIST_CAP", 40, skill_dir)


# Scanner: per-ticker stage timeout safety bound (seconds)
def get_scan_stage_task_timeout_sec(skill_dir: Path | None = None) -> float:
    return _get_float("SCAN_STAGE_TASK_TIMEOUT_SEC", 120.0, skill_dir)


# Scanner: allow scans to run even when SPY is below 200 SMA.
def get_scan_allow_bear_regime(skill_dir: Path | None = None) -> bool:
    return _get_bool("SCAN_ALLOW_BEAR_REGIME", False, skill_dir)


# Breakout confirmation: require intraday price above prior high (minutes from midnight, 570=9:30)
def get_breakout_confirm_min_time(skill_dir: Path | None = None) -> int:
    return _get_int("BREAKOUT_CONFIRM_MIN_TIME", 570, skill_dir)


# Breakout confirmation: enable/disable
def get_breakout_confirm_enabled(skill_dir: Path | None = None) -> bool:
    return _get_bool("BREAKOUT_CONFIRM_ENABLED", True, skill_dir)


# Data: prefer Schwab, only use yfinance on explicit failure
def get_prefer_schwab_data(skill_dir: Path | None = None) -> bool:
    return _get_bool("PREFER_SCHWAB_DATA", True, skill_dir)


# Volatility sizing: base USD when ATR_mult=1.0
def get_volatility_base_usd(skill_dir: Path | None = None) -> int:
    return _get_int("VOLATILITY_BASE_USD", 5000, skill_dir)


# Volatility sizing: target ATR multiple (2.0 = size for 2 ATR stop)
def get_volatility_atr_mult(skill_dir: Path | None = None) -> float:
    return _get_float("VOLATILITY_ATR_MULT", 2.0, skill_dir)


# Volatility sizing: enable (false = use fixed POSITION_SIZE_USD)
def get_volatility_sizing_enabled(skill_dir: Path | None = None) -> bool:
    return _get_bool("VOLATILITY_SIZING_ENABLED", False, skill_dir)


def get_alert_min_conviction(skill_dir: Path | None = None) -> int:
    """Minimum conviction to send any alert (below = suppressed)."""
    return _get_int("ALERT_MIN_CONVICTION", 20, skill_dir)


def get_alert_ping_conviction(skill_dir: Path | None = None) -> int:
    """Conviction threshold above which the user gets a @ping."""
    return _get_int("ALERT_PING_CONVICTION", 50, skill_dir)


def get_alert_ping_score(skill_dir: Path | None = None) -> int:
    """Setup score threshold above which the user gets a @ping."""
    return _get_int("ALERT_PING_SCORE", 60, skill_dir)


def get_stop_order_duration(skill_dir: Path | None = None) -> str:
    """
    Stop duration for protective trailing stop orders.
    Allowed values are normalized to DAY or GOOD_TILL_CANCEL.
    """
    env = _load_env(skill_dir)
    raw = _env_value("STOP_ORDER_DURATION", env).strip().upper()
    if raw in ("DAY", "GOOD_TILL_CANCEL"):
        return raw
    return "GOOD_TILL_CANCEL"


def get_adaptive_stop_enabled(skill_dir: Path | None = None) -> bool:
    """Enable adaptive stop sizing using ATR + trend regime."""
    return _get_bool("ADAPTIVE_STOP_ENABLED", True, skill_dir)


def get_adaptive_stop_base_pct(skill_dir: Path | None = None) -> float:
    """Base stop percentage fallback when adaptive inputs are unavailable."""
    return _get_float("ADAPTIVE_STOP_BASE_PCT", 0.07, skill_dir)


def get_adaptive_stop_min_pct(skill_dir: Path | None = None) -> float:
    """Minimum adaptive stop percent clamp."""
    return _get_float("ADAPTIVE_STOP_MIN_PCT", 0.05, skill_dir)


def get_adaptive_stop_max_pct(skill_dir: Path | None = None) -> float:
    """Maximum adaptive stop percent clamp."""
    return _get_float("ADAPTIVE_STOP_MAX_PCT", 0.12, skill_dir)


def get_adaptive_stop_atr_mult(skill_dir: Path | None = None) -> float:
    """ATR multiplier for stop distance. 2.5x ATR gives each stock room proportional to its volatility."""
    return _get_float("ADAPTIVE_STOP_ATR_MULT", 2.5, skill_dir)


def get_adaptive_stop_trend_lookback(skill_dir: Path | None = None) -> int:
    """Lookback window for trend regime adjustment."""
    return _get_int("ADAPTIVE_STOP_TREND_LOOKBACK", 20, skill_dir)


def get_execution_shadow_mode(skill_dir: Path | None = None) -> bool:
    """
    If true, execution computes decisions but does not submit live broker orders.
    """
    return _get_bool("EXECUTION_SHADOW_MODE", False, skill_dir)


def get_exec_quality_mode(skill_dir: Path | None = None) -> str:
    """Execution quality plugin mode (OFF|SHADOW|LIVE)."""
    return _get_mode("EXEC_QUALITY_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_exit_manager_mode(skill_dir: Path | None = None) -> str:
    """Exit manager plugin mode (OFF|SHADOW|LIVE)."""
    return _get_mode("EXIT_MANAGER_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_event_risk_mode(skill_dir: Path | None = None) -> str:
    """Event-risk plugin mode (OFF|SHADOW|LIVE)."""
    return _get_mode("EVENT_RISK_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_correlation_guard_mode(skill_dir: Path | None = None) -> str:
    """Correlation guard plugin mode (OFF|SHADOW|LIVE)."""
    return _get_mode("CORRELATION_GUARD_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_regime_v2_mode(skill_dir: Path | None = None) -> str:
    """Regime v2 plugin mode (OFF|SHADOW|LIVE)."""
    return _get_mode("REGIME_V2_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_strategy_pullback_mode(skill_dir: Path | None = None) -> str:
    """Pullback strategy plugin mode (OFF|SHADOW|LIVE)."""
    return _get_mode("STRATEGY_PULLBACK_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_strategy_regime_router_mode(skill_dir: Path | None = None) -> str:
    """Regime router weighting mode for strategy ensemble (OFF|SHADOW|LIVE)."""
    return _get_mode("STRATEGY_REGIME_ROUTER_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_strategy_ensemble_mode(skill_dir: Path | None = None) -> str:
    """Final ensemble rank mode (OFF|SHADOW|LIVE)."""
    return _get_mode("STRATEGY_ENSEMBLE_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_strategy_weight_breakout_high(skill_dir: Path | None = None) -> float:
    return _get_float("STRATEGY_WEIGHT_BREAKOUT_HIGH", 1.00, skill_dir)


def get_strategy_weight_breakout_med(skill_dir: Path | None = None) -> float:
    return _get_float("STRATEGY_WEIGHT_BREAKOUT_MED", 1.00, skill_dir)


def get_strategy_weight_breakout_low(skill_dir: Path | None = None) -> float:
    return _get_float("STRATEGY_WEIGHT_BREAKOUT_LOW", 0.95, skill_dir)


def get_strategy_weight_pullback_high(skill_dir: Path | None = None) -> float:
    return _get_float("STRATEGY_WEIGHT_PULLBACK_HIGH", 0.90, skill_dir)


def get_strategy_weight_pullback_med(skill_dir: Path | None = None) -> float:
    return _get_float("STRATEGY_WEIGHT_PULLBACK_MED", 1.05, skill_dir)


def get_strategy_weight_pullback_low(skill_dir: Path | None = None) -> float:
    return _get_float("STRATEGY_WEIGHT_PULLBACK_LOW", 1.10, skill_dir)


def get_exec_quality_min_signal_score(skill_dir: Path | None = None) -> int:
    """Execution quality threshold (unused for now)."""
    return _get_int("EXEC_QUALITY_MIN_SIGNAL_SCORE", 55, skill_dir)


def get_exec_spread_max_bps(skill_dir: Path | None = None) -> int:
    """Max allowed bid/ask spread in basis points for execution quality checks."""
    return _get_int("EXEC_SPREAD_MAX_BPS", 35, skill_dir)


def get_exec_slippage_max_bps(skill_dir: Path | None = None) -> int:
    """Max allowed expected slippage in basis points for execution quality checks."""
    return _get_int("EXEC_SLIPPAGE_MAX_BPS", 20, skill_dir)


def get_exec_reprice_attempts(skill_dir: Path | None = None) -> int:
    """Max bounded cancel/replace attempts for limit orders."""
    return _get_int("EXEC_REPRICE_ATTEMPTS", 2, skill_dir)


def get_exec_reprice_interval_sec(skill_dir: Path | None = None) -> int:
    """Seconds to wait between limit-order reprice checks."""
    return _get_int("EXEC_REPRICE_INTERVAL_SEC", 3, skill_dir)


def get_exec_use_limit_for_liquid(skill_dir: Path | None = None) -> bool:
    """Prefer limit orders for liquid symbols under execution quality live mode."""
    return _get_bool("EXEC_USE_LIMIT_FOR_LIQUID", True, skill_dir)


def get_exit_manager_trail_atr_mult(skill_dir: Path | None = None) -> float:
    """Exit manager threshold (unused for now)."""
    return _get_float("EXIT_MANAGER_TRAIL_ATR_MULT", 2.0, skill_dir)


def get_exit_partial_tp_r_mult(skill_dir: Path | None = None) -> float:
    """R-multiple target for first partial take-profit."""
    return _get_float("EXIT_PARTIAL_TP_R_MULT", 1.5, skill_dir)


def get_exit_partial_tp_fraction(skill_dir: Path | None = None) -> float:
    """Fraction of shares to trim at partial take-profit trigger."""
    value = _get_float("EXIT_PARTIAL_TP_FRACTION", 0.5, skill_dir)
    return max(0.05, min(0.95, value))


def get_exit_breakeven_after_partial(skill_dir: Path | None = None) -> bool:
    """Move residual stop to breakeven after partial fill."""
    return _get_bool("EXIT_BREAKEVEN_AFTER_PARTIAL", True, skill_dir)


def get_exit_max_hold_days(skill_dir: Path | None = None) -> int:
    """Maximum hold days before forcing a time-stop exit."""
    return _get_int("EXIT_MAX_HOLD_DAYS", 12, skill_dir)


def get_event_risk_blackout_minutes(skill_dir: Path | None = None) -> int:
    """Event risk threshold (unused for now)."""
    return _get_int("EVENT_RISK_BLACKOUT_MINUTES", 30, skill_dir)


def get_event_block_earnings_days(skill_dir: Path | None = None) -> int:
    """Flag symbols with earnings within +/-N days."""
    return _get_int("EVENT_BLOCK_EARNINGS_DAYS", 2, skill_dir)


def get_event_macro_blackout_enabled(skill_dir: Path | None = None) -> bool:
    """Enable macro blackout date checks."""
    return _get_bool("EVENT_MACRO_BLACKOUT_ENABLED", False, skill_dir)


def get_event_action(skill_dir: Path | None = None) -> str:
    """Event-risk action policy: block or downsize."""
    env = _load_env(skill_dir)
    raw = _env_value("EVENT_ACTION", env).strip().lower()
    if raw in {"block", "downsize"}:
        return raw
    return "block"


def get_event_downsize_factor(skill_dir: Path | None = None) -> float:
    """Position multiplier used for event-risk downsize action."""
    v = _get_float("EVENT_DOWNSIZE_FACTOR", 0.5, skill_dir)
    return max(0.10, min(1.0, v))


def get_correlation_guard_max_pair_corr(skill_dir: Path | None = None) -> float:
    """Correlation guard threshold (unused for now)."""
    return _get_float("CORRELATION_GUARD_MAX_PAIR_CORR", 0.85, skill_dir)


def get_regime_v2_min_confidence(skill_dir: Path | None = None) -> float:
    """Regime v2 threshold (unused for now)."""
    return _get_float("REGIME_V2_MIN_CONFIDENCE", 0.55, skill_dir)


def get_regime_v2_entry_min_score(skill_dir: Path | None = None) -> int:
    """Minimum composite regime score required for new entries."""
    return _get_int("REGIME_V2_ENTRY_MIN_SCORE", 55, skill_dir)


def get_regime_v2_size_mult_high(skill_dir: Path | None = None) -> float:
    """Sizing multiplier for high regime bucket."""
    return _get_float("REGIME_V2_SIZE_MULT_HIGH", 1.0, skill_dir)


def get_regime_v2_size_mult_med(skill_dir: Path | None = None) -> float:
    """Sizing multiplier for medium regime bucket."""
    return _get_float("REGIME_V2_SIZE_MULT_MED", 0.7, skill_dir)


def get_regime_v2_size_mult_low(skill_dir: Path | None = None) -> float:
    """Sizing multiplier for low regime bucket."""
    return _get_float("REGIME_V2_SIZE_MULT_LOW", 0.4, skill_dir)


def get_quality_gates_enabled(skill_dir: Path | None = None) -> bool:
    """Legacy check — prefer get_quality_gates_mode() directly."""
    return get_quality_gates_mode(skill_dir) in {"soft", "hard"}


def get_quality_gates_mode(skill_dir: Path | None = None) -> str:
    """
    Quality gate mode:
    - off: disabled (diagnostics only)
    - shadow: disabled but tracks would-filter counts
    - soft: filter only when multiple weak reasons exist (default)
    - hard: filter on any weak reason
    Note: weak_breakout_volume is always a hard gate regardless of mode.
    """
    env = _load_env(skill_dir)
    raw = _env_value("QUALITY_GATES_MODE", env).strip().lower()
    if raw in {"off", "shadow", "soft", "hard"}:
        return raw
    enabled = _get_bool("QUALITY_GATES_ENABLED", False, skill_dir)
    return "hard" if enabled else "shadow"


def get_quality_soft_min_reasons(skill_dir: Path | None = None) -> int:
    """Minimum number of weak reasons before filtering in soft mode."""
    return _get_int("QUALITY_SOFT_MIN_REASONS", 2, skill_dir)


def get_quality_min_signal_score(skill_dir: Path | None = None) -> int:
    """Minimum score required when quality gates are enabled."""
    return _get_int("QUALITY_MIN_SIGNAL_SCORE", 50, skill_dir)


def get_quality_min_continuation_prob(skill_dir: Path | None = None) -> float:
    """Minimum continuation probability (0..1) when quality gates are enabled."""
    return _get_float("QUALITY_MIN_CONTINUATION_PROB", 0.55, skill_dir)


def get_quality_max_bull_trap_prob(skill_dir: Path | None = None) -> float:
    """Maximum acceptable bull-trap probability (0..1) when quality gates are enabled."""
    return _get_float("QUALITY_MAX_BULL_TRAP_PROB", 0.45, skill_dir)


def get_quality_require_breakout_volume(skill_dir: Path | None = None) -> bool:
    """Require latest volume above 50-day average when quality gates are enabled."""
    return _get_bool("QUALITY_REQUIRE_BREAKOUT_VOLUME", False, skill_dir)


def get_quality_breakout_volume_min_ratio(skill_dir: Path | None = None) -> float:
    """Required latest/avg50 volume ratio for breakout quality confirmation."""
    return _get_float("QUALITY_BREAKOUT_VOLUME_MIN_RATIO", 0.90, skill_dir)


def get_quality_watchlist_prefilter_enabled(skill_dir: Path | None = None) -> bool:
    """Reduce universe noise with deterministic prefiltering before scan loop."""
    return _get_bool("QUALITY_WATCHLIST_PREFILTER_ENABLED", False, skill_dir)


def get_quality_watchlist_prefilter_max(skill_dir: Path | None = None) -> int:
    """Maximum symbols after optional prefiltering."""
    return _get_int("QUALITY_WATCHLIST_PREFILTER_MAX", 800, skill_dir)


def get_forensic_enabled(skill_dir: Path | None = None) -> bool:
    """Enable forensic accounting enrichment/checks."""
    return _get_bool("FORENSIC_ENABLED", True, skill_dir)


def get_forensic_filter_mode(skill_dir: Path | None = None) -> str:
    """
    Forensic filter mode:
    - off: disabled
    - shadow: diagnostics-only
    - soft: add quality reasons but do not hard block
    - hard: block entries with forensic flags
    """
    env = _load_env(skill_dir)
    raw = _env_value("FORENSIC_FILTER_MODE", env).strip().lower()
    if raw in {"off", "shadow", "soft", "hard"}:
        return raw
    return "shadow"


def get_forensic_sloan_max(skill_dir: Path | None = None) -> float:
    """Max acceptable Sloan ratio before flagging accrual risk."""
    return _get_float("FORENSIC_SLOAN_MAX", 0.10, skill_dir)


def get_forensic_beneish_max(skill_dir: Path | None = None) -> float:
    """Max acceptable Beneish M-score before manipulation flag."""
    return _get_float("FORENSIC_BENEISH_MAX", -1.78, skill_dir)


def get_forensic_altman_min(skill_dir: Path | None = None) -> float:
    """Min acceptable Altman Z-score before distress flag."""
    return _get_float("FORENSIC_ALTMAN_MIN", 1.80, skill_dir)


def get_forensic_cache_hours(skill_dir: Path | None = None) -> float:
    """TTL for forensic snapshot cache."""
    return _get_float("FORENSIC_CACHE_HOURS", 24.0, skill_dir)


def get_pead_enabled(skill_dir: Path | None = None) -> bool:
    """Enable post-earnings drift enrichment."""
    return _get_bool("PEAD_ENABLED", True, skill_dir)


def get_pead_lookback_days(skill_dir: Path | None = None) -> int:
    """Recent earnings window in days."""
    return _get_int("PEAD_LOOKBACK_DAYS", 10, skill_dir)


def get_pead_score_boost(skill_dir: Path | None = None) -> float:
    """Score boost for positive earnings surprise."""
    return _get_float("PEAD_SCORE_BOOST", 3.0, skill_dir)


def get_pead_score_boost_large(skill_dir: Path | None = None) -> float:
    """Score boost for strong positive earnings surprise."""
    return _get_float("PEAD_SCORE_BOOST_LARGE", 5.0, skill_dir)


def get_pead_score_penalty(skill_dir: Path | None = None) -> float:
    """Score penalty for negative earnings surprise."""
    return _get_float("PEAD_SCORE_PENALTY", 3.0, skill_dir)


def get_guidance_score_enabled(skill_dir: Path | None = None) -> bool:
    """Enable guidance-tone score adjustments in scanner ranking."""
    return _get_bool("GUIDANCE_SCORE_ENABLED", True, skill_dir)


def get_guidance_score_boost(skill_dir: Path | None = None) -> float:
    """Score boost when filing guidance is positive."""
    return _get_float("GUIDANCE_SCORE_BOOST", 2.0, skill_dir)


def get_guidance_score_penalty(skill_dir: Path | None = None) -> float:
    """Score penalty when filing guidance is negative."""
    return _get_float("GUIDANCE_SCORE_PENALTY", 2.0, skill_dir)


def get_signal_universe_mode(skill_dir: Path | None = None) -> str:
    """
    Universe selection mode for scanning.
    - focused: default, narrows broad universes for higher expectancy
    - broad: keep full universe
    """
    env = _load_env(skill_dir)
    raw = _env_value("SIGNAL_UNIVERSE_MODE", env).strip().lower()
    if raw in {"focused", "broad"}:
        return raw
    return "focused"


def get_signal_universe_target_size(skill_dir: Path | None = None) -> int:
    """Target size for focused universe mode."""
    return _get_int("SIGNAL_UNIVERSE_TARGET_SIZE", 250, skill_dir)


def get_sec_enrichment_enabled(skill_dir: Path | None = None) -> bool:
    """Enable SEC enrichment for reports/scanner tags."""
    return _get_bool("SEC_ENRICHMENT_ENABLED", True, skill_dir)


def get_sec_tagging_enabled(skill_dir: Path | None = None) -> bool:
    """Enable attaching SEC tags to signal payloads."""
    return _get_bool("SEC_TAGGING_ENABLED", True, skill_dir)


def get_sec_shadow_mode(skill_dir: Path | None = None) -> bool:
    """When true, SEC score hints are diagnostics-only and do not alter ranking."""
    return _get_bool("SEC_SHADOW_MODE", True, skill_dir)


def get_sec_score_hint_enabled(skill_dir: Path | None = None) -> bool:
    """Enable bounded SEC score hints in scanner ranking logic."""
    return _get_bool("SEC_SCORE_HINT_ENABLED", False, skill_dir)


def get_sec_cache_hours(skill_dir: Path | None = None) -> float:
    """SEC cache TTL in hours (conservative default)."""
    return _get_float("SEC_CACHE_HOURS", 12.0, skill_dir)


def get_edgar_user_agent(skill_dir: Path | None = None) -> str:
    """
    SEC requests should include a descriptive user-agent with contact info.
    Falls back to a safe default when missing or invalid.
    """
    env = _load_env(skill_dir)
    raw = _env_value("EDGAR_USER_AGENT", env).strip()
    if len(raw) >= 12 and "@" in raw:
        return raw
    return "SchwabTradingBot contact@example.com"


def get_sec_filing_analysis_enabled(skill_dir: Path | None = None) -> bool:
    """Enable full filing-text analysis endpoints and report enrichment."""
    return _get_bool("SEC_FILING_ANALYSIS_ENABLED", True, skill_dir)


def get_sec_filing_compare_enabled(skill_dir: Path | None = None) -> bool:
    """Enable SEC compare endpoints and dashboard compare panel."""
    return _get_bool("SEC_FILING_COMPARE_ENABLED", True, skill_dir)


def get_sec_filing_cache_hours(skill_dir: Path | None = None) -> float:
    """TTL for full filing text cache."""
    return _get_float("SEC_FILING_CACHE_HOURS", 24.0, skill_dir)


def get_sec_filing_max_chars(skill_dir: Path | None = None) -> int:
    """Max characters to keep per filing after normalization."""
    return _get_int("SEC_FILING_MAX_CHARS", 120000, skill_dir)


def get_sec_filing_max_compare_items(skill_dir: Path | None = None) -> int:
    """UI/API safeguard for compare requests."""
    return _get_int("SEC_FILING_MAX_COMPARE_ITEMS", 2, skill_dir)


def get_sec_filing_llm_summary_enabled(skill_dir: Path | None = None) -> bool:
    """Allow optional LLM summary generation on filing analyses."""
    return _get_bool("SEC_FILING_LLM_SUMMARY_ENABLED", True, skill_dir)


def get_advisory_model_enabled(skill_dir: Path | None = None) -> bool:
    """Enable advisory-only probability scoring on scan signals."""
    return _get_bool("ADVISORY_MODEL_ENABLED", True, skill_dir)


def get_advisory_model_path(skill_dir: Path | None = None) -> str:
    """Path to advisory model artifact JSON (relative to skill dir or absolute)."""
    env = _load_env(skill_dir)
    raw = _env_value("ADVISORY_MODEL_PATH", env).strip()
    return raw or "artifacts/advisory_model_v1.json"


def get_advisory_confidence_high(skill_dir: Path | None = None) -> float:
    """High-confidence threshold for calibrated P(up_10d)."""
    return _get_float("ADVISORY_CONFIDENCE_HIGH", 0.62, skill_dir)


def get_advisory_confidence_low(skill_dir: Path | None = None) -> float:
    """Medium-confidence threshold for calibrated P(up_10d)."""
    return _get_float("ADVISORY_CONFIDENCE_LOW", 0.52, skill_dir)


def get_advisory_require_model(skill_dir: Path | None = None) -> bool:
    """When true, validation should fail if advisory model is missing."""
    return _get_bool("ADVISORY_REQUIRE_MODEL", False, skill_dir)


# --- Data quality & degraded execution (default off: no behavior change) ---


def get_data_quality_exec_policy(skill_dir: Path | None = None) -> str:
    """
    How execution treats non-ok data_quality for risk-increasing orders:
    - off: no data-quality gate (default)
    - warn: log + metrics only
    - block_risk_increasing: block BUY / opening legs at guardrail boundary
    """
    env = _load_env(skill_dir)
    raw = _env_value("DATA_QUALITY_EXEC_POLICY", env).strip().lower()
    if raw in {"off", "warn", "block_risk_increasing"}:
        return raw
    return "off"


def get_data_quote_max_age_sec(skill_dir: Path | None = None) -> float:
    """Mark quote stale when last trade / quote timestamp older than this (seconds)."""
    return _get_float("DATA_QUOTE_MAX_AGE_SEC", 600.0, skill_dir)


def get_data_bar_max_staleness_days(skill_dir: Path | None = None) -> int:
    """Mark daily bars stale when last bar is older than this many calendar days."""
    return _get_int("DATA_BAR_MAX_STALENESS_DAYS", 7, skill_dir)


def get_data_edgar_max_age_hours(skill_dir: Path | None = None) -> float:
    """When SEC enrichment is on, flag if newest .sec_cache.json entry is older than this."""
    return _get_float("DATA_EDGAR_MAX_AGE_HOURS", 72.0, skill_dir)


def get_data_crosscheck_enabled(skill_dir: Path | None = None) -> bool:
    """Compare quote last to last daily close via yfinance when Schwab history exists."""
    return _get_bool("DATA_CROSSCHECK_ENABLED", False, skill_dir)


def get_data_crosscheck_max_rel_diff(skill_dir: Path | None = None) -> float:
    """Relative price difference that triggers data_quality=conflict when cross-check runs."""
    return _get_float("DATA_CROSSCHECK_MAX_REL_DIFF", 0.012, skill_dir)


# --- Hypothesis ledger (default off) ---


def get_hypothesis_ledger_enabled(skill_dir: Path | None = None) -> bool:
    return _get_bool("HYPOTHESIS_LEDGER_ENABLED", False, skill_dir)


def get_hypothesis_score_horizons(skill_dir: Path | None = None) -> list[int]:
    """Trading-day horizons for outcome scoring (e.g. 1, 5, 20)."""
    env = _load_env(skill_dir)
    raw = _env_value("HYPOTHESIS_SCORE_HORIZONS", env).strip()
    if not raw:
        return [1, 5, 20]
    out: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(max(1, int(float(part))))
        except (ValueError, TypeError):
            continue
    return out or [1, 5, 20]


def get_hypothesis_self_study_merge(skill_dir: Path | None = None) -> bool:
    """Attach hypothesis score summaries into run_self_study() output when ledger exists."""
    return _get_bool("HYPOTHESIS_SELF_STUDY_MERGE", False, skill_dir)


def get_hypothesis_promotion_guard_enabled(skill_dir: Path | None = None) -> bool:
    """When true, advisory promotion scripts consult scored hypothesis hit rates."""
    return _get_bool("HYPOTHESIS_PROMOTION_GUARD_ENABLED", False, skill_dir)


def get_hypothesis_promotion_min_n(skill_dir: Path | None = None) -> int:
    return _get_int("HYPOTHESIS_PROMOTION_MIN_N", 30, skill_dir)


def get_hypothesis_promotion_min_hit_rate(skill_dir: Path | None = None) -> float:
    return _get_float("HYPOTHESIS_PROMOTION_MIN_HIT_RATE", 0.45, skill_dir)
