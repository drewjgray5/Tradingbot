/**
 * Central UI state singleton plus the localStorage key constants used by
 * the rest of the dashboard modules.
 *
 * The `state` object is intentionally a single mutable singleton — every
 * panel reads and writes the same instance. Keep it shallow and JSON-ish so
 * a future migration to a proper store stays tractable.
 */

export const state = {
  latestSignals: [],
  /** Last watchlist size from scan diagnostics (for hero KPI). */
  lastWatchlistSize: null,
  approvingTradeId: null,
  approvingChecklist: null,
  pendingFilter: "pending",
  pendingSort: "newest",
  config: { auth_mode: "jwt" },
  allowManualJwt: true,
  publicConfig: {
    supabase: null,
    saas_mode: false,
    schwab_oauth: false,
    schwab_market_oauth: false,
    platform_live_trading_kill_switch: false,
  },
  accountMe: null,
  twoFaStatus: null,
  reportRawView: false,
  lastReportData: null,
  activeReportTab: "summary",
  secCompareResult: null,
  onboarding: null,
  profile: null,
  presetCatalog: null,
  savedUiSettings: null,
  performance: null,
  calibration: null,
  strategyChatMessages: [],
  strategyChatBusy: false,
  backtestQueueBusy: false,
  lastQuoteHealthLogSig: null,
  queueScanDraft: null,
  /** Optional scan body: strategy_overrides, universe_mode, tickers (see /api/scan). */
  scanRunOptions: null,
  sseEnabled: false,
};

/** localStorage keys used across modules. Centralised here to keep namespacing
 * consistent and to make grep/refactoring easier. */
export const UI_VIEW_MODE_KEY = "tradingbot.ui.view_mode";
export const AUTH_TOKEN_KEY = "tradingbot.jwt";
export const LEGACY_AUTH_TOKEN_KEYS = ["supabasetoken", "supabaseToken", "supabase_token"];
export const BACKTEST_PREFS_KEY = "tradingbot.backtest.preferences";
export const NOTIF_STORAGE_KEY = "tradingbot.notifications";
