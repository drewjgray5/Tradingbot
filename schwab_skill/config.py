"""
Load configurable parameters from .env for Stage 2, VCP, signal scoring, and data.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent

# Cache parsed `.env` files keyed by absolute path. Each entry stores the file's
# mtime_ns alongside the parsed values so we can invalidate when the file
# changes on disk. Previously every call to a getter (e.g. `_get_float`) would
# re-open and re-parse `.env`, which became a hot path during scans.
_ENV_CACHE: dict[str, tuple[int, dict[str, str]]] = {}
_ENV_CACHE_LOCK = threading.Lock()


def _parse_env_file(path: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip('"\'')
    return vals


def _load_env(skill_dir: Path | None = None) -> dict[str, str]:
    path = (skill_dir or SKILL_DIR) / ".env"
    try:
        st = path.stat()
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    cache_key = str(path)
    mtime = int(getattr(st, "st_mtime_ns", 0) or int(st.st_mtime * 1e9))
    with _ENV_CACHE_LOCK:
        cached = _ENV_CACHE.get(cache_key)
        if cached and cached[0] == mtime:
            return cached[1]
    try:
        parsed = _parse_env_file(path)
    except OSError:
        return {}
    with _ENV_CACHE_LOCK:
        _ENV_CACHE[cache_key] = (mtime, parsed)
    return parsed


def clear_env_cache() -> None:
    """Force a full reload of `.env` on the next getter call.

    Useful in tests and after `_apply_temporary_env` patches the file.
    """
    with _ENV_CACHE_LOCK:
        _ENV_CACHE.clear()


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


def get_pred_market_enabled(skill_dir: Path | None = None) -> bool:
    """Enable prediction-market metadata enrichment."""
    return _get_bool("PRED_MARKET_ENABLED", False, skill_dir)


def get_pred_market_mode(skill_dir: Path | None = None) -> str:
    """Prediction-market rollout mode (OFF|SHADOW|LIVE)."""
    return _get_mode("PRED_MARKET_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_pred_market_provider(skill_dir: Path | None = None) -> str:
    """
    Prediction-market provider id.
    Allowed: stub, polymarket.
    """
    env = _load_env(skill_dir)
    raw = _env_value("PRED_MARKET_PROVIDER", env).strip().lower()
    if raw in {"stub", "polymarket"}:
        return raw
    return "stub"


def get_pred_market_timeout_ms(skill_dir: Path | None = None) -> int:
    """Per-request timeout in milliseconds for prediction-market provider calls."""
    val = _get_int("PRED_MARKET_TIMEOUT_MS", 1200, skill_dir)
    return max(100, min(15000, val))


def get_pred_market_cache_ttl_sec(skill_dir: Path | None = None) -> int:
    """Cache TTL in seconds for provider responses."""
    val = _get_int("PRED_MARKET_CACHE_TTL_SEC", 300, skill_dir)
    return max(10, min(86400, val))


def get_pred_market_max_event_age_hours(skill_dir: Path | None = None) -> float:
    """Maximum age for event metadata before considered stale."""
    val = _get_float("PRED_MARKET_MAX_EVENT_AGE_HOURS", 24.0, skill_dir)
    return max(0.25, min(240.0, val))


def get_pred_market_min_liquidity(skill_dir: Path | None = None) -> float:
    """Minimum acceptable market liquidity for overlay usage."""
    val = _get_float("PRED_MARKET_MIN_LIQUIDITY", 1000.0, skill_dir)
    return max(0.0, val)


def get_pred_market_max_spread(skill_dir: Path | None = None) -> float:
    """Maximum acceptable spread (0..1) before ignoring metadata."""
    val = _get_float("PRED_MARKET_MAX_SPREAD", 0.08, skill_dir)
    return max(0.0, min(1.0, val))


def get_pred_market_min_match_confidence(skill_dir: Path | None = None) -> float:
    """Minimum acceptable PM event-ticker match confidence (0..1)."""
    val = _get_float("PRED_MARKET_MIN_MATCH_CONFIDENCE", 0.55, skill_dir)
    return max(0.0, min(1.0, val))


def get_pred_market_score_delta_clamp(skill_dir: Path | None = None) -> float:
    """Clamp (absolute) applied to PM score delta when overlay is live."""
    val = _get_float("PRED_MARKET_SCORE_DELTA_CLAMP", 2.0, skill_dir)
    return max(0.0, min(10.0, val))


def get_pred_market_size_mult_min(skill_dir: Path | None = None) -> float:
    """Lower bound for PM position-size multiplier."""
    val = _get_float("PRED_MARKET_SIZE_MULT_MIN", 0.9, skill_dir)
    return max(0.1, min(1.0, val))


def get_pred_market_size_mult_max(skill_dir: Path | None = None) -> float:
    """Upper bound for PM position-size multiplier."""
    val = _get_float("PRED_MARKET_SIZE_MULT_MAX", 1.1, skill_dir)
    return max(1.0, min(3.0, val))


def get_pred_market_advisory_delta_clamp(skill_dir: Path | None = None) -> float:
    """Clamp (absolute) applied to advisory probability delta."""
    val = _get_float("PRED_MARKET_ADVISORY_DELTA_CLAMP", 0.02, skill_dir)
    return max(0.0, min(0.25, val))


def get_pred_market_min_baseline_score(skill_dir: Path | None = None) -> float:
    """Minimum baseline signal score required before PM overlay can apply."""
    val = _get_float("PRED_MARKET_MIN_BASELINE_SCORE", 55.0, skill_dir)
    return max(0.0, min(100.0, val))


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
    # Default kept conservative to reduce Schwab 429s during wide watchlists.
    return _get_int("SCAN_STAGE_A_MAX_WORKERS", 4, skill_dir)


# Scanner: bounded workers for heavy enrichment stage
def get_scan_stage_b_max_workers(skill_dir: Path | None = None) -> int:
    # Default 4: Stage B is shortlist-only; moderate parallelism improves latency vs Schwab 429 tradeoffs.
    return _get_int("SCAN_STAGE_B_MAX_WORKERS", 4, skill_dir)


# Scanner: shortlist width relative to top-N final output size
def get_scan_stage_a_shortlist_multiplier(skill_dir: Path | None = None) -> float:
    return _get_float("SCAN_STAGE_A_SHORTLIST_MULTIPLIER", 3.0, skill_dir)


# Scanner: hard cap for Stage A shortlist candidates
def get_scan_stage_a_shortlist_cap(skill_dir: Path | None = None) -> int:
    return _get_int("SCAN_STAGE_A_SHORTLIST_CAP", 40, skill_dir)


# Scanner: per-ticker stage timeout safety bound (seconds)
def get_scan_stage_task_timeout_sec(skill_dir: Path | None = None) -> float:
    return _get_float("SCAN_STAGE_TASK_TIMEOUT_SEC", 120.0, skill_dir)


def get_scan_vcp_gate_mode(skill_dir: Path | None = None) -> str:
    """
    VCP gate mode for Stage A:
    - hard: reject candidate when VCP fails
    - shadow: keep candidate, apply score penalty, track would-filter diagnostics
    """
    env = _load_env(skill_dir)
    raw = _env_value("SCAN_VCP_GATE_MODE", env).strip().lower()
    if raw in {"hard", "shadow"}:
        return raw
    return "shadow"


def get_scan_sector_gate_mode(skill_dir: Path | None = None) -> str:
    """
    Sector gate mode for Stage A:
    - hard: reject candidate when sector is unresolved or underperforming
    - shadow: keep candidate, apply score penalty, track would-filter diagnostics
    """
    env = _load_env(skill_dir)
    raw = _env_value("SCAN_SECTOR_GATE_MODE", env).strip().lower()
    if raw in {"hard", "shadow"}:
        return raw
    return "shadow"


def get_scan_vcp_penalty_points(skill_dir: Path | None = None) -> float:
    """Stage A score penalty applied when VCP fails in shadow mode."""
    return _get_float("SCAN_VCP_PENALTY_POINTS", 14.0, skill_dir)


def get_scan_sector_penalty_points(skill_dir: Path | None = None) -> float:
    """Stage A score penalty applied when sector underperforms in shadow mode."""
    return _get_float("SCAN_SECTOR_PENALTY_POINTS", 10.0, skill_dir)


def get_scan_sector_unresolved_penalty_points(skill_dir: Path | None = None) -> float:
    """Stage A score penalty applied when sector mapping is unavailable in shadow mode."""
    return _get_float("SCAN_SECTOR_UNRESOLVED_PENALTY_POINTS", 6.0, skill_dir)


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


# Position sizing mode (forward-looking knob)
#
# ``fixed``        — current default; ``POSITION_SIZE_USD`` * conviction multiplier.
# ``vol_target``   — size each entry to a target portfolio volatility contribution
#                    (uses ATR / realised vol; not yet wired into ``execution.py``
#                    end-to-end, but exposed so backtest variants can opt in).
# ``kelly_capped`` — fractional Kelly using advisory model edge & realised vol,
#                    clamped to ``KELLY_MAX_FRACTION``. Also forward-looking.
#
# The runtime continues to honour ``VOLATILITY_SIZING_ENABLED`` until each new
# mode is fully validated. This getter exists so callers (including the new
# advisory model and the planned backtest parity layer) can branch on intent.
def get_position_size_mode(skill_dir: Path | None = None) -> str:
    env = _load_env(skill_dir)
    raw = _env_value("POSITION_SIZE_MODE", env).strip().lower()
    if raw in ("fixed", "vol_target", "kelly_capped"):
        return raw
    if raw:
        # Unknown override: log via stderr is intentionally avoided here
        # (config.py is import-time critical). Return safe default.
        return "fixed"
    # Backwards-compat: if vol sizing was already on, treat as vol_target intent.
    return "vol_target" if get_volatility_sizing_enabled(skill_dir) else "fixed"


def get_kelly_max_fraction(skill_dir: Path | None = None) -> float:
    """Cap on fractional-Kelly position size (default 0.25 = quarter-Kelly)."""
    return _get_float("KELLY_MAX_FRACTION", 0.25, skill_dir)


def get_vol_target_annualized(skill_dir: Path | None = None) -> float:
    """Per-position annualised vol target for ``vol_target`` sizing mode."""
    return _get_float("VOL_TARGET_ANNUALIZED", 0.20, skill_dir)


# ---------------------------------------------------------------------------
# Backtest portfolio simulator
# ---------------------------------------------------------------------------
# Replays per-trade returns through a shared equity book with a hard
# concurrency cap and risk-based (or fixed %) sizing. Replaces the legacy
# (1+r).cumprod() aggregator that treated every trade as a sequential
# 100%-of-equity roll and produced fictional -95% to -99% drawdowns.
def get_backtest_portfolio_enabled(skill_dir: Path | None = None) -> bool:
    """Master switch for the portfolio-level equity simulator (default on)."""
    return _get_bool("BACKTEST_PORTFOLIO_ENABLED", True, skill_dir)


def get_backtest_portfolio_starting_equity(skill_dir: Path | None = None) -> float:
    """Notional starting capital for the portfolio simulator."""
    return _get_float("BACKTEST_PORTFOLIO_STARTING_EQUITY", 100_000.0, skill_dir)


def get_backtest_portfolio_max_positions(skill_dir: Path | None = None) -> int:
    """Hard cap on simultaneous open positions in the portfolio simulator."""
    return max(1, _get_int("BACKTEST_PORTFOLIO_MAX_POSITIONS", 10, skill_dir))


def get_backtest_position_size_pct(skill_dir: Path | None = None) -> float:
    """Fallback fixed allocation (fraction of current equity) per entry when
    risk-based sizing cannot be computed (e.g. missing stop distance)."""
    return max(0.001, _get_float("BACKTEST_POSITION_SIZE_PCT", 0.05, skill_dir))


def get_backtest_risk_per_trade_pct(skill_dir: Path | None = None) -> float:
    """Fraction of current equity risked per trade when stop distance is
    available. Default 0.0075 = 0.75% Minervini/O'Neil convention. Set to
    0 to force fixed-% sizing only."""
    return max(0.0, _get_float("BACKTEST_RISK_PER_TRADE_PCT", 0.0075, skill_dir))


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
    PAPER_TRADING_ENABLED=1 is an alias for operators who prefer that name.
    """
    if _get_bool("PAPER_TRADING_ENABLED", False, skill_dir):
        return True
    return _get_bool("EXECUTION_SHADOW_MODE", False, skill_dir)


def get_live_trading_kill_switch(skill_dir: Path | None = None) -> bool:
    """Platform-wide halt when LIVE_TRADING_KILL_SWITCH=1 (injected into tenant .env on SaaS)."""
    return _get_bool("LIVE_TRADING_KILL_SWITCH", False, skill_dir)


def get_user_trading_halted(skill_dir: Path | None = None) -> bool:
    """Per-user pause when USER_TRADING_HALTED=1 (SaaS materializes from DB)."""
    return _get_bool("USER_TRADING_HALTED", False, skill_dir)


def get_live_trading_kill_switch_blocks_exits(skill_dir: Path | None = None) -> bool:
    """
    When true with kill switch / user halt, SELL and reducing orders are blocked too.
    Default false: exits still allowed.
    """
    return _get_bool("LIVE_TRADING_KILL_SWITCH_BLOCKS_EXITS", False, skill_dir)


def get_max_sector_account_fraction(skill_dir: Path | None = None) -> float:
    """
    Max fraction of total account equity allowed in one sector ETF bucket (0..1).
    0 disables the check. Uses yfinance-backed sector mapping (cached).
    """
    v = _get_float("MAX_SECTOR_ACCOUNT_FRACTION", 0.0, skill_dir)
    return max(0.0, min(1.0, v))


def get_exec_quality_mode(skill_dir: Path | None = None) -> str:
    """Execution quality plugin mode (OFF|SHADOW|LIVE).

    Default promoted to ``live`` (2026-Q2 promotion) — see
    ``docs/RELEASE_NOTES_PLUGIN_PROMOTIONS.md`` and
    ``scripts/promotion_ledger.jsonl``. Invalid values still fall back to
    the operational default (``live``) rather than silently disabling
    the gate; explicit ``EXEC_QUALITY_MODE=off`` is required to opt out.
    """
    return _get_mode("EXEC_QUALITY_MODE", PLUGIN_MODE_VALUES, "live", skill_dir)


def get_exit_manager_mode(skill_dir: Path | None = None) -> str:
    """Exit manager plugin mode (OFF|SHADOW|LIVE)."""
    return _get_mode("EXIT_MANAGER_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_event_risk_mode(skill_dir: Path | None = None) -> str:
    """Event-risk plugin mode (OFF|SHADOW|LIVE).

    Default promoted to ``live`` (2026-Q2 promotion) — see
    ``docs/RELEASE_NOTES_PLUGIN_PROMOTIONS.md`` and
    ``scripts/promotion_ledger.jsonl``. Invalid values still fall back to
    the operational default (``live``); explicit ``EVENT_RISK_MODE=off``
    is required to opt out.
    """
    return _get_mode("EVENT_RISK_MODE", PLUGIN_MODE_VALUES, "live", skill_dir)


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
    """Score penalty for a small/medium negative earnings surprise."""
    return _get_float("PEAD_SCORE_PENALTY", 3.0, skill_dir)


def get_pead_score_penalty_large(skill_dir: Path | None = None) -> float:
    """Score penalty for a large negative earnings surprise (default mirrors PEAD_SCORE_BOOST_LARGE).

    Symmetric counterpart to ``PEAD_SCORE_BOOST_LARGE``; applied when the
    surprise magnitude is at or below ``-15%``. Falls back to the small-miss
    penalty when unset to preserve historical behaviour.
    """
    fallback = _get_float("PEAD_SCORE_PENALTY", 3.0, skill_dir)
    return _get_float("PEAD_SCORE_PENALTY_LARGE", max(fallback, 5.0), skill_dir)


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
    - broad: keep full loaded watchlist (default when unset)
    - focused: narrows broad universes via prefilter_watchlist
    """
    env = _load_env(skill_dir)
    raw = _env_value("SIGNAL_UNIVERSE_MODE", env).strip().lower()
    if raw in {"focused", "broad"}:
        return raw
    return "broad"


def get_signal_universe_target_size(skill_dir: Path | None = None) -> int:
    """Target size for focused universe mode."""
    return _get_int("SIGNAL_UNIVERSE_TARGET_SIZE", 250, skill_dir)


def get_signal_scan_full_universe(skill_dir: Path | None = None) -> bool:
    """
    When True (default), the dynamic index watchlist path (S&P 500 + 400 + 600 + R2000)
    is not shortened by QUALITY_WATCHLIST_PREFILTER_* or SIGNAL_UNIVERSE_MODE=focused.
    Set SIGNAL_SCAN_FULL_UNIVERSE=0 to allow those filters on the full index list.
    """
    return _get_bool("SIGNAL_SCAN_FULL_UNIVERSE", True, skill_dir)


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


def get_data_integrity_min_history_coverage_pct(skill_dir: Path | None = None) -> float:
    """Minimum symbol history coverage percent required by pre-run integrity gate."""
    val = _get_float("DATA_INTEGRITY_MIN_HISTORY_COVERAGE_PCT", 95.0, skill_dir)
    return max(0.0, min(100.0, val))


def get_data_integrity_min_history_bars(skill_dir: Path | None = None) -> int:
    """Minimum bars required for a symbol to count as history-covered."""
    return _get_int("DATA_INTEGRITY_MIN_HISTORY_BARS", 260, skill_dir)


def get_data_integrity_min_pm_coverage_pct(skill_dir: Path | None = None) -> float:
    """Minimum PM PIT coverage percent required by pre-run integrity gate."""
    val = _get_float("DATA_INTEGRITY_MIN_PM_COVERAGE_PCT", 25.0, skill_dir)
    return max(0.0, min(100.0, val))


def get_data_integrity_fail_on_silent_fallback(skill_dir: Path | None = None) -> bool:
    """Fail integrity gate when unclassified/unknown provider rows are detected."""
    return _get_bool("DATA_INTEGRITY_FAIL_ON_SILENT_FALLBACK", True, skill_dir)


def get_data_integrity_max_fallback_unknown_count(skill_dir: Path | None = None) -> int:
    """Maximum allowed unknown fallback classifications before failing gate."""
    return _get_int("DATA_INTEGRITY_MAX_FALLBACK_UNKNOWN_COUNT", 0, skill_dir)


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


# --- Agent intelligence controls (default off / safe) ---


def get_mirofish_weighting_mode(skill_dir: Path | None = None) -> str:
    """Dynamic persona weighting mode (OFF|SHADOW|LIVE)."""
    return _get_mode("MIROFISH_WEIGHTING_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_mirofish_weighting_window_days(skill_dir: Path | None = None) -> int:
    """Historical window used to compute persona reliability."""
    val = _get_int("MIROFISH_WEIGHTING_WINDOW_DAYS", 60, skill_dir)
    return max(7, min(365, val))


def get_mirofish_weighting_min_samples(skill_dir: Path | None = None) -> int:
    """Minimum labeled outcomes before reliability reweighting engages."""
    val = _get_int("MIROFISH_WEIGHTING_MIN_SAMPLES", 30, skill_dir)
    return max(5, min(1000, val))


def get_mirofish_weighting_decay_half_life_days(skill_dir: Path | None = None) -> float:
    """Time-decay half-life for reliability history weighting."""
    val = _get_float("MIROFISH_WEIGHTING_DECAY_HALF_LIFE_DAYS", 20.0, skill_dir)
    return max(1.0, min(365.0, val))


def get_mirofish_weighting_max_multiplier(skill_dir: Path | None = None) -> float:
    """Upper cap for persona multiplier derived from reliability."""
    val = _get_float("MIROFISH_WEIGHTING_MAX_MULTIPLIER", 1.8, skill_dir)
    return max(1.0, min(4.0, val))


def get_mirofish_weighting_min_multiplier(skill_dir: Path | None = None) -> float:
    """Lower cap for persona multiplier derived from reliability."""
    val = _get_float("MIROFISH_WEIGHTING_MIN_MULTIPLIER", 0.5, skill_dir)
    return max(0.1, min(1.0, val))


def get_meta_policy_mode(skill_dir: Path | None = None) -> str:
    """Meta-policy rollout mode (OFF|SHADOW|LIVE)."""
    return _get_mode("META_POLICY_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_meta_policy_min_base_score(skill_dir: Path | None = None) -> float:
    """Minimum baseline score required before meta-policy can increase size."""
    val = _get_float("META_POLICY_MIN_BASE_SCORE", 40.0, skill_dir)
    return max(0.0, min(100.0, val))


def get_meta_policy_max_score_delta(skill_dir: Path | None = None) -> float:
    """Absolute clamp for meta-policy score adjustments."""
    val = _get_float("META_POLICY_MAX_SCORE_DELTA", 4.0, skill_dir)
    return max(0.0, min(20.0, val))


def get_meta_policy_size_mult_min(skill_dir: Path | None = None) -> float:
    """Lower bound for meta-policy size multipliers."""
    val = _get_float("META_POLICY_SIZE_MULT_MIN", 0.70, skill_dir)
    return max(0.1, min(1.0, val))


def get_meta_policy_size_mult_max(skill_dir: Path | None = None) -> float:
    """Upper bound for meta-policy size multipliers."""
    val = _get_float("META_POLICY_SIZE_MULT_MAX", 1.10, skill_dir)
    return max(1.0, min(3.0, val))


def get_meta_policy_suppress_threshold(skill_dir: Path | None = None) -> float:
    """Uncertainty threshold above which signals are suppressed."""
    val = _get_float("META_POLICY_SUPPRESS_THRESHOLD", 0.25, skill_dir)
    return max(0.0, min(1.0, val))


def get_meta_policy_downsize_threshold(skill_dir: Path | None = None) -> float:
    """Uncertainty threshold above which size is reduced."""
    val = _get_float("META_POLICY_DOWNSIZE_THRESHOLD", 0.45, skill_dir)
    return max(0.0, min(1.0, val))


def get_uncertainty_mode(skill_dir: Path | None = None) -> str:
    """Uncertainty plugin rollout mode (OFF|SHADOW|LIVE)."""
    return _get_mode("UNCERTAINTY_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_uncertainty_high_threshold(skill_dir: Path | None = None) -> float:
    """High uncertainty threshold."""
    val = _get_float("UNCERTAINTY_HIGH_THRESHOLD", 0.65, skill_dir)
    return max(0.0, min(1.0, val))


def get_uncertainty_med_threshold(skill_dir: Path | None = None) -> float:
    """Medium uncertainty threshold."""
    val = _get_float("UNCERTAINTY_MED_THRESHOLD", 0.45, skill_dir)
    return max(0.0, min(1.0, val))


def get_uncertainty_score_delta_penalty(skill_dir: Path | None = None) -> float:
    """Absolute score penalty applied when uncertainty is elevated."""
    val = _get_float("UNCERTAINTY_SCORE_DELTA_PENALTY", 2.0, skill_dir)
    return max(0.0, min(10.0, val))


def get_uncertainty_size_mult_floor(skill_dir: Path | None = None) -> float:
    """Minimum size multiplier allowed after uncertainty penalty."""
    val = _get_float("UNCERTAINTY_SIZE_MULT_FLOOR", 0.75, skill_dir)
    return max(0.1, min(1.0, val))


def get_counterfactual_logging_enabled(skill_dir: Path | None = None) -> bool:
    """Enable counterfactual logging for filtered/suppressed opportunities."""
    return _get_bool("COUNTERFACTUAL_LOGGING_ENABLED", False, skill_dir)


def get_counterfactual_max_horizon_days(skill_dir: Path | None = None) -> int:
    """Maximum outcome horizon tracked for counterfactual scoring."""
    val = _get_int("COUNTERFACTUAL_MAX_HORIZON_DAYS", 20, skill_dir)
    return max(1, min(252, val))


def get_counterfactual_min_labeled_samples(skill_dir: Path | None = None) -> int:
    """Minimum labeled samples before counterfactual stats are trusted."""
    val = _get_int("COUNTERFACTUAL_MIN_LABELED_SAMPLES", 100, skill_dir)
    return max(10, min(20000, val))
