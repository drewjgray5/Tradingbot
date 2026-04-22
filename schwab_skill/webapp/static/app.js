/**
 * Dashboard orchestrator. The big render functions, panel-specific helpers,
 * and the bootstrap IIFE live here. Cleanly-separable concerns have been
 * pulled into ./modules/*.js — see [[static-module-layout]] in the wiki for
 * the map of what lives where.
 */

import {
  state,
  UI_VIEW_MODE_KEY,
  AUTH_TOKEN_KEY,
  LEGACY_AUTH_TOKEN_KEYS,
  BACKTEST_PREFS_KEY,
} from "./modules/state.js";
import {
  safeText,
  escapeHtml,
  safeNum,
  prettyJson,
  formatMoney,
  pct,
  formatPercentPoints,
  clampPct,
  verdictFromScore,
  timeAgo,
  durationSec,
} from "./modules/format.js";
import { api } from "./modules/api.js";
import {
  authSessionReady,
  markAuthReady,
  normalizeUserJwt,
  getApiAccessToken,
  clearLegacyApiJwtKeys,
  readStoredApiJwt,
  clearStoredApiJwt,
  ensureCookieAuthSession,
  createCookieAuthSession,
  clearCookieAuthSession,
  persistApiJwtFromSession,
  updateSupabaseAuthUI,
  setSupabaseClient,
  SUPABASE_ESM,
  isProbablyAccessJwt,
  JWT_BAD_SHAPE_HINT,
} from "./modules/auth.js";
import { showToast, addNotification, setupNotifications } from "./modules/notifications.js";
import { setupScrollToTop } from "./modules/scrollToTop.js";
import {
  clearOAuthQueryParams,
  installRouter,
} from "./modules/router.js";
import {
  setupCommandPalette,
  openCommandPalette,
  closeCommandPalette,
} from "./modules/commandPalette.js";
import { setupKeyboardShortcuts } from "./modules/shortcuts.js";
import {
  logEvent,
  updateActionCenter,
  updateActivityBadge,
  statusClass,
  sentimentTagClass,
  healthBadgeClass,
  setStatusPill,
  DIAG_LABELS,
} from "./modules/logger.js";
import {
  renderTwoFaPanel,
  refreshTwoFaStatus,
  submitEnableLiveTrading as _submitEnableLiveTradingPanel,
} from "./panels/twoFa.js";
import {
  renderOnboardingCards,
  refreshOnboarding as _refreshOnboardingPanel,
  startOnboarding as _startOnboardingPanel,
  runOnboardingStep as _runOnboardingStepPanel,
  triggerSchwabAccountOAuth,
  triggerSchwabMarketOAuth,
} from "./panels/onboarding.js";
import {
  renderCalibrationPanel,
  refreshCalibration,
  submitTradingHaltSave as _submitTradingHaltSavePanel,
} from "./panels/calibration.js";
import {
  loadDecisionCard,
  mapRecovery,
  openTradeDrawer,
  openTradeDrawerForTrade,
} from "./panels/tradeDrawer.js";
import { refreshSectors } from "./panels/sectors.js";
import {
  renderQuickCheckCard,
  quickCheck,
  renderTickerChart,
} from "./panels/quickCheck.js";
// Quick-view, decision-card, and recovery have been merged into the
// unified slide-in trade drawer (see imports above).
import {
  refreshPortfolio as _refreshPortfolioPanel,
  loadPortfolioRisk,
} from "./panels/portfolio.js";
import {
  applySecCompareMode,
  renderSecAnalysisCard,
  toReadableDeltaLabel,
  buildNarrativeSummary,
  renderSecCompareEmpty,
  renderSecCompareVisual as _renderSecCompareVisualPanel,
  buildFallbackSecCompare,
  runSecCompare as _runSecComparePanel,
} from "./panels/sec.js";
import {
  renderReportTabs,
  renderReportVisual,
  applyReportViewMode,
  runReport,
} from "./panels/report.js";
import {
  PRESET_SETTING_LABELS,
  presetSettingLabel,
  renderProfilePanel,
  renderPresetApplyPreview,
  loadProfiles,
  applyProfile,
} from "./panels/profile.js";
import {
  renderPerformancePanel as _renderPerformancePanel,
  renderChallengerPanel,
  renderEvolvePanel,
  refreshPerformance as _refreshPerformancePanel,
} from "./panels/performance.js";
import {
  setDefaultBacktestDates,
  restoreBacktestFormFromStorage,
  wireBacktestFormPersistence,
  resetBacktestFormToDefaults,
  setBacktestQueueUiBusy,
  setBtMetaMessage,
  syncBtUniverseRow,
  applyBacktestPresetYears,
  collectBacktestOverrides,
  collectBacktestSpecFromForm,
  renderBacktestResultSummary,
  renderBacktestResultRaw as _renderBacktestResultRawPanel,
  backtestSpecSummaryLine,
  switchBacktestHubTab,
  refreshBacktestRuns,
  pollBacktestTask as _pollBacktestTaskPanel,
  queueUserBacktest as _queueUserBacktestPanel,
} from "./panels/backtest.js";
import {
  strategyChatPayloadMessages,
  scrollStrategyChatToEnd,
  renderStrategyChatMessages,
  hideScQueueCallout,
  showScQueueCallout as _showScQueueCalloutPanel,
  sendStrategyChat as _sendStrategyChatPanel,
} from "./panels/strategyChat.js";

// Thin wrappers preserve the call signatures used by `wireEvents`,
// `connectSSE`, `runLazyApi`, etc. without leaking the panel-module
// dependency-injection contract into every call site.
const submitEnableLiveTrading = () =>
  _submitEnableLiveTradingPanel({ refreshAccountMe, refreshPending });
const refreshOnboarding = () => _refreshOnboardingPanel({ runLazyApi });
const startOnboarding = () => _startOnboardingPanel({ runLazyApi });
const runOnboardingStep = (step) => _runOnboardingStepPanel(step, { runLazyApi });
const submitTradingHaltSave = () =>
  _submitTradingHaltSavePanel({ refreshAccountMe });
const refreshPortfolio = () => _refreshPortfolioPanel({ runScan });
const renderSecCompareVisual = (data) =>
  _renderSecCompareVisualPanel(data, { getDisplayMode });
const runSecCompare = () => _runSecComparePanel({ getDisplayMode });
const refreshPerformance = () => _refreshPerformancePanel({ getDisplayMode });
const renderPerformancePanel = (rootEl, data, opts = {}) =>
  _renderPerformancePanel(rootEl, data, { ...opts, getDisplayMode });
const renderBacktestResultRaw = (result, fallbackText) =>
  _renderBacktestResultRawPanel(result, fallbackText, { getDisplayMode });
const pollBacktestTask = (taskId) =>
  _pollBacktestTaskPanel(taskId, { setJobProgress, getDisplayMode });
const queueUserBacktest = () =>
  _queueUserBacktestPanel({ setJobProgress, getDisplayMode });
const showScQueueCallout = (taskId, runId) =>
  _showScQueueCalloutPanel(taskId, runId, { switchBacktestHubTab });
const sendStrategyChat = () =>
  _sendStrategyChatPanel({ refreshBacktestRuns, switchBacktestHubTab });

const lazyLoaded = {
  portfolio: false,
  sectors: false,
  performance: false,
  backtest: false,
  onboarding: false,
  profiles: false,
  calibration: false,
};

function resetLazyLoaded() {
  Object.keys(lazyLoaded).forEach((k) => {
    lazyLoaded[k] = false;
  });
}

function getDisplayMode() {
  const m = localStorage.getItem(UI_VIEW_MODE_KEY) || "standard";
  return ["simple", "standard", "pro"].includes(m) ? m : "standard";
}

function applyDisplayMode(mode) {
  const m = ["simple", "standard", "pro"].includes(mode) ? mode : "standard";
  localStorage.setItem(UI_VIEW_MODE_KEY, m);
  document.body.classList.remove("ui-simple", "ui-standard", "ui-pro");
  document.body.classList.add(`ui-${m}`);
  const sel = document.getElementById("displayModeSelect");
  if (sel) sel.value = m;
  const pro = m === "pro";
  const scanDiag = document.getElementById("scanDiagnosticsPanel");
  const statusDet = document.getElementById("statusDetailsPanel");
  const secDeep = document.getElementById("secCompareDeepPanel");
  if (scanDiag) scanDiag.open = pro;
  if (statusDet) statusDet.open = pro;
  if (secDeep) secDeep.open = pro;
  const perfRaw = document.getElementById("performanceRawDetails");
  if (perfRaw && !pro) perfRaw.open = false;
}

async function runLazyApi(key) {
  if (!key || lazyLoaded[key]) return;
  lazyLoaded[key] = true;
  try {
    if (key === "portfolio") await refreshPortfolio();
    else if (key === "sectors") await refreshSectors();
    else if (key === "performance") await refreshPerformance();
    else if (key === "backtest") await refreshBacktestRuns();
    else if (key === "onboarding") await refreshOnboarding();
    else if (key === "profiles") {
      await loadProfiles();
    } else if (key === "calibration") {
      await refreshCalibration();
    }
  } catch (err) {
    console.warn("lazy load failed", key, err);
    lazyLoaded[key] = false;
  }
}

function setupLazySectionLoading() {
  const nodes = document.querySelectorAll("[data-lazy-api]");
  if (!nodes.length) return;
  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        const k = e.target.getAttribute("data-lazy-api");
        if (k) void runLazyApi(k);
      });
    },
    { rootMargin: "120px 0px", threshold: 0.04 }
  );
  nodes.forEach((n) => io.observe(n));
}

function markDeferredDataPlaceholders() {
  const pb = document.getElementById("portfolioBody");
  const firstCell = pb?.querySelector("td");
  if (pb && firstCell && firstCell.textContent === "Loading...") {
    pb.innerHTML = `<tr><td colspan="5" class="muted">Portfolio loads when you scroll here (or use Refresh All).</td></tr>`;
  }
  const pm = document.getElementById("portfolioMeta");
  if (pm && pm.textContent === "Loading...") pm.textContent = "Not loaded yet";
}

function renderLiveTradingSaasPanel() {
  const block = document.getElementById("liveTradingSaasBlock");
  const killBanner = document.getElementById("platformKillSwitchBanner");
  if (killBanner) {
    if (state.publicConfig.platform_live_trading_kill_switch) {
      killBanner.classList.remove("hidden");
    } else {
      killBanner.classList.add("hidden");
    }
  }
  if (!block) return;
  if (!state.publicConfig.saas_mode) {
    block.classList.add("hidden");
    return;
  }
  block.classList.remove("hidden");
  const statusEl = document.getElementById("liveTradingStatus");
  if (statusEl) {
    const on = Boolean(state.accountMe?.live_execution_enabled);
    const halted = Boolean(state.accountMe?.trading_halted);
    let line = on
      ? "Account status: live orders from this app are on."
      : "Account status: live orders from this app are still off.";
    if (halted) line += " Trading pause is on (new approvals blocked).";
    statusEl.textContent = line;
  }
  const haltCb = document.getElementById("tradingHaltedCheckbox");
  if (haltCb && state.accountMe) {
    haltCb.checked = Boolean(state.accountMe.trading_halted);
  }
}

async function refreshAccountMe() {
  if (!state.publicConfig.saas_mode) {
    state.accountMe = null;
    renderLiveTradingSaasPanel();
    return;
  }
  const token = await getApiAccessToken();
  if (!token) {
    state.accountMe = null;
    renderLiveTradingSaasPanel();
    return;
  }
  const out = await api.get("/api/me");
  state.accountMe = out.ok ? out.data : null;
  renderLiveTradingSaasPanel();
}

async function refreshCritical() {
  await Promise.all([refreshStatus(), refreshAccountMe(), refreshPending(), refreshTwoFaStatus()]);
}

function setJobProgress(barId, labelId, fraction, labelText) {
  const bar = document.getElementById(barId);
  const lbl = labelId ? document.getElementById(labelId) : null;
  const wrap = bar?.closest?.(".job-progress-wrap");
  if (bar && bar.tagName === "PROGRESS") {
    const pct = Math.max(0, Math.min(100, Math.round((fraction || 0) * 100)));
    bar.value = pct;
    if (wrap) wrap.classList.toggle("hidden", pct <= 0 && !labelText);
  }
  if (lbl) lbl.textContent = labelText || "";
}

async function initSupabaseAuth(url, anonKey) {
  let createClient;
  try {
    const mod = await import(SUPABASE_ESM);
    createClient = mod.createClient;
  } catch (err) {
    console.warn("Supabase client SDK failed to load", err);
    logEvent({
      kind: "system",
      severity: "warn",
      message: "Could not load Supabase from CDN; use manual JWT below.",
    });
    markAuthReady();
    return;
  }

  const sb = createClient(url, anonKey, {
    auth: {
      autoRefreshToken: true,
      persistSession: true,
      detectSessionInUrl: true,
    },
  });
  setSupabaseClient(sb);

  const {
    data: { session },
  } = await sb.auth.getSession();
  persistApiJwtFromSession(session);
  updateSupabaseAuthUI(session);

  sb.auth.onAuthStateChange((_event, nextSession) => {
    persistApiJwtFromSession(nextSession);
    updateSupabaseAuthUI(nextSession);
    void refreshAccountMe();
  });

  document.getElementById("supabaseSignInBtn")?.addEventListener("click", async () => {
    const email = document.getElementById("supabaseEmail")?.value?.trim() || "";
    const password = document.getElementById("supabasePassword")?.value || "";
    if (!email || !password) {
      logEvent({ kind: "system", severity: "warn", message: "Enter email and password." });
      return;
    }
    const { error } = await sb.auth.signInWithPassword({ email, password });
    if (error) logEvent({ kind: "system", severity: "error", message: error.message });
    else logEvent({ kind: "system", severity: "info", message: "Signed in." });
  });

  document.getElementById("supabaseSignUpBtn")?.addEventListener("click", async () => {
    const email = document.getElementById("supabaseEmail")?.value?.trim() || "";
    const password = document.getElementById("supabasePassword")?.value || "";
    if (!email || !password) {
      logEvent({ kind: "system", severity: "warn", message: "Enter email and password to sign up." });
      return;
    }
    const { error } = await sb.auth.signUp({ email, password });
    if (error) logEvent({ kind: "system", severity: "error", message: error.message });
    else {
      logEvent({
        kind: "system",
        severity: "info",
        message: "Sign-up sent. Check email if confirmation is required, then sign in.",
      });
    }
  });

  document.getElementById("supabaseSignOutBtn")?.addEventListener("click", async () => {
    await sb.auth.signOut();
    clearStoredApiJwt();
    await clearCookieAuthSession();
    const inp = document.getElementById("jwtInput");
    if (inp) inp.value = "";
    logEvent({ kind: "system", severity: "info", message: "Signed out." });
  });

  markAuthReady();
}

function renderValidationRecentSteps(validation = {}) {
  const listEl = document.getElementById("validationRecentSteps");
  const wrapEl = document.getElementById("validationRecentWrap");
  if (!listEl || !wrapEl) return;
  listEl.innerHTML = "";
  const rows = Array.isArray(validation.results) ? validation.results : [];
  if (!rows.length) {
    listEl.innerHTML = `<li class="muted">No validation steps yet.</li>`;
    return;
  }
  const lastFive = rows.slice(-5).reverse();
  lastFive.forEach((step) => {
    const name = safeText(step.name || "unknown_step");
    const rc = safeNum(step.returncode, 1);
    const status = rc === 0 ? "PASS" : "FAIL";
    const seconds = durationSec(step.started_at, step.ended_at);
    const durText = seconds === null ? "n/a" : `${seconds}s`;
    const li = document.createElement("li");
    li.innerHTML = `${name}: <strong>${status}</strong> (${durText})`;
    listEl.appendChild(li);
  });
}

function buildScanMeta(signals = [], count = null) {
  const total = count ?? signals.length;
  const high = signals.filter((s) => (s?.advisory?.confidence_bucket || "").toLowerCase() === "high").length;
  if (high > 0) return `Found ${total} signal(s). High-confidence: ${high}.`;
  return `Found ${total} signal(s).`;
}

function diagnosticsHeadline(diagOrSummary = null) {
  if (!diagOrSummary || typeof diagOrSummary !== "object") return "";
  const headline = safeText(diagOrSummary.headline || "").trim();
  if (headline && headline !== "—") return headline;
  const dq = safeText(diagOrSummary.data_quality || "").trim().toLowerCase();
  if (dq && dq !== "ok") {
    const rs = Array.isArray(diagOrSummary.data_quality_reasons)
      ? diagOrSummary.data_quality_reasons
      : [];
    const rtxt = rs.slice(0, 2).map((x) => safeText(x)).filter(Boolean).join("; ");
    return rtxt ? `Data quality: ${dq} — ${rtxt}.` : `Data quality: ${dq}.`;
  }
  if (safeNum(diagOrSummary.scan_blocked, 0) > 0) {
    const reason = safeText(diagOrSummary.scan_blocked_reason || "").trim();
    if (reason === "bear_regime_spy_below_200sma") {
      return "Scan blocked by regime gate: SPY is below 200 SMA.";
    }
    return "Scan blocked by active risk gates.";
  }
  return "";
}

function formatStrategySummary(summary = null) {
  if (!summary || typeof summary !== "object") return "";
  const dominant = safeText(summary.dominant_live_strategy || "");
  const total = safeNum(summary.total_ranked, 0);
  const count = safeNum(summary.dominant_count, 0);
  if (!dominant || dominant === "—" || total <= 0 || count <= 0) return "";
  return ` Dominant strategy: ${dominant} (${count}/${total}).`;
}

function updateTopStrategyChip(summary = null) {
  const el = document.getElementById("scanTopStrategy");
  if (!el) return;
  const dominant = safeText(summary?.dominant_live_strategy || "—");
  const total = safeNum(summary?.total_ranked, 0);
  const count = safeNum(summary?.dominant_count, 0);
  if (dominant === "—" || total <= 0 || count <= 0) {
    el.textContent = "Top Strategy: --";
    return;
  }
  el.textContent = `Top Strategy: ${dominant} (${count}/${total})`;
}

function setHealthRibbonTiles(authOk, quoteOk, errRate, validation) {
  const setTile = (id, stateName, gauge) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.dataset.state = stateName;
    el.style.setProperty("--gauge", String(gauge));
  };
  setTile("healthTileAuth", authOk ? "good" : "bad", authOk ? 1 : 0);
  setTile("healthTileQuotes", quoteOk ? "good" : "bad", quoteOk ? 1 : 0);
  const er = safeNum(errRate, 0);
  const apiGaugeHealth = Math.max(0, Math.min(1, 1 - er / 18));
  const apiState = er < 2 ? "good" : er < 8 ? "warn" : "bad";
  setTile("healthTileApi", apiState, apiGaugeHealth);

  const v = validation || {};
  const runStatus = safeText(v.run_status || "").toLowerCase();
  let vState = "neutral";
  let vGauge = 0.35;
  if (v.exists && v.passed === true) {
    vState = "good";
    vGauge = 1;
  } else if (v.exists && v.passed === false) {
    vState = "bad";
    vGauge = 0.12;
  } else if (runStatus === "running") {
    vState = "warn";
    const pct = safeNum(v.progress_pct, 0);
    vGauge = Math.max(0.25, Math.min(0.92, pct > 0 ? pct / 100 : 0.55));
  } else if (v.exists) {
    vState = "warn";
    vGauge = 0.55;
  }
  setTile("healthTileValidation", vState, vGauge);
}

function prioritizeActionCenterFromHealth({ authOk, quoteOk, errRate, validation, topBlocker, quoteHealth }) {
  const runStatus = safeText(validation?.run_status || "").toLowerCase();
  const blocker = safeText(topBlocker || "").trim();
  if (!authOk) {
    updateActionCenter({
      title: "P0: Broker Authentication Blocked",
      message: "Reconnect Schwab account and market sessions before running scans or approving orders.",
      severity: "error",
    });
    return;
  }
  if (!quoteOk || errRate >= 3.0) {
    const qh = quoteHealth && typeof quoteHealth === "object" ? quoteHealth : {};
    const quoteReason = safeText(qh.reason || "").trim();
    const quoteHint = safeText(qh.operator_hint || "").trim();
    const quoteMsg = quoteOk
      ? ""
      : `Quotes unhealthy${quoteReason ? ` (${quoteReason})` : ""}${quoteHint ? `: ${quoteHint}` : "."}`;
    const apiMsg = `API server error rate is ${errRate.toFixed(1)}%.`;
    const message =
      !quoteOk && errRate >= 3.0
        ? `${quoteMsg} ${apiMsg} Check provider status and fallback readiness.`
        : !quoteOk
          ? `${quoteMsg} Check provider status and fallback readiness.`
          : `${apiMsg} Check provider status and fallback readiness.`;
    updateActionCenter({
      title: "P1: Market Data Reliability Degraded",
      message,
      severity: "warn",
    });
    return;
  }
  if (runStatus === "running") {
    updateActionCenter({
      title: "P2: Validation In Progress",
      message: "Validation pipeline is running; monitor progress before trusting new model outputs.",
      severity: "info",
    });
    return;
  }
  if (blocker) {
    updateActionCenter({
      title: "P2: Scan Blocker Identified",
      message: blocker,
      severity: "warn",
    });
  }
}

function updateHeroInfographic() {
  const sigEl = document.getElementById("heroKpiSignals");
  const pendEl = document.getElementById("heroKpiPending");
  const wlEl = document.getElementById("heroKpiWatchlist");
  if (sigEl) sigEl.textContent = String(Array.isArray(state.latestSignals) ? state.latestSignals.length : 0);
  if (pendEl) {
    const pc = document.getElementById("pendingCount");
    pendEl.textContent = pc && pc.textContent.trim() !== "" ? pc.textContent.trim() : "0";
  }
  if (wlEl) {
    const n = state.lastWatchlistSize;
    wlEl.textContent = n !== null && n !== undefined && n >= 0 ? String(n) : "—";
  }
}

function setLoading(textMap = {}) {
  if (textMap.scan) document.getElementById("scanMeta").textContent = textMap.scan;
  if (textMap.portfolio) document.getElementById("portfolioMeta").textContent = textMap.portfolio;
}

function buildDiagnosticsSummary(diag = {}) {
  const blockers = Object.entries(diag)
    .filter(([k, v]) => safeNum(v, 0) > 0 && !["watchlist_size"].includes(k))
    .map(([k, v]) => ({
      key: k,
      label: DIAG_LABELS[k] || k.replaceAll("_", " "),
      value: safeNum(v, 0),
      severity: ["exceptions", "df_empty"].includes(k) ? "error" : "warn",
    }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 5);

  const watch = safeNum(diag.watchlist_size, 0);
  const stageFail = safeNum(diag.stage2_fail, 0);
  const vcpFail = safeNum(diag.vcp_fail, 0);
  const finalSignals = state.latestSignals.length;

  const funnel = {
    watchlist: watch,
    stage2_pass: Math.max(0, watch - stageFail),
    vcp_pass: Math.max(0, watch - stageFail - vcpFail),
    final: finalSignals,
  };

  return { blockers, funnel };
}

function renderDiagnostics(diag = {}) {
  const chipWrap = document.getElementById("scanDiagnostics");
  const blockersEl = document.getElementById("scanBlockers");
  const funnelEl = document.getElementById("scanFunnel");
  const alertWrap = document.getElementById("blockersAlertSection");
  const alertList = document.getElementById("blockersAlertList");
  chipWrap.innerHTML = "";
  blockersEl.innerHTML = "";
  funnelEl.innerHTML = "";
  if (alertList) alertList.innerHTML = "";

  const dq = safeText(diag.data_quality || "").trim();
  if (dq) {
    const rs = Array.isArray(diag.data_quality_reasons) ? diag.data_quality_reasons : [];
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent =
      rs.length > 0
        ? `Data quality: ${dq} (${rs.slice(0, 2).map((x) => safeText(x)).join("; ")})`
        : `Data quality: ${dq}`;
    chipWrap.appendChild(chip);
  }

  const summary = buildDiagnosticsSummary(diag);
  if (!summary.blockers.length) {
    blockersEl.innerHTML = `<li>No major blockers detected.</li>`;
    if (alertWrap) alertWrap.classList.add("hidden");
  } else {
    summary.blockers.forEach((b) => {
      const li = document.createElement("li");
      li.innerHTML = `${b.label}: <strong>${b.value}</strong> <span class="${statusClass(b.severity)}">${b.severity}</span>`;
      blockersEl.appendChild(li);
      if (alertList) {
        const alertLi = document.createElement("li");
        alertLi.innerHTML = `${b.label}: <strong>${b.value}</strong>`;
        alertList.appendChild(alertLi);
      }
    });
    if (alertWrap) alertWrap.classList.remove("hidden");
  }

  const funnelPairs = [
    ["Watchlist", summary.funnel.watchlist],
    ["Stage2 Pass", summary.funnel.stage2_pass],
    ["VCP Pass", summary.funnel.vcp_pass],
    ["Final", summary.funnel.final],
  ];
  const funnelVals = funnelPairs.map(([, v]) => safeNum(v, 0));
  const funnelMax = Math.max(1, ...funnelVals);

  funnelPairs.forEach(([label, value], i) => {
    const n = safeNum(value, 0);
    const pct = Math.round((n / funnelMax) * 100);
    const hue = 200 - i * 22;
    const node = document.createElement("div");
    node.className = "funnel-node";
    node.innerHTML = `
      <div class="funnel-node-head">
        <span class="label">${label}</span>
        <span class="funnel-node-pct mono-nums">${pct}%</span>
      </div>
      <div class="funnel-bar-track" aria-hidden="true">
        <div class="funnel-bar-fill" style="width:${pct}%;--funnel-hue:${hue}"></div>
      </div>
      <div class="value mono-nums">${n}</div>
    `;
    funnelEl.appendChild(node);
  });

  Object.entries(diag).slice(0, 8).forEach(([key, value]) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${DIAG_LABELS[key] || key}: ${value}`;
    chipWrap.appendChild(chip);
  });
  state.lastWatchlistSize = summary.funnel.watchlist;
  updateHeroInfographic();
  const diagPanel = document.getElementById("scanDiagnosticsPanel");
  if (diagPanel && getDisplayMode() === "pro") diagPanel.open = true;
}

function renderScanRows(signals = []) {
  const body = document.getElementById("scanTableBody");
  body.innerHTML = "";
  if (!signals.length) {
    body.innerHTML = `
      <tr>
        <td colspan="9" class="muted">
          <div class="empty-state-cell">
            <svg class="empty-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 8h16M6 12h12M9 16h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
              <rect x="3" y="4" width="18" height="16" rx="2.5" stroke="currentColor" stroke-width="1.5"/>
            </svg>
            <div>No signal candidates yet.</div>
            <button id="scanEmptyCtaBtn" class="btn small secondary" type="button">Run Scan to Begin</button>
          </div>
        </td>
      </tr>
    `;
    const cta = document.getElementById("scanEmptyCtaBtn");
    if (cta) cta.addEventListener("click", runScan);
    updateHeroInfographic();
    return;
  }

  signals.forEach((sig, idx) => {
    const ticker = sig.ticker || sig.symbol || "?";
    const topLive = safeText(sig?.strategy_attribution?.top_live || "—");
    const score = safeNum(sig.signal_score ?? sig.score, null);
    const conviction = safeNum(sig.mirofish_conviction, null);
    const advisory = sig.advisory || {};
    const pUp = safeNum(advisory.p_up_10d, null);
    const conf = safeText(advisory.confidence_bucket || "—").toUpperCase();
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${safeText(ticker)}</strong></td>
      <td><span class="pill info strategy-badge">${topLive}</span></td>
      <td>${sig.price || sig.current_price ? formatMoney(sig.price || sig.current_price) : "—"}</td>
      <td>${score !== null ? `${score.toFixed(1)}` : "—"}</td>
      <td>${pUp !== null ? pct(pUp, 1) : "—"}</td>
      <td>${conf}</td>
      <td>${conviction !== null ? `${conviction}` : "—"}</td>
      <td>${safeText(sig.sector_etf || "—")}</td>
      <td><button type="button" class="btn small secondary" data-idx="${idx}">Stage…</button></td>
    `;
    body.appendChild(tr);
  });

  body.querySelectorAll("button[data-idx]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const idx = Number(e.currentTarget.getAttribute("data-idx"));
      const sig = state.latestSignals[idx];
      openQueueScanDialog(sig);
    });
  });
  updateHeroInfographic();
}

function getSectorKeyFromTrade(row) {
  const sector = row?.signal?.sector_etf || "Unknown";
  return String(sector || "Unknown").toUpperCase();
}

function meterFromScore(score) {
  return clampPct(safeNum(score, 0));
}

function meterFromConviction(conviction) {
  return clampPct((safeNum(conviction, 0) + 100) / 2);
}

function renderPendingContext(row) {
  const sig = row.signal || {};
  const score = sig.signal_score ?? sig.score;
  const sector = sig.sector_etf;
  const conviction = sig.mirofish_conviction;
  return `score: ${score !== undefined ? safeNum(score).toFixed(0) : "—"}<br/>
    sector: ${safeText(sector || "—")}<br/>
    conviction: ${conviction !== undefined ? safeText(conviction) : "—"}`;
}

function renderTimeline(row) {
  const status = (row.status || "").toLowerCase();
  if (status === "pending") return `<span class="timeline-badge"><span class="timeline-dot"></span>queued -> waiting action</span>`;
  if (status === "executed") return `<span class="timeline-badge"><span class="timeline-dot"></span>queued -> approved -> executed</span>`;
  if (status === "rejected") return `<span class="timeline-badge"><span class="timeline-dot"></span>queued -> rejected</span>`;
  if (status === "failed") return `<span class="timeline-badge"><span class="timeline-dot"></span>queued -> approve attempted -> failed</span>`;
  return `<span class="timeline-badge"><span class="timeline-dot"></span>${safeText(status)}</span>`;
}

function formatPreflightChecklistHtml(c) {
  if (!c || typeof c !== "object") return "";
  const lines = Array.isArray(c.checklist_lines) ? c.checklist_lines : [];
  const plainItems = lines
    .map((line) => {
      if (!line || typeof line !== "object") return "";
      const lb = safeText(line.label);
      const vl = safeText(line.value_plain);
      return `<li><strong>${lb}:</strong> ${vl}</li>`;
    })
    .filter(Boolean)
    .join("");
  let blockSection = "";
  if (c.blocked) {
    const br = Array.isArray(c.block_reasons_plain) ? c.block_reasons_plain : [];
    const brHtml = br.length ? br.map((t) => `<li>${safeText(t)}</li>`).join("") : "";
    const fallback = brHtml || "<li>Policy blocked this order.</li>";
    blockSection = `<p class="approve-blocked"><strong>Cannot send yet</strong></p><ul>${fallback}</ul>`;
  }
  const techJson = safeText(prettyJson(c));
  const tech = `<details class="approve-checklist-details"><summary>Technical checklist</summary><pre class="code-block code-block--tight">${techJson}</pre></details>`;
  return `<div class="approve-preflight"><strong>Pre-trade summary</strong><ul>${plainItems || "<li>No extra checklist rows.</li>"}</ul>${blockSection}${tech}</div>`;
}

async function openApproveDialog(row) {
  const dialog = document.getElementById("approveDialog");
  const summary = document.getElementById("approveSummary");
  const est = safeNum(row.price, 0) * safeNum(row.qty, 0);
  const sig = row.signal || {};
  const riskHint = (!sig.sector_etf || safeNum(sig.signal_score, 0) < 60)
    ? "Caution: missing sector or lower-confidence setup."
    : "Setup context looks complete.";
  let checklistText = "";
  const preflight = await api.get(`/api/trades/${row.id}/preflight`);
  if (preflight.ok) {
    state.approvingChecklist = preflight.data?.checklist || null;
    const c = state.approvingChecklist || {};
    const hv = preflight.data?.high_value_2fa || {};
    checklistText = formatPreflightChecklistHtml(c);
    if (hv.required) {
      checklistText += `<p class="muted"><strong>High-value guardrail:</strong> 2FA code required for this approval.</p>`;
    }
  } else {
    checklistText = `<div class="approve-preflight muted">Checklist unavailable: ${safeText(preflight.error)}</div>`;
  }
  summary.innerHTML = `
    Approve BUY ${row.qty} ${row.ticker} @ ${row.price ? formatMoney(row.price) : "market"}?<br/>
    Est. value: <strong>${formatMoney(est)}</strong><br/>
    <span class="muted">${riskHint}</span>
    ${checklistText}
  `;
  const tickerInput = document.getElementById("approveTickerInput");
  const otpInput = document.getElementById("approveOtpInput");
  if (tickerInput) {
    tickerInput.value = "";
    tickerInput.placeholder = String(row.ticker || "TICKER");
  }
  if (otpInput) otpInput.value = "";
  state.approvingTradeId = row.id;
  dialog.showModal();
}

function applySchwabConnectButtonVisibility() {
  const pc = state.publicConfig || {};
  document.getElementById("onboardingSchwabBtn")?.classList.toggle("hidden", !pc.schwab_oauth);
  document.getElementById("onboardingSchwabMarketBtn")?.classList.toggle("hidden", !pc.schwab_market_oauth);
  document.getElementById("onboardingSchwabLink")?.classList.toggle("hidden", !pc.schwab_oauth);
  document.getElementById("onboardingSchwabMarketLink")?.classList.toggle("hidden", !pc.schwab_market_oauth);
}

async function loadConfig() {
  const tokenInput = document.getElementById("jwtInput");
  const saveBtn = document.getElementById("saveJwtBtn");
  const manualDetails = document.getElementById("manualJwtDetails");
  const manualSummary = document.getElementById("manualJwtSummary");
  const supabaseBlock = document.getElementById("supabaseAuthBlock");

  let publicCfg = {
    supabase: null,
    saas_mode: false,
    schwab_oauth: false,
    schwab_market_oauth: false,
    auth_setup: null,
  };
  try {
    const res = await fetch("/api/public-config", { headers: { Accept: "application/json" } });
    const body = res.ok ? await res.json() : {};
    if (body?.ok && body?.data) publicCfg = { ...publicCfg, ...body.data };
  } catch {
    /* offline or boot — fall back to manual JWT only */
  }
  state.publicConfig = publicCfg;
  state.sseEnabled = publicCfg?.sse_enabled === true;
  state.allowManualJwt = publicCfg?.manual_jwt_entry_enabled !== false;
  if (publicCfg.api_key_required && !localStorage.getItem("tradingbot.api_key")) {
    const key = prompt("This server requires an API key for write operations.\nEnter your WEB_API_KEY:");
    if (key) localStorage.setItem("tradingbot.api_key", key.trim());
  }
  applySchwabConnectButtonVisibility();
  renderLiveTradingSaasPanel();

  const implLink = document.getElementById("implementationGuideLink");
  const implUrl = (publicCfg?.implementation_guide_url || "").trim();
  if (implLink) {
    if (implUrl) {
      implLink.href = implUrl;
      implLink.classList.remove("hidden");
    } else {
      implLink.classList.add("hidden");
      implLink.setAttribute("href", "#");
    }
  }

  const hasSupabaseUi = Boolean(publicCfg?.supabase?.url && publicCfg?.supabase?.anon_key);
  const manualJwtAllowed = Boolean(state.allowManualJwt);
  if (!manualJwtAllowed) clearStoredApiJwt();
  if (hasSupabaseUi && supabaseBlock) {
    supabaseBlock.classList.remove("hidden");
    if (manualDetails) {
      manualDetails.classList.add("hidden");
      manualDetails.open = false;
    }
    await initSupabaseAuth(publicCfg.supabase.url, publicCfg.supabase.anon_key);
  } else {
    if (supabaseBlock) supabaseBlock.classList.add("hidden");
    if (manualDetails) {
      manualDetails.classList.toggle("hidden", !manualJwtAllowed);
      manualDetails.open = false;
    }
    if (manualSummary) {
      manualSummary.textContent = "Session token";
      manualSummary.classList.add("manual-jwt-summary--hidden");
    }
    markAuthReady();
  }

  if (tokenInput) {
    tokenInput.value = manualJwtAllowed ? readStoredApiJwt() : "";
    tokenInput.disabled = !manualJwtAllowed;
  }
  if (saveBtn) {
    saveBtn.disabled = !manualJwtAllowed;
    saveBtn.addEventListener("click", () => {
      if (!manualJwtAllowed) return;
      const val = normalizeUserJwt(tokenInput?.value);
      if (val) {
        if (!isProbablyAccessJwt(val)) {
          logEvent({ kind: "system", severity: "error", message: JWT_BAD_SHAPE_HINT });
          return;
        }
        localStorage.setItem(AUTH_TOKEN_KEY, val);
        clearLegacyApiJwtKeys();
        void createCookieAuthSession(val);
        logEvent({ kind: "system", severity: "info", message: "JWT token saved locally." });
      } else {
        clearStoredApiJwt();
        void clearCookieAuthSession();
        logEvent({ kind: "system", severity: "warn", message: "JWT token cleared." });
      }
    });
  }
  state.config = { auth_mode: hasSupabaseUi ? "supabase" : "jwt" };
  const authSetup = publicCfg?.auth_setup && typeof publicCfg.auth_setup === "object" ? publicCfg.auth_setup : {};
  const saasHost = Boolean(publicCfg?.saas_mode);
  const originHint = window.location.origin || "";
  const jwtReady =
    authSetup.jwt_verification_ready === true ||
    (authSetup.jwt_verification_ready === undefined && authSetup.jwt_secret_configured === true);
  if (saasHost && !jwtReady) {
    updateActionCenter({
      title: "Server cannot verify Supabase tokens",
      message:
        "Set SUPABASE_URL (for ES256/RS256 JWKS) and/or SUPABASE_JWT_SECRET (for legacy HS256) from Supabase → Project Settings → API on your host (e.g. Render → Environment), then redeploy.",
      severity: "error",
    });
  } else if (saasHost && authSetup.supabase_sign_in_available === false) {
    updateActionCenter({
      title: "Hosted sign-in not configured",
      message: hasSupabaseUi
        ? "Sign in with Supabase to access protected APIs. Your session token is used automatically."
        : `This server did not expose Supabase browser sign-in (set SUPABASE_URL and SUPABASE_ANON_KEY in Render to match your local .env). In Supabase → Authentication → URL configuration, add ${originHint} to Site URL and Redirect URLs.`,
      severity: "warn",
    });
  } else {
    updateActionCenter({
      title: "Authentication Required",
      message: hasSupabaseUi
        ? "Sign in with Supabase to access protected APIs. Your session token is used automatically."
        : "Sign in with Supabase to access protected APIs.",
      severity: "warn",
    });
  }

  const params = new URLSearchParams(window.location.search);
  const oauthSt = params.get("schwab_oauth");
  const marketOauthSt = params.get("schwab_market_oauth");
  if (oauthSt || marketOauthSt) {
    const msg = params.get("message") || "";
    clearOAuthQueryParams(["schwab_oauth", "schwab_market_oauth", "message"]);
    applySchwabConnectButtonVisibility();

    if (oauthSt) {
      if (oauthSt === "ok") {
        logEvent({ kind: "system", severity: "info", message: "Schwab account linked successfully." });
        updateActionCenter({
          title: "Schwab",
          message: "Brokerage side linked (balances, positions, orders). If you have not yet, also connect market data.",
          severity: "success",
        });
        try { showToast("Schwab account linked.", "success", 4000); } catch { /* ignore */ }
      } else {
        logEvent({ kind: "system", severity: "error", message: `Schwab OAuth: ${msg || "failed"}` });
        updateActionCenter({ title: "Schwab OAuth", message: msg || "Connection failed.", severity: "error" });
        try { showToast(`Schwab OAuth: ${msg || "failed"}`, "error", 6000); } catch { /* ignore */ }
      }
    }
    if (marketOauthSt) {
      if (marketOauthSt === "ok") {
        logEvent({ kind: "system", severity: "info", message: "Schwab market data linked successfully." });
        updateActionCenter({
          title: "Schwab market",
          message: "Market data linked (quotes and history for scans).",
          severity: "success",
        });
        try { showToast("Schwab market data linked.", "success", 4000); } catch { /* ignore */ }
      } else {
        logEvent({ kind: "system", severity: "error", message: `Schwab market OAuth: ${msg || "failed"}` });
        updateActionCenter({
          title: "Schwab market OAuth",
          message: msg || "Connection failed.",
          severity: "error",
        });
        try { showToast(`Schwab market OAuth: ${msg || "failed"}`, "error", 6000); } catch { /* ignore */ }
      }
    }

    // After any Schwab OAuth callback, the server has updated /api/onboarding/status
    // (schwab_linked, wizard_required, etc.). Re-pull it so the wizard stepper, CTA,
    // and connection meta line reflect the new state instead of the cached pre-link view.
    try {
      await refreshOnboarding();
    } catch (err) {
      logEvent({
        kind: "system",
        severity: "warn",
        message: `Could not refresh onboarding after OAuth: ${err?.message || err}`,
      });
    }
  }
}

/**
 * Restore scan table + diagnostics from persisted last_scan (local) or scan-results (SaaS).
 * Without this, the UI stayed empty after refresh even when a scan had completed.
 */
async function hydrateScanTableFromStatus(status) {
  const ls = status.last_scan;
  if (!ls || !ls.at) return;

  const diag = ls.diagnostics || ls.diagnostics_summary || {};
  const metaEl = document.getElementById("scanMeta");
  const strat = ls.strategy_summary || null;

  if (state.publicConfig.saas_mode) {
    const jobId = safeText(ls.job_id || "").trim();
    const foundRaw = ls.signals_found;
    const foundN = foundRaw === null || foundRaw === undefined ? null : safeNum(foundRaw, 0);

    if (jobId && foundN === 0) {
      state.latestSignals = [];
      const headline = diagnosticsHeadline(diag);
      if (metaEl) metaEl.textContent = (headline || buildScanMeta([], 0)) + formatStrategySummary(strat);
      updateTopStrategyChip(strat);
      renderDiagnostics(diag);
      renderScanRows([]);
      return;
    }

    const url = jobId
      ? `/api/scan-results?limit=500&job_id=${encodeURIComponent(jobId)}`
      : `/api/scan-results?limit=500`;
    const listOut = await api.get(url);
    if (!listOut.ok) return;
    const rows = Array.isArray(listOut.data) ? listOut.data : [];
    const signals = rows.map((r) => r.payload).filter((p) => p && typeof p === "object");
    state.latestSignals = signals;
    const headline = diagnosticsHeadline(diag);
    if (metaEl)
      metaEl.textContent =
        (headline || buildScanMeta(signals, ls.signals_found ?? signals.length)) + formatStrategySummary(strat);
    updateTopStrategyChip(strat);
    renderDiagnostics(diag);
    renderScanRows(signals);
    return;
  }

  const localSignals = Array.isArray(ls.signals) ? ls.signals : [];
  state.latestSignals = localSignals;
  const headline = diagnosticsHeadline(diag);
  if (metaEl)
    metaEl.textContent =
      (headline || buildScanMeta(localSignals, ls.signals_found)) + formatStrategySummary(strat);
  updateTopStrategyChip(strat);
  renderDiagnostics(diag);
  renderScanRows(localSignals);
}

async function refreshStatus() {
  const saasMode = !!state.publicConfig?.saas_mode;
  // In SaaS mode the Schwab quote probe inside /api/status already populates
  // status.api_health (quote_ok + quote_health). Calling /api/health/deep on
  // top would trigger a SECOND probe per dashboard refresh, doubling Schwab
  // load and racing on the rotating refresh token. Synthesize deepRes from
  // the status payload instead. (Local mode keeps the legacy split because
  // /api/health/deep there also surfaces server-wide metrics counters.)
  const statusRes = await api.get("/api/status");
  let deepRes;
  if (saasMode) {
    if (statusRes.ok) {
      const ah = statusRes.data?.api_health || {};
      deepRes = {
        ok: true,
        data: {
          db_ok: true,
          market_token_ok: !!ah.market_token_ok,
          account_token_ok: !!ah.account_token_ok,
          quote_ok: !!ah.quote_ok,
          quote_health: ah.quote_health || {
            symbol: "AAPL",
            ok: !!ah.quote_ok,
            reason: ah.quote_ok ? null : ah.error || "not_linked_or_probe_failed",
            operator_hint: null,
          },
          metrics: ah.metrics || { requests_total: 0, errors_total: 0, client_errors_total: 0 },
        },
      };
    } else {
      deepRes = { ok: false, error: statusRes.error };
    }
  } else {
    deepRes = await api.get("/api/health/deep", { timeoutMs: 30000 });
  }
  if (!statusRes.ok) {
    logEvent({ kind: "system", severity: "error", message: `Status failed: ${statusRes.error}` });
    return;
  }

  const status = statusRes.data || {};
  try {
    await hydrateScanTableFromStatus(status);
  } catch (e) {
    console.warn("hydrateScanTableFromStatus", e);
  }
  setStatusPill(document.getElementById("marketToken"), status.market_state || (status.market_token_ok ? "Connected" : "Disconnected"));
  setStatusPill(document.getElementById("accountToken"), status.account_state || (status.account_token_ok ? "Connected" : "Disconnected"));

  const lastScanEl = document.getElementById("lastScan");
  lastScanEl.className = "pill neutral";
  if (status.last_scan && status.last_scan.at) {
    const ts = new Date(status.last_scan.at);
    const when = Number.isNaN(ts.getTime()) ? "recently" : ts.toLocaleTimeString();
    lastScanEl.textContent = `${status.last_scan.signals_found ?? 0} @ ${when}`;
  } else {
    lastScanEl.textContent = "Never";
  }

  const quoteEl = document.getElementById("quoteHealth");
  const errEl = document.getElementById("apiErrorRate");
  const validationEl = document.getElementById("validationHealth");
  const validationAgeEl = document.getElementById("validationAge");
  const validationProgressEl = document.getElementById("validationProgress");
  const validation = status.validation_status || {};
  const runStatus = safeText(validation.run_status || "idle").toLowerCase();
  if (runStatus === "running") {
    setStatusPill(validationEl, "Running");
  } else if (validation.exists && validation.passed === true) {
    setStatusPill(validationEl, "Pass");
  } else if (validation.exists && validation.passed === false) {
    setStatusPill(validationEl, "Fail");
  } else if (validation.exists) {
    setStatusPill(validationEl, "Degraded");
  } else {
    setStatusPill(validationEl, "Unknown");
  }
  if (validationAgeEl) {
    const failedSteps = (validation.failed_steps || []).slice(0, 2).join(", ");
    const failHint = failedSteps ? ` | failed: ${failedSteps}` : "";
    validationAgeEl.textContent = validation.exists
      ? `Updated ${timeAgo(validation.generated_at)}${failHint}`
      : "No validation artifact yet.";
  }
  if (validationProgressEl) {
    if (runStatus === "running") {
      const completed = safeNum(validation.completed_steps, 0);
      const total = safeNum(validation.total_steps, 0);
      const pctDone = safeNum(validation.progress_pct, 0);
      const stepName = safeText(validation.current_step || "starting");
      validationProgressEl.textContent = `Progress: ${completed}/${total} (${pctDone}%) | step: ${stepName}`;
    } else if (validation.exists) {
      const completed = safeNum(validation.completed_steps, 0);
      const total = safeNum(validation.total_steps, 0);
      if (total > 0) {
        validationProgressEl.textContent = `Progress: ${completed}/${total} (100%)`;
      } else {
        validationProgressEl.textContent = "Progress: complete";
      }
    } else {
      validationProgressEl.textContent = "Progress: --";
    }
  }
  renderValidationRecentSteps(validation);
  if (deepRes.ok) {
    setStatusPill(quoteEl, deepRes.data.quote_ok ? "Connected" : "Degraded");
    const qh = deepRes.data.quote_health;
    if (!deepRes.data.quote_ok && qh && qh.operator_hint) {
      const sig = `${qh.reason || ""}|${qh.operator_hint}`;
      if (sig !== state.lastQuoteHealthLogSig) {
        state.lastQuoteHealthLogSig = sig;
        logEvent({
          kind: "system",
          severity: "warn",
          message: `Quotes: ${qh.reason || "issue"} — ${qh.operator_hint}`,
        });
      }
    } else if (deepRes.data.quote_ok) {
      state.lastQuoteHealthLogSig = null;
    }
    const metrics = deepRes.data.metrics || {};
    const req = safeNum(metrics.requests_total, 0);
    const srvErr = safeNum(metrics.errors_total, 0);
    const clientErr = safeNum(metrics.client_errors_total, 0);
    const rate = req > 0 ? `${((srvErr / req) * 100).toFixed(1)}%` : "0.0%";
    errEl.className = statusClass(srvErr > 0 ? "warn" : clientErr > 0 ? "info" : "info");
    errEl.textContent =
      clientErr > 0 ? `${rate} srv (${srvErr}/${req}, 4xx:${clientErr})` : `${rate} srv (${srvErr}/${req})`;
  } else {
    setStatusPill(quoteEl, "Unknown");
    errEl.className = "pill neutral";
    errEl.textContent = "--";
  }

  const authOk = Boolean(status.market_token_ok && status.account_token_ok);
  const quoteOk = Boolean(deepRes.ok && deepRes.data?.quote_ok);
  const req = safeNum(deepRes?.data?.metrics?.requests_total, 0);
  const srvErrRibbon = safeNum(deepRes?.data?.metrics?.errors_total, 0);
  const errRate = req > 0 ? (srvErrRibbon / req) * 100 : 0;

  const ribbonAuth = document.getElementById("ribbonAuth");
  const ribbonQuotes = document.getElementById("ribbonQuotes");
  const ribbonApi = document.getElementById("ribbonApiErrorRate");
  const ribbonValidation = document.getElementById("ribbonValidation");
  if (ribbonAuth) {
    ribbonAuth.className = healthBadgeClass(authOk);
    ribbonAuth.textContent = authOk ? "Connected" : "Disconnected";
  }
  if (ribbonQuotes) {
    ribbonQuotes.className = healthBadgeClass(quoteOk);
    ribbonQuotes.textContent = quoteOk ? "Healthy" : "Degraded";
  }
  if (ribbonApi) {
    const apiHealthy = errRate < 2.0;
    ribbonApi.className = healthBadgeClass(apiHealthy);
    ribbonApi.textContent = `${errRate.toFixed(1)}%`;
  }
  if (ribbonValidation) {
    const validOk = validation.exists && validation.passed === true;
    ribbonValidation.className = healthBadgeClass(validOk);
    ribbonValidation.textContent = validOk ? "Pass" : safeText(validation.run_status || "Unknown");
  }
  setHealthRibbonTiles(authOk, quoteOk, errRate, validation);
  const topBlocker =
    status?.last_scan?.diagnostics_summary?.top_blockers?.[0]?.key ||
    status?.last_scan?.diagnostics_summary?.headline ||
    "";
  prioritizeActionCenterFromHealth({
    authOk,
    quoteOk,
    errRate,
    validation,
    topBlocker,
    quoteHealth: deepRes?.data?.quote_health || null,
  });
  updateHeroInfographic();
}

const SCAN_START_META = "Scanning market candidates...";

function scanBodyFromBacktestSpec(spec) {
  if (!spec || typeof spec !== "object") return {};
  const out = {};
  if (spec.overrides && typeof spec.overrides === "object" && Object.keys(spec.overrides).length) {
    out.strategy_overrides = spec.overrides;
  }
  const um = safeText(spec.universe_mode || "watchlist").toLowerCase();
  if (um === "tickers" || um === "watchlist") out.universe_mode = um;
  if (um === "tickers" && Array.isArray(spec.tickers)) out.tickers = spec.tickers;
  return out;
}

function readScanOptionsFromForm() {
  const ta = document.getElementById("scanOptionsJson");
  if (!ta) {
    state.scanRunOptions = null;
    return true;
  }
  const raw = ta.value.trim();
  if (!raw) {
    state.scanRunOptions = null;
    return true;
  }
  try {
    const parsed = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Scan options must be a JSON object.");
    }
    state.scanRunOptions = parsed;
    return true;
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    logEvent({ kind: "scan", severity: "error", message: `Invalid scan options JSON: ${msg}` });
    updateActionCenter({ title: "Scan options", message: msg, severity: "error" });
    return false;
  }
}

async function fillScanOptionsFromLatestBacktest() {
  const ta = document.getElementById("scanOptionsJson");
  if (!ta) return;
  const out = await api.get("/api/backtest-runs?limit=1");
  if (!out.ok) {
    logEvent({ kind: "scan", severity: "error", message: `Backtest list failed: ${out.error}` });
    updateActionCenter({ title: "Backtests", message: safeText(out.error), severity: "error" });
    return;
  }
  const rows = Array.isArray(out.data) ? out.data : [];
  if (!rows.length) {
    updateActionCenter({ title: "Backtests", message: "No backtest runs yet.", severity: "info" });
    return;
  }
  const spec = rows[0].spec;
  const body = scanBodyFromBacktestSpec(spec);
  ta.value = JSON.stringify(body, null, 2);
  readScanOptionsFromForm();
  logEvent({ kind: "scan", severity: "info", message: "Scan options filled from latest backtest." });
  updateActionCenter({
    title: "Scan options",
    message: "Filled from your most recent backtest. Edit JSON if needed, then Run Scan.",
    severity: "info",
  });
}

function strategySummaryFromSignals(signals) {
  const rows = Array.isArray(signals) ? signals : [];
  const counts = {};
  rows.forEach((sig) => {
    const attr = sig?.strategy_attribution;
    const name = String((attr && attr.top_live) || "unknown");
    counts[name] = (counts[name] || 0) + 1;
  });
  const ranked = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const dominant = ranked[0]?.[0] || "—";
  const dominantCount = ranked[0]?.[1] || 0;
  return {
    dominant_live_strategy: dominant,
    dominant_count: dominantCount,
    total_ranked: rows.length,
    counts: Object.fromEntries(ranked),
  };
}

async function waitForSaaScanCompletion(taskId) {
  const maxPolls = 400;
  const metaEl = document.getElementById("scanMeta");
  let firstPendingAt = null;
  let workerHintShown = false;
  setJobProgress("scanJobProgress", "scanJobProgressLabel", 0.05, "Queued…");
  for (let i = 0; i < maxPolls; i++) {
    const status = await api.get(`/api/scan-lifecycle?task_id=${encodeURIComponent(taskId)}`);
    if (!status.ok) {
      metaEl.textContent = "Scan failed.";
      updateTopStrategyChip(null);
      logEvent({ kind: "scan", severity: "error", message: `Scan task status failed: ${status.error}` });
      updateActionCenter({ title: "Scan Failed", message: status.error, severity: "error" });
      return;
    }
    const data = status.data || {};
    const celeryStatus = safeText(data.status || "").toLowerCase();
    if (celeryStatus === "pending" || celeryStatus === "received") {
      if (firstPendingAt === null) firstPendingAt = Date.now();
      metaEl.textContent = "Scan queued… waiting for worker.";
      setJobProgress("scanJobProgress", "scanJobProgressLabel", 0.12, "Queued — waiting for worker");
      const queuedMs = Date.now() - firstPendingAt;
      if (queuedMs > 50_000 && !workerHintShown) {
        workerHintShown = true;
        metaEl.textContent =
          "Still queued — no worker yet. Confirm Celery is running with queue \"scan\" and REDIS_URL matches the API.";
        updateActionCenter({
          title: "Scan waiting for worker",
          message:
            "If this stays queued, start workers with: celery -A webapp.tasks worker -Q scan,orders,celery — and use the same REDIS_URL as the app.",
          severity: "warn",
        });
      } else {
        updateActionCenter({
          title: "Scan Queued",
          message: "Task is waiting for a worker. This page will update when results are ready.",
          severity: "info",
        });
      }
      await new Promise((r) => setTimeout(r, 2000));
      continue;
    }
    firstPendingAt = null;
    if (celeryStatus === "started" || celeryStatus === "retry") {
      metaEl.textContent = "Scan running…";
      setJobProgress("scanJobProgress", "scanJobProgressLabel", 0.55, "Running scan…");
      updateActionCenter({
        title: "Scan Running",
        message: "Scan task is executing. Results will appear below when finished.",
        severity: "info",
      });
      await new Promise((r) => setTimeout(r, 4000));
      continue;
    }
    if (celeryStatus === "success") {
      const result = data.result;
      if (!result || typeof result !== "object") {
        metaEl.textContent = "Scan failed.";
        updateTopStrategyChip(null);
        const raw = typeof result === "string" ? result : "Invalid task result.";
        logEvent({ kind: "scan", severity: "error", message: raw });
        updateActionCenter({ title: "Scan Failed", message: raw, severity: "error" });
        return;
      }
      if (result.ok === false) {
        metaEl.textContent = "Scan failed.";
        updateTopStrategyChip(null);
        const errMsg = safeText(result.error || "Scan task returned error.");
        logEvent({ kind: "scan", severity: "error", message: errMsg });
        updateActionCenter({ title: "Scan Failed", message: errMsg, severity: "error" });
        return;
      }
      const jobId = result.job_id;
      let listOut;
      if (jobId) {
        listOut = await api.get(`/api/scan-results?limit=500&job_id=${encodeURIComponent(jobId)}`);
      } else {
        listOut = { ok: false, error: "Missing job_id in scan result." };
      }
      if (!listOut.ok) {
        metaEl.textContent = "Scan finished but results could not be loaded.";
        updateTopStrategyChip(null);
        logEvent({ kind: "scan", severity: "error", message: `Scan results failed: ${listOut.error}` });
        updateActionCenter({ title: "Scan Results Failed", message: listOut.error, severity: "error" });
        return;
      }
      const rows = Array.isArray(listOut.data) ? listOut.data : [];
      const signals = rows.map((r) => r.payload).filter((p) => p && typeof p === "object");
      state.latestSignals = signals;
      const diag = result.diagnostics || {};
      const headline = diagnosticsHeadline(diag);
      const n = safeNum(result.signals_found, signals.length);
      const strat =
        result.strategy_summary && typeof result.strategy_summary === "object"
          ? result.strategy_summary
          : strategySummaryFromSignals(signals);
      metaEl.textContent =
        (headline || buildScanMeta(signals, n)) + formatStrategySummary(strat);
      updateTopStrategyChip(strat);
      renderDiagnostics(diag);
      renderScanRows(signals);
      logEvent({
        kind: "scan",
        severity: "info",
        message: `Scan complete (SaaS): ${n} signal(s), task ${safeText(taskId).slice(0, 12)}…`,
      });
      updateActionCenter({
        title: "Scan Complete",
        message: `Found ${n} signal(s). Review queue candidates in Scan Results.`,
        severity: "success",
      });
      setJobProgress("scanJobProgress", "scanJobProgressLabel", 1, "Complete");
      return;
    }
    if (celeryStatus === "failure" || celeryStatus === "revoked") {
      metaEl.textContent = "Scan failed.";
      updateTopStrategyChip(null);
      const res = data.result;
      let errMsg = "Scan task failed.";
      if (typeof res === "string") errMsg = res;
      else if (res && typeof res === "object")
        errMsg = safeText(res.error || res.message || res.exc_message || JSON.stringify(res));
      logEvent({ kind: "scan", severity: "error", message: errMsg });
      updateActionCenter({ title: "Scan Failed", message: errMsg, severity: "error" });
      setJobProgress("scanJobProgress", "scanJobProgressLabel", 0, "");
      return;
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  metaEl.textContent = "Scan still running. Use Refresh to check progress.";
  updateTopStrategyChip(null);
  logEvent({ kind: "scan", severity: "warn", message: "SaaS scan polling window ended." });
  updateActionCenter({
    title: "Scan Still Running",
    message: "Polling window ended. Use Refresh All to check task status.",
    severity: "warn",
  });
}

async function runScan() {
  const btn = document.getElementById("scanBtn");
  const scanMetaEl = document.getElementById("scanMeta");
  btn.disabled = true;
  btn.textContent = "Scanning...";
  setJobProgress("scanJobProgress", "scanJobProgressLabel", 0, "");
  setLoading({ scan: SCAN_START_META });
  updateActionCenter({ title: "Scan Running", message: "Market scan is running. Results will stream into this page.", severity: "info" });
  try {
    if (!readScanOptionsFromForm()) return;
    const scanBody = state.scanRunOptions && typeof state.scanRunOptions === "object" ? state.scanRunOptions : {};
    const out = await api.post("/api/scan?async_mode=true", scanBody);
    if (!out.ok) {
      scanMetaEl.textContent = "Scan failed.";
      updateTopStrategyChip(null);
      logEvent({ kind: "scan", severity: "error", message: out.error });
      updateActionCenter({ title: "Scan Failed", message: out.error, severity: "error" });
      return;
    }
    const d = out.data || {};
    if (d.task_id) {
      const wq = d.worker_queue || {};
      const qBusy =
        wq.inspect_available && (wq.reserved_total != null || wq.active_total != null)
          ? safeNum(wq.reserved_total, 0) + safeNum(wq.active_total, 0)
          : null;
      const qPart = qBusy !== null ? ` · worker backlog ~${qBusy}` : "";
      const limPart =
        d.daily_scan_limit != null ? ` · daily scan quota ${safeNum(d.daily_scan_limit, 0)}/24h` : "";
      logEvent({
        kind: "scan",
        severity: "info",
        message: `Scan queued (task ${safeText(d.task_id).slice(0, 12)}…)${qPart}${limPart}.`,
      });
      await waitForSaaScanCompletion(d.task_id);
      await refreshStatus();
      return;
    }
    if (d.status === "running") {
      logEvent({
        kind: "scan",
        severity: "info",
        message: d.started ? "Scan started in background." : "Scan already running; monitoring progress.",
      });
      await waitForScanCompletion();
      await refreshStatus();
      return;
    }
    if (d.signals) {
      state.latestSignals = d.signals || [];
      const headline = diagnosticsHeadline(d.diagnostics_summary || d.diagnostics || {});
      scanMetaEl.textContent =
        (headline || buildScanMeta(state.latestSignals, d.signals_found)) + formatStrategySummary(d.strategy_summary);
      updateTopStrategyChip(d.strategy_summary);
      renderDiagnostics(d.diagnostics || d.diagnostics_summary || {});
      renderScanRows(state.latestSignals);
      logEvent({ kind: "scan", severity: "info", message: `Scan complete: ${d.signals_found} signal(s).` });
      updateActionCenter({
        title: "Scan Complete",
        message: `Found ${d.signals_found} signal(s). Review queue candidates in Scan Results.`,
        severity: "success",
      });
      return;
    }
    scanMetaEl.textContent = "Unexpected scan response; try Refresh or check API version.";
    updateTopStrategyChip(null);
    logEvent({ kind: "scan", severity: "warn", message: "Scan POST returned ok but unrecognized payload." });
    updateActionCenter({
      title: "Scan",
      message: "Unexpected response from server. Try Refresh All.",
      severity: "warn",
    });
  } finally {
    btn.disabled = false;
    btn.textContent = "Run Scan";
    if (scanMetaEl && scanMetaEl.textContent === SCAN_START_META) {
      scanMetaEl.textContent = "No scan run yet.";
      updateActionCenter({
        title: "Scan",
        message: "Scan did not start. Check connection and try again.",
        severity: "warn",
      });
    }
  }
}

async function waitForScanCompletion() {
  const maxPolls = 360;
  const metaEl = document.getElementById("scanMeta");
  for (let i = 0; i < maxPolls; i++) {
    const status = await api.get("/api/scan-lifecycle");
    if (!status.ok) {
      metaEl.textContent = "Scan failed.";
      updateTopStrategyChip(null);
      logEvent({ kind: "scan", severity: "error", message: `Scan status failed: ${status.error}` });
      updateActionCenter({ title: "Scan Status Failed", message: status.error, severity: "error" });
      return;
    }
    const data = status.data || {};
    if (data.status === "running") {
      updateTopStrategyChip(null);
      const elapsed = data.elapsed_seconds ?? (
        data.started_at ? Math.max(0, Math.floor((Date.now() - Date.parse(data.started_at)) / 1000)) : null
      );
      metaEl.textContent = elapsed !== null ? `Scan running... ${elapsed}s elapsed` : "Scan running...";
      updateActionCenter({
        title: "Scan Running",
        message:
          elapsed !== null
            ? `Local scan in progress (${elapsed}s elapsed). Results will appear when complete.`
            : "Local scan in progress. Results will appear when complete.",
        severity: "info",
      });
      await new Promise((r) => setTimeout(r, 5000));
      continue;
    }
    if (data.status === "completed") {
      state.latestSignals = data.signals || [];
      const headline = diagnosticsHeadline(data.diagnostics_summary || data.diagnostics || {});
      metaEl.textContent =
        (headline || buildScanMeta(state.latestSignals, data.signals_found ?? state.latestSignals.length))
        + formatStrategySummary(data.strategy_summary);
      updateTopStrategyChip(data.strategy_summary);
      renderDiagnostics(data.diagnostics_summary || data.diagnostics || {});
      renderScanRows(state.latestSignals);
      logEvent({ kind: "scan", severity: "info", message: `Scan complete: ${data.signals_found ?? state.latestSignals.length} signal(s).` });
      updateActionCenter({
        title: "Scan Complete",
        message: `Found ${data.signals_found ?? state.latestSignals.length} signal(s).`,
        severity: "success",
      });
      return;
    }
    if (data.status === "failed") {
      metaEl.textContent = "Scan failed.";
      updateTopStrategyChip(null);
      const errMsg = data.error || "unknown error";
      logEvent({ kind: "scan", severity: "error", message: errMsg });
      updateActionCenter({ title: "Scan Failed", message: errMsg, severity: "error" });
      return;
    }
    if (data.status === "idle" && data.last_scan) {
      metaEl.textContent = `Last scan: ${data.last_scan.signals_found ?? 0} signal(s).`;
      updateTopStrategyChip(data.last_scan.strategy_summary || null);
      updateActionCenter({
        title: "Scan Idle",
        message: `No active scan. Last run: ${data.last_scan.signals_found ?? 0} signal(s).`,
        severity: "info",
      });
      return;
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  metaEl.textContent = "Scan still running. Use Refresh to check progress.";
  updateTopStrategyChip(null);
  logEvent({ kind: "scan", severity: "warn", message: "Scan still running in background; polling window ended." });
  updateActionCenter({
    title: "Scan Still Running",
    message: "Polling window ended. Use Refresh All to check progress.",
    severity: "warn",
  });
}

async function refreshPending() {
  const filter = document.getElementById("pendingFilter")?.value || state.pendingFilter;
  const sort = document.getElementById("pendingSort")?.value || state.pendingSort;
  state.pendingFilter = filter;
  state.pendingSort = sort;
  const query = new URLSearchParams({ status: filter, sort });
  const pendingOnlyQuery = new URLSearchParams({ status: "pending", sort });
  const [out, pendingOnlyOut] = await Promise.all([
    api.get(`/api/pending-trades?${query.toString()}`),
    api.get(`/api/pending-trades?${pendingOnlyQuery.toString()}`),
  ]);
  if (!out.ok) {
    logEvent({ kind: "trade", severity: "error", message: `Pending trades load failed: ${out.error}` });
    return;
  }
  const rows = out.data || [];
  let pendingN =
    pendingOnlyOut.ok && Array.isArray(pendingOnlyOut.data)
      ? pendingOnlyOut.data.length
      : rows.filter((r) => r.status === "pending").length;
  document.getElementById("pendingCount").textContent = String(pendingN);
  const clearBtn = document.getElementById("clearPendingBtn");
  if (clearBtn) clearBtn.disabled = pendingN === 0;
  updateHeroInfographic();

  const board = document.getElementById("pendingBoard");
  board.innerHTML = "";
  if (!rows.length) {
    board.innerHTML = `<div class="task-empty muted">No trades match current filter.</div>`;
    return;
  }

  const groups = rows.reduce((acc, row) => {
    const key = getSectorKeyFromTrade(row);
    if (!acc[key]) acc[key] = [];
    acc[key].push(row);
    return acc;
  }, {});

  Object.keys(groups).sort().forEach((sector) => {
    const section = document.createElement("section");
    section.className = "task-group";
    section.innerHTML = `<h3>${sector}</h3>`;
    groups[sector].forEach((row) => {
      const score = meterFromScore(row?.signal?.signal_score ?? row?.signal?.score);
      const conviction = meterFromConviction(row?.signal?.mirofish_conviction);
      const liveBlocked =
        state.publicConfig.saas_mode &&
        (!state.accountMe || !state.accountMe.live_execution_enabled);
      const approveTitle = liveBlocked
        ? "Live trading is off — enable in Strategy Presets after reviewing risk."
        : "";
      const card = document.createElement("article");
      card.className = "task-card";
      card.innerHTML = `
        <div class="task-card-head">
          <div>
            <strong>${safeText(row.ticker)}</strong>
            <span class="muted">#${safeText(row.id)} • Qty ${safeText(row.qty)}</span>
          </div>
          <span class="${statusClass(row.status)}">${safeText(row.status)}</span>
        </div>
        <div class="task-meters">
          <div>
            <span class="meter-label">Score ${safeNum(row?.signal?.signal_score ?? row?.signal?.score, 0).toFixed(0)}</span>
            <div class="meter"><span style="width:${score}%"></span></div>
          </div>
          <div>
            <span class="meter-label">Conviction ${safeNum(row?.signal?.mirofish_conviction, 0).toFixed(0)}</span>
            <div class="meter conviction"><span style="width:${conviction}%"></span></div>
          </div>
        </div>
        <div class="context-mini">${renderTimeline(row)}<br/>${renderPendingContext(row)}</div>
        <div class="task-actions">
          <button class="btn small secondary" data-quick="${row.id}">Quick View</button>
          <button class="btn small approve-btn" data-approve="${row.id}" title="${escapeHtml(approveTitle)}" ${row.status !== "pending" || liveBlocked ? "disabled" : ""}>Approve</button>
          <button class="btn small reject-btn" data-reject="${row.id}" ${row.status !== "pending" ? "disabled" : ""}>Reject</button>
          <button class="btn small bad" data-delete="${row.id}" title="Permanently delete this trade">Delete</button>
        </div>
      `;
      section.appendChild(card);
    });
    board.appendChild(section);
  });

  board.querySelectorAll("button[data-quick]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const id = e.currentTarget.getAttribute("data-quick");
      const row = rows.find((r) => r.id === id);
      if (row) await openTradeDrawerForTrade(row);
    });
  });

  board.querySelectorAll("button[data-approve]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const id = e.currentTarget.getAttribute("data-approve");
      const row = rows.find((r) => r.id === id);
      openApproveDialog(row);
    });
  });

  board.querySelectorAll("button[data-reject]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const clicked = e.currentTarget;
      clicked.disabled = true;
      const id = clicked.getAttribute("data-reject");
      const out = await api.post(`/api/trades/${id}/reject`, {});
      if (!out.ok) {
        logEvent({ kind: "trade", severity: "error", message: `Reject ${id} failed: ${out.error}` });
        updateActionCenter({ title: "Trade Reject Failed", message: out.error, severity: "error" });
      } else {
        logEvent({ kind: "trade", severity: "info", message: `Rejected ${id}.` });
        updateActionCenter({ title: "Trade Rejected", message: `Trade ${id} was rejected.`, severity: "warn" });
      }
      await refreshPending();
      clicked.disabled = false;
    });
  });

  board.querySelectorAll("button[data-delete]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const clicked = e.currentTarget;
      clicked.disabled = true;
      const id = clicked.getAttribute("data-delete");
      const out = await api.post(`/api/trades/${id}/delete`, {});
      if (!out.ok) {
        logEvent({ kind: "trade", severity: "error", message: `Delete ${id} failed: ${out.error}` });
      } else {
        logEvent({ kind: "trade", severity: "info", message: `Deleted ${id}.` });
      }
      await refreshPending();
    });
  });

  const strip = document.getElementById("pendingSummaryStrip");
  const stripText = document.getElementById("pendingSummaryText");
  if (strip && stripText) {
    if (pendingN > 0) {
      strip.classList.remove("hidden");
      stripText.textContent = `${pendingN} pending trade(s) need a decision.`;
    } else {
      strip.classList.add("hidden");
    }
  }
}

async function approveTradeById(id) {
  const typed = document.getElementById("approveTickerInput")?.value?.trim() || "";
  const otpCode = document.getElementById("approveOtpInput")?.value?.trim() || "";
  if (!typed) {
    updateActionCenter({
      title: "Confirm ticker",
      message: "Type the trade ticker in the box to confirm this live order.",
      severity: "warn",
    });
    return;
  }
  const out = await api.post(`/api/trades/${id}/approve?confirm_live=true`, { typed_ticker: typed, otp_code: otpCode });
  if (!out.ok) {
    logEvent({ kind: "trade", severity: "error", message: `Approve ${id} failed: ${out.error}` });
    updateActionCenter({ title: "Approval Failed", message: out.error, severity: "error" });
  } else {
    logEvent({ kind: "trade", severity: "info", message: `Approved ${id}: order submitted.` });
    updateActionCenter({ title: "Trade Approved", message: `Trade ${id} approved and submitted.`, severity: "success" });
  }
  await refreshPending();
}

function openQueueScanDialog(sig) {
  const dialog = document.getElementById("queueScanDialog");
  const headline = document.getElementById("queueScanHeadline");
  const qty = document.getElementById("queueScanQty");
  const note = document.getElementById("queueScanNote");
  if (!dialog || !sig) return;
  state.queueScanDraft = sig;
  const t = sig.ticker || sig.symbol || "?";
  if (headline) {
    const px = sig.price ?? sig.current_price;
    headline.innerHTML = `${escapeHtml(t)} · last ${px != null ? escapeHtml(formatMoney(px)) : "—"}`;
  }
  if (qty) qty.value = "";
  if (note) note.value = "Queued from scan table";
  dialog.showModal();
}

function closeQueueScanDialog() {
  const dialog = document.getElementById("queueScanDialog");
  state.queueScanDraft = null;
  dialog?.close();
}

async function confirmQueueScanDialog() {
  const sig = state.queueScanDraft;
  if (!sig) {
    closeQueueScanDialog();
    return;
  }
  const qtyRaw = document.getElementById("queueScanQty")?.value?.trim();
  const note = document.getElementById("queueScanNote")?.value?.trim() || "Queued from scan table";
  let qty = null;
  if (qtyRaw) {
    const n = parseInt(qtyRaw, 10);
    if (!Number.isFinite(n) || n < 1) {
      logEvent({ kind: "trade", severity: "warn", message: "Enter a positive whole number for quantity, or leave blank for auto sizing." });
      return;
    }
    qty = n;
  }
  const btn = document.getElementById("queueScanConfirmBtn");
  if (btn) btn.disabled = true;
  const payload = {
    ticker: sig.ticker || sig.symbol,
    price: sig.price ?? sig.current_price ?? null,
    signal: sig,
    note,
  };
  if (qty != null) payload.qty = qty;
  const out = await api.post("/api/pending-trades", payload);
  if (!out.ok) {
    logEvent({ kind: "trade", severity: "error", message: `Queue failed: ${out.error}` });
    updateActionCenter({ title: "Queue failed", message: out.error, severity: "error" });
  } else {
    logEvent({ kind: "trade", severity: "info", message: `Queued ${payload.ticker} (${out.data.id})` });
    updateActionCenter({ title: "Staged for approval", message: `${payload.ticker} added to pending.`, severity: "success" });
    await refreshPending();
    closeQueueScanDialog();
  }
  if (btn) btn.disabled = false;
}

async function submitManualPendingTrade() {
  const tEl = document.getElementById("manualPendingTicker");
  const qEl = document.getElementById("manualPendingQty");
  const nEl = document.getElementById("manualPendingNote");
  const ticker = (tEl?.value || "").trim().toUpperCase();
  if (!ticker) {
    logEvent({ kind: "trade", severity: "warn", message: "Enter a ticker to stage a trade." });
    return;
  }
  let qty = null;
  const qRaw = (qEl?.value || "").trim();
  if (qRaw) {
    const n = parseInt(qRaw, 10);
    if (!Number.isFinite(n) || n < 1) {
      logEvent({ kind: "trade", severity: "warn", message: "Quantity must be a positive whole number, or leave blank for auto sizing." });
      return;
    }
    qty = n;
  }
  const note = (nEl?.value || "").trim() || "Manual staging from dashboard";
  const btn = document.getElementById("manualPendingBtn");
  if (btn) btn.disabled = true;
  const payload = { ticker, note };
  if (qty != null) payload.qty = qty;
  const out = await api.post("/api/pending-trades", payload);
  if (!out.ok) {
    logEvent({ kind: "trade", severity: "error", message: `Manual queue failed: ${out.error}` });
    updateActionCenter({ title: "Could not stage trade", message: out.error, severity: "error" });
  } else {
    logEvent({ kind: "trade", severity: "info", message: `Queued ${ticker} (${out.data.id})` });
    updateActionCenter({ title: "Staged for approval", message: `${ticker} added to pending.`, severity: "success" });
    if (tEl) tEl.value = "";
    if (qEl) qEl.value = "";
    if (nEl) nEl.value = "";
    await refreshPending();
  }
  if (btn) btn.disabled = false;
}

async function refreshAll() {
  resetLazyLoaded();
  setLoading({ portfolio: "Loading portfolio..." });
  await Promise.all([
    refreshStatus(),
    refreshAccountMe(),
    refreshPending(),
    refreshPortfolio(),
    refreshSectors(),
    refreshOnboarding(),
    loadProfiles(),
    refreshPerformance(),
    refreshCalibration(),
    refreshBacktestRuns(),
  ]);
  Object.keys(lazyLoaded).forEach((k) => {
    lazyLoaded[k] = true;
  });
}

/**
 * Safe DOM binder. Logs (but never throws) when an element is missing so a
 * single stale id can't take down the whole bootstrap. Returns the element
 * (or null) for callers that want to do more with it.
 */
function bindEvent(elementId, eventName, handler, options) {
  const el = document.getElementById(elementId);
  if (!el) {
    logEvent({
      kind: "system",
      severity: "warn",
      message: `wireEvents: missing #${elementId} (skipped ${eventName} binding)`,
    });
    return null;
  }
  el.addEventListener(eventName, handler, options);
  return el;
}

function wireEvents() {
  restoreBacktestFormFromStorage();
  setDefaultBacktestDates();
  syncBtUniverseRow();
  wireBacktestFormPersistence();
  renderStrategyChatMessages();
  document.getElementById("btHubTabForm")?.addEventListener("click", () => switchBacktestHubTab("form"));
  document.getElementById("btHubTabChat")?.addEventListener("click", () => switchBacktestHubTab("chat"));
  document.getElementById("btUniverse")?.addEventListener("change", syncBtUniverseRow);
  document.querySelectorAll(".bt-preset").forEach((btn) => {
    btn.addEventListener("click", () => {
      const y = btn.getAttribute("data-years");
      if (y) applyBacktestPresetYears(y);
    });
  });
  document.querySelectorAll(".sc-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const t = btn.getAttribute("data-text") || "";
      const input = document.getElementById("scInput");
      if (input) input.value = t;
      input?.focus();
    });
  });
  document.getElementById("scInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendStrategyChat();
    }
  });
  document.getElementById("btQueueBtn")?.addEventListener("click", queueUserBacktest);
  document.getElementById("btRefreshListBtn")?.addEventListener("click", refreshBacktestRuns);
  document.getElementById("btResetFormBtn")?.addEventListener("click", resetBacktestFormToDefaults);
  document.getElementById("queueScanCancelBtn")?.addEventListener("click", closeQueueScanDialog);
  document.getElementById("queueScanConfirmBtn")?.addEventListener("click", () => void confirmQueueScanDialog());
  document.getElementById("manualPendingBtn")?.addEventListener("click", () => void submitManualPendingTrade());
  document.getElementById("queueScanDialog")?.addEventListener("click", (e) => {
    if (e.target?.id === "queueScanDialog") closeQueueScanDialog();
  });
  document.getElementById("scSendBtn")?.addEventListener("click", sendStrategyChat);
  bindEvent("scanBtn", "click", runScan);
  document.getElementById("scanApplyBacktestSpecBtn")?.addEventListener("click", () => void fillScanOptionsFromLatestBacktest());
  document.getElementById("scanClearOptionsBtn")?.addEventListener("click", () => {
    const ta = document.getElementById("scanOptionsJson");
    if (ta) ta.value = "";
    state.scanRunOptions = null;
  });
  bindEvent("refreshBtn", "click", refreshAll);
  document.getElementById("onboardingStartBtn")?.addEventListener("click", startOnboarding);
  document.getElementById("onboardingConnectBtn")?.addEventListener("click", () => runOnboardingStep("connect"));
  document.getElementById("onboardingVerifyBtn")?.addEventListener("click", () => runOnboardingStep("verify_token_health"));
  document.getElementById("onboardingScanBtn")?.addEventListener("click", () => runOnboardingStep("test_scan"));
  document.getElementById("onboardingPaperBtn")?.addEventListener("click", () => runOnboardingStep("test_paper_order"));
  document.getElementById("onboardingSchwabBtn")?.addEventListener("click", () => triggerSchwabAccountOAuth());
  document.getElementById("onboardingSchwabMarketBtn")?.addEventListener("click", () => triggerSchwabMarketOAuth());
  document.getElementById("onboardingSchwabLink")?.addEventListener("click", (e) => {
    e.preventDefault();
    void triggerSchwabAccountOAuth();
  });
  document.getElementById("onboardingSchwabMarketLink")?.addEventListener("click", (e) => {
    e.preventDefault();
    void triggerSchwabMarketOAuth();
  });
  bindEvent("applyProfileBtn", "click", applyProfile);
  document.getElementById("enableLiveTradingBtn")?.addEventListener("click", () => void submitEnableLiveTrading());
  document.getElementById("saveTradingHaltBtn")?.addEventListener("click", () => void submitTradingHaltSave());
  document.getElementById("calibrationRefreshBtn")?.addEventListener("click", () => void refreshCalibration());
  document.getElementById("portfolioRiskPanel")?.addEventListener("toggle", (e) => {
    if (e.target.open) void loadPortfolioRisk();
  });
  bindEvent("settingsModeSelect", "change", loadProfiles);
  document.getElementById("profileSelect")?.addEventListener("change", renderPresetApplyPreview);
  document.getElementById("automationOptIn")?.addEventListener("change", renderPresetApplyPreview);
  bindEvent("decisionBtn", "click", loadDecisionCard);
  bindEvent("recoveryBtn", "click", mapRecovery);
  bindEvent("performanceRefreshBtn", "click", refreshPerformance);
  document.getElementById("evolveBtn")?.addEventListener("click", async () => {
    const btn = document.getElementById("evolveBtn");
    const panel = document.getElementById("learningPanel");
    if (btn) { btn.disabled = true; btn.textContent = "Analyzing..."; }
    try {
      const out = await api.post("/api/evolve/run");
      if (out.ok) {
        renderEvolvePanel(panel, out.data);
      } else {
        if (panel) panel.innerHTML = `<div class="panel-error">${safeText(out.error || "Analysis failed")}</div>`;
      }
    } catch (e) {
      if (panel) panel.innerHTML = `<div class="panel-error">Error: ${safeText(String(e))}</div>`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Run Post-Mortem Analysis"; }
    }
  });
  document.getElementById("challengerBtn")?.addEventListener("click", async () => {
    const btn = document.getElementById("challengerBtn");
    const panel = document.getElementById("challengerPanel");
    if (btn) { btn.disabled = true; btn.textContent = "Running scans..."; }
    try {
      const out = await api.post("/api/challenger/run");
      if (out.ok && out.data && out.data.comparison) {
        renderChallengerPanel(panel, { available: true, latest: out.data.comparison, win_rate: out.data.win_rate || {} });
      } else {
        if (panel) panel.innerHTML = `<div class="panel-error">${safeText((out.data && out.data.message) || out.error || "Challenger scan failed")}</div>`;
      }
    } catch (e) {
      if (panel) panel.innerHTML = `<div class="panel-error">Error: ${safeText(String(e))}</div>`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Run Challenger Scan"; }
    }
  });
  // Close button + Esc + backdrop are wired inside panels/tradeDrawer.js.
  bindEvent("activityDrawerToggle", "click", () => {
    const body = document.getElementById("activityDrawerBody");
    const toggle = document.getElementById("activityDrawerToggle");
    if (!body || !toggle) return;
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", expanded ? "false" : "true");
    body.classList.toggle("open", !expanded);
  });
  bindEvent("checkBtn", "click", quickCheck);
  bindEvent("reportBtn", "click", runReport);
  bindEvent("secCompareBtn", "click", runSecCompare);
  bindEvent("secCompareMode", "change", applySecCompareMode);
  document.querySelectorAll("#secComparePresetButtons button[data-a]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const node = e.currentTarget;
      const mode = node.getAttribute("data-mode") || "ticker_vs_ticker";
      const a = node.getAttribute("data-a") || "";
      const b = node.getAttribute("data-b") || "";
      document.getElementById("secCompareMode").value = mode;
      document.getElementById("secCompareTickerA").value = a;
      document.getElementById("secCompareTickerB").value = b;
      applySecCompareMode();
      updateActionCenter({
        title: "Preset Loaded",
        message: `${a}${b ? ` vs ${b}` : " over time"} template loaded. Click Run SEC Compare.`,
        severity: "info",
      });
    });
  });
  bindEvent("toggleReportViewBtn", "click", () => {
    state.reportRawView = !state.reportRawView;
    applyReportViewMode();
  });
  bindEvent("pendingFilter", "change", refreshPending);
  bindEvent("pendingSort", "change", refreshPending);
  document.getElementById("clearPendingBtn")?.addEventListener("click", async () => {
    const btn = document.getElementById("clearPendingBtn");
    if (!btn || btn.disabled) return;
    if (
      !confirm(
        "Reject all pending trades? They will move to rejected status and disappear from the pending queue.",
      )
    ) {
      return;
    }
    btn.disabled = true;
    const out = await api.post("/api/pending-trades/clear-pending", {});
    if (!out.ok) {
      logEvent({ kind: "trade", severity: "error", message: `Clear pending failed: ${out.error}` });
      updateActionCenter({ title: "Clear pending failed", message: out.error, severity: "error" });
      await refreshPending();
      return;
    }
    const n = typeof out.data?.cleared === "number" ? out.data.cleared : 0;
    logEvent({ kind: "trade", severity: "info", message: `Cleared ${n} pending trade(s).` });
    updateActionCenter({
      title: n ? "Pending queue cleared" : "Nothing to clear",
      message: n ? `Rejected ${n} pending trade(s).` : "There were no pending trades.",
      severity: n ? "warn" : "info",
    });
    await refreshPending();
  });

  document.getElementById("deleteAllTradesBtn")?.addEventListener("click", async () => {
    if (!confirm("Permanently delete ALL trades from history? This cannot be undone.")) return;
    const btn = document.getElementById("deleteAllTradesBtn");
    if (btn) btn.disabled = true;
    const out = await api.post("/api/pending-trades/delete-all", {});
    if (!out.ok) {
      logEvent({ kind: "trade", severity: "error", message: `Delete all failed: ${out.error}` });
      updateActionCenter({ title: "Delete failed", message: out.error, severity: "error" });
    } else {
      const n = typeof out.data?.deleted === "number" ? out.data.deleted : 0;
      logEvent({ kind: "trade", severity: "info", message: `Deleted ${n} trade(s) from history.` });
      updateActionCenter({ title: "History cleared", message: `Permanently deleted ${n} trade(s).`, severity: "success" });
    }
    if (btn) btn.disabled = false;
    await refreshPending();
  });

  const dialog = document.getElementById("approveDialog");
  bindEvent("confirmApproveBtn", "click", async (e) => {
    e.preventDefault();
    const id = state.approvingTradeId;
    if (!id) {
      dialog?.close();
      return;
    }
    const confirmBtn = document.getElementById("confirmApproveBtn");
    if (confirmBtn) confirmBtn.disabled = true;
    await approveTradeById(id);
    if (confirmBtn) confirmBtn.disabled = false;
    state.approvingTradeId = null;
    dialog?.close();
  });
  bindEvent("cancelApproveBtn", "click", () => {
    state.approvingTradeId = null;
    dialog?.close();
  });

  const navLinks = [...document.querySelectorAll(".section-nav a")];
  const sections = navLinks
    .map((a) => document.querySelector(a.getAttribute("href")))
    .filter(Boolean);
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const id = entry.target.getAttribute("id");
      navLinks.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === `#${id}`));
    });
  }, { rootMargin: "-35% 0px -55% 0px", threshold: 0.01 });
  sections.forEach((section) => observer.observe(section));

  document.getElementById("displayModeSelect")?.addEventListener("change", (e) => {
    const v = e.target.value;
    applyDisplayMode(v);
    if (v === "pro" && state.performance) {
      const panel = document.getElementById("performancePanel");
      if (panel) renderPerformancePanel(panel, state.performance);
    }
  });
}

/* ── Scroll-to-top button ─────────────────────── */
/* ── Server-Sent Events ───────────────────────── */
let _sseSource = null;
function connectSSE() {
  if (!state.sseEnabled) return;
  if (_sseSource) return;
  _sseSource = new EventSource("/api/events");
  _sseSource.addEventListener("connected", () => {
    logEvent({ kind: "system", severity: "info", message: "Live connection established." });
  });
  _sseSource.addEventListener("message", (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      const event = msg.event;
      if (event === "scan_started") {
        const btn = document.getElementById("scanBtn");
        if (btn) { btn.disabled = true; btn.textContent = "Scanning..."; }
        updateActionCenter({ title: "Scan Running", message: "Market scan started. Results will appear automatically.", severity: "info" });
      } else if (event === "scan_completed") {
        const btn = document.getElementById("scanBtn");
        if (btn) { btn.disabled = false; btn.textContent = "Run Scan"; }
        const count = msg.signals_found ?? 0;
        showToast(`Scan complete: ${count} signal(s) found`, "success", 4000);
        addNotification(`Scan complete: ${count} signal(s) found`, "success");
        updateActionCenter({ title: "Scan Complete", message: `Found ${count} signal(s). Refreshing data...`, severity: "success" });
        refreshStatus();
        refreshPending();
      } else if (event === "scan_failed") {
        const btn = document.getElementById("scanBtn");
        if (btn) { btn.disabled = false; btn.textContent = "Run Scan"; }
        showToast("Scan failed: " + (msg.error || "unknown error"), "error", 6000);
        addNotification(`Scan failed: ${msg.error || "unknown"}`, "error");
        updateActionCenter({ title: "Scan Failed", message: msg.error || "Unknown error", severity: "error" });
      } else if (event === "trade_created") {
        showToast(`Trade queued: ${msg.ticker || "?"} (${msg.qty || "?"} shares)`, "info", 3000);
        addNotification(`Trade queued: ${msg.ticker || "?"} (${msg.qty || "?"} shares)`, "info");
        refreshPending();
      } else if (event === "trade_approved") {
        showToast(`Trade executed: ${msg.ticker || "?"}`, "success", 4000);
        addNotification(`Trade executed: ${msg.ticker || "?"}`, "success");
        refreshPending();
      } else if (event === "trade_rejected") {
        showToast(`Trade rejected: ${msg.ticker || "?"}`, "warn", 3000);
        addNotification(`Trade rejected: ${msg.ticker || "?"}`, "info");
        refreshPending();
      } else if (event === "trade_failed") {
        showToast(`Trade failed: ${msg.ticker || "?"} — ${msg.error || ""}`, "error", 5000);
        addNotification(`Trade failed: ${msg.ticker || "?"} — ${msg.error || ""}`, "error");
        refreshPending();
      }
    } catch { /* ignore malformed events */ }
  });
  _sseSource.onerror = () => {
    _sseSource.close();
    _sseSource = null;
    if (state.sseEnabled) setTimeout(connectSSE, 5000);
  };
}

(async () => {
  // Wrap each step so a stale/missing element in one area can't kill all the
  // downstream init (and leave the page looking dead with no buttons working).
  function safeInit(label, fn) {
    try {
      const result = fn();
      return result instanceof Promise
        ? result.catch((err) => {
            console.error(`[init] ${label} failed`, err);
            logEvent({ kind: "system", severity: "error", message: `${label} failed: ${String(err?.message || err)}` });
            try { showToast(`Init step failed: ${label}. Some buttons may not work.`, "error", 6000); } catch { /* ignore */ }
          })
        : result;
    } catch (err) {
      console.error(`[init] ${label} failed`, err);
      logEvent({ kind: "system", severity: "error", message: `${label} failed: ${String(err?.message || err)}` });
      try { showToast(`Init step failed: ${label}. Some buttons may not work.`, "error", 6000); } catch { /* ignore */ }
      return undefined;
    }
  }

  safeInit("wireEvents", wireEvents);
  safeInit("setupScrollToTop", setupScrollToTop);
  safeInit("setupCommandPalette", () =>
    setupCommandPalette({ runLazyApi, applyDisplayMode, openTradeDrawer }),
  );
  safeInit("setupKeyboardShortcuts", () =>
    setupKeyboardShortcuts({
      openCommandPalette,
      closeCommandPalette,
      showToast,
      applyDisplayMode,
    }),
  );
  safeInit("setupNotifications", setupNotifications);
  safeInit("applyDisplayMode", () => applyDisplayMode(getDisplayMode()));
  safeInit("applyReportViewMode", applyReportViewMode);
  safeInit("applySecCompareMode", applySecCompareMode);
  await safeInit("loadConfig", loadConfig);
  if (state.sseEnabled) safeInit("connectSSE", connectSSE);
  await authSessionReady;
  const token = await getApiAccessToken();
  if (token) {
    await safeInit("refreshCritical", refreshCritical);
    safeInit("markDeferredDataPlaceholders", markDeferredDataPlaceholders);
    safeInit("setupLazySectionLoading", setupLazySectionLoading);
  } else if (state.config?.auth_mode === "supabase") {
    updateActionCenter({
      title: "Sign in",
      message: "Sign in with Supabase to load portfolio, pending trades, and billing-protected actions.",
      severity: "warn",
    });
    safeInit("setupLazySectionLoading", setupLazySectionLoading);
  } else {
    await safeInit("refreshAll", refreshAll);
    safeInit("setupLazySectionLoading", setupLazySectionLoading);
  }
  safeInit("installRouter", installRouter);
  safeInit("updateActivityBadge", updateActivityBadge);
  logEvent({ kind: "system", severity: "info", message: "Dashboard loaded." });
})();

