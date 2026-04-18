"""
Typed, validated configuration for the webapp layer.

Replaces scattered os.getenv() calls with a single Pydantic Settings model
that validates on import and fails fast with clear errors.

Usage::

    from webapp.settings import settings
    print(settings.web_api_key)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOG = logging.getLogger("webapp.settings")

_SKILL_DIR = Path(__file__).resolve().parent.parent
_ENV_FILE = _SKILL_DIR / ".env"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    database_url: str = Field(
        default=f"sqlite:///{_SKILL_DIR / 'webapp' / 'webapp.db'}",
        alias="DATABASE_URL",
    )
    database_sslmode: str = Field(default="", alias="DATABASE_SSLMODE")
    db_pool_size: int = Field(default=5, alias="DB_POOL_SIZE", ge=1)
    db_max_overflow: int = Field(default=10, alias="DB_MAX_OVERFLOW", ge=0)
    db_pool_timeout: int = Field(default=30, alias="DB_POOL_TIMEOUT", ge=1)


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    web_api_key: str = Field(default="", alias="WEB_API_KEY")
    web_local_user_id: str = Field(default="local", alias="WEB_LOCAL_USER_ID")
    web_last_scan_signals_cap: int = Field(default=120, alias="WEB_LAST_SCAN_SIGNALS_CAP", ge=1, le=500)
    web_implementation_guide_url: str = Field(default="", alias="WEB_IMPLEMENTATION_GUIDE_URL")

    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_anon_key: str = Field(default="", alias="SUPABASE_ANON_KEY")
    supabase_jwt_secret: str = Field(default="", alias="SUPABASE_JWT_SECRET")
    supabase_jwt_secret_legacy: str = Field(default="", alias="SUPABASE_JWT_SECRET_LEGACY")

    live_trading_kill_switch: bool = Field(default=False, alias="LIVE_TRADING_KILL_SWITCH")
    max_trades_per_day: int = Field(default=20, alias="MAX_TRADES_PER_DAY", ge=1)
    max_total_account_value: float = Field(default=500000.0, alias="MAX_TOTAL_ACCOUNT_VALUE", gt=0)

    @field_validator("live_trading_kill_switch", mode="before")
    @classmethod
    def _parse_bool(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("web_local_user_id", mode="before")
    @classmethod
    def _strip_nonempty(cls, v: Any) -> str:
        s = str(v or "local").strip()
        return s if s else "local"


class ScanSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    stage2_52w_pct: float = Field(default=0.85, alias="STAGE2_52W_PCT", gt=0, le=1)
    stage2_sma_upward_days: int = Field(default=20, alias="STAGE2_SMA_UPWARD_DAYS", ge=1)
    vcp_days: int = Field(default=4, alias="VCP_DAYS", ge=1)
    signal_top_n: int = Field(default=5, alias="SIGNAL_TOP_N", ge=1)
    scan_stage_a_max_workers: int = Field(default=4, alias="SCAN_STAGE_A_MAX_WORKERS", ge=1, le=32)
    scan_stage_b_max_workers: int = Field(default=4, alias="SCAN_STAGE_B_MAX_WORKERS", ge=1, le=32)
    scan_stage_a_shortlist_multiplier: float = Field(default=3.0, alias="SCAN_STAGE_A_SHORTLIST_MULTIPLIER", gt=0)
    scan_stage_a_shortlist_cap: int = Field(default=40, alias="SCAN_STAGE_A_SHORTLIST_CAP", ge=1)
    scan_stage_task_timeout_sec: float = Field(default=120.0, alias="SCAN_STAGE_TASK_TIMEOUT_SEC", gt=0)
    scan_allow_bear_regime: bool = Field(default=False, alias="SCAN_ALLOW_BEAR_REGIME")

    breakout_confirm_enabled: bool = Field(default=True, alias="BREAKOUT_CONFIRM_ENABLED")
    breakout_confirm_min_time: int = Field(default=570, alias="BREAKOUT_CONFIRM_MIN_TIME", ge=1)

    signal_universe_mode: Literal["focused", "broad"] = Field(default="broad", alias="SIGNAL_UNIVERSE_MODE")
    signal_universe_target_size: int = Field(default=250, alias="SIGNAL_UNIVERSE_TARGET_SIZE", ge=1)
    signal_scan_full_universe: bool = Field(default=True, alias="SIGNAL_SCAN_FULL_UNIVERSE")

    @field_validator("scan_allow_bear_regime", "breakout_confirm_enabled", "signal_scan_full_universe", mode="before")
    @classmethod
    def _parse_bool(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")


class ExecutionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    execution_shadow_mode: bool = Field(default=False, alias="EXECUTION_SHADOW_MODE")
    paper_trading_enabled: bool = Field(default=False, alias="PAPER_TRADING_ENABLED")

    position_size_usd: int = Field(default=5000, alias="POSITION_SIZE_USD", ge=100)
    volatility_base_usd: int = Field(default=5000, alias="VOLATILITY_BASE_USD", ge=100)
    volatility_atr_mult: float = Field(default=2.0, alias="VOLATILITY_ATR_MULT", gt=0)
    volatility_sizing_enabled: bool = Field(default=False, alias="VOLATILITY_SIZING_ENABLED")

    exec_quality_mode: Literal["off", "shadow", "live"] = Field(default="live", alias="EXEC_QUALITY_MODE")
    exit_manager_mode: Literal["off", "shadow", "live"] = Field(default="off", alias="EXIT_MANAGER_MODE")
    event_risk_mode: Literal["off", "shadow", "live"] = Field(default="live", alias="EVENT_RISK_MODE")
    correlation_guard_mode: Literal["off", "shadow", "live"] = Field(default="off", alias="CORRELATION_GUARD_MODE")
    regime_v2_mode: Literal["off", "shadow", "live"] = Field(default="off", alias="REGIME_V2_MODE")

    quality_gates_mode: str = Field(default="shadow", alias="QUALITY_GATES_MODE")
    forensic_filter_mode: str = Field(default="shadow", alias="FORENSIC_FILTER_MODE")

    adaptive_stop_enabled: bool = Field(default=True, alias="ADAPTIVE_STOP_ENABLED")
    adaptive_stop_base_pct: float = Field(default=0.07, alias="ADAPTIVE_STOP_BASE_PCT", gt=0, lt=1)
    adaptive_stop_min_pct: float = Field(default=0.05, alias="ADAPTIVE_STOP_MIN_PCT", gt=0, lt=1)
    adaptive_stop_max_pct: float = Field(default=0.12, alias="ADAPTIVE_STOP_MAX_PCT", gt=0, lt=1)

    regime_v2_entry_min_score: int = Field(default=55, alias="REGIME_V2_ENTRY_MIN_SCORE", ge=0, le=100)

    @field_validator(
        "execution_shadow_mode", "paper_trading_enabled",
        "volatility_sizing_enabled", "adaptive_stop_enabled",
        mode="before",
    )
    @classmethod
    def _parse_bool(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")


class SaaSSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    saas_rate_scan_per_min: int = Field(default=12, alias="SAAS_RATE_SCAN_PER_MIN", ge=1)
    saas_rate_limit_window_sec: int = Field(default=60, alias="SAAS_RATE_LIMIT_WINDOW_SEC", ge=1)
    saas_scan_daily_limit: int = Field(default=200, alias="SAAS_SCAN_DAILY_LIMIT", ge=1)
    saas_scan_daily_limit_trial: int = Field(default=30, alias="SAAS_SCAN_DAILY_LIMIT_TRIAL", ge=1)
    saas_scan_cooldown_sec: int = Field(default=60, alias="SAAS_SCAN_COOLDOWN_SEC", ge=0)
    saas_rate_order_per_min: int = Field(default=30, alias="SAAS_RATE_ORDER_PER_MIN", ge=1)
    saas_rate_backtest_per_hour: int = Field(default=6, alias="SAAS_RATE_BACKTEST_PER_HOUR", ge=1)
    saas_health_require_redis: bool = Field(default=True, alias="SAAS_HEALTH_REQUIRE_REDIS")
    saas_health_require_workers: bool = Field(default=True, alias="SAAS_HEALTH_REQUIRE_WORKERS")
    saas_bootstrap_schema: bool = Field(default=False, alias="SAAS_BOOTSTRAP_SCHEMA")

    schwab_account_app_key: str = Field(default="", alias="SCHWAB_ACCOUNT_APP_KEY")
    schwab_callback_url: str = Field(default="", alias="SCHWAB_CALLBACK_URL")
    schwab_market_app_key: str = Field(default="", alias="SCHWAB_MARKET_APP_KEY")
    schwab_market_callback_url: str = Field(default="", alias="SCHWAB_MARKET_CALLBACK_URL")

    stripe_webhook_secret: str = Field(default="", alias="STRIPE_WEBHOOK_SECRET")
    stripe_checkout_success_url: str = Field(default="", alias="STRIPE_CHECKOUT_SUCCESS_URL")
    stripe_checkout_cancel_url: str = Field(default="", alias="STRIPE_CHECKOUT_CANCEL_URL")
    stripe_portal_return_url: str = Field(default="", alias="STRIPE_PORTAL_RETURN_URL")

    @field_validator("saas_health_require_redis", "saas_bootstrap_schema", mode="before")
    @classmethod
    def _parse_bool(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")


class DataQualitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    prefer_schwab_data: bool = Field(default=True, alias="PREFER_SCHWAB_DATA")
    polygon_api_key: str = Field(default="", alias="POLYGON_API_KEY")
    data_quality_exec_policy: Literal["off", "warn", "block_risk_increasing"] = Field(
        default="off", alias="DATA_QUALITY_EXEC_POLICY",
    )
    data_quote_max_age_sec: float = Field(default=600.0, alias="DATA_QUOTE_MAX_AGE_SEC", gt=0)
    data_bar_max_staleness_days: int = Field(default=7, alias="DATA_BAR_MAX_STALENESS_DAYS", ge=1)

    @field_validator("prefer_schwab_data", mode="before")
    @classmethod
    def _parse_bool(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")


class AppSettings(BaseSettings):
    """Top-level settings aggregating all groups. Validated once at import time."""

    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    scan: ScanSettings = Field(default_factory=ScanSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    saas: SaaSSettings = Field(default_factory=SaaSSettings)
    data: DataQualitySettings = Field(default_factory=DataQualitySettings)

    @property
    def api_key_configured(self) -> bool:
        return bool(self.web.web_api_key)

    @property
    def kill_switch_active(self) -> bool:
        return self.web.live_trading_kill_switch

    def non_default_summary(self) -> dict[str, Any]:
        """Return fields that differ from defaults (useful for /api/config/diff)."""
        defaults = AppSettings()
        diff: dict[str, Any] = {}
        for group_name in ("db", "web", "scan", "execution", "saas", "data"):
            current_group = getattr(self, group_name)
            default_group = getattr(defaults, group_name)
            for field_name in current_group.model_fields:
                cur = getattr(current_group, field_name)
                dflt = getattr(default_group, field_name)
                if cur != dflt:
                    diff[f"{group_name}.{field_name}"] = {"current": cur, "default": dflt}
        return diff


def _load_settings() -> AppSettings:
    try:
        return AppSettings()
    except Exception as exc:
        LOG.error("Configuration validation failed: %s", exc)
        raise SystemExit(f"Configuration validation failed:\n{exc}") from exc


settings = _load_settings()
