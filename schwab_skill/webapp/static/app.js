const state = {
  latestSignals: [],
  /** Last watchlist size from scan diagnostics (for hero KPI). */
  lastWatchlistSize: null,
  approvingTradeId: null,
  approvingChecklist: null,
  pendingFilter: "all",
  pendingSort: "newest",
  config: { auth_mode: "jwt" },
  publicConfig: {
    supabase: null,
    saas_mode: false,
    schwab_oauth: false,
    schwab_market_oauth: false,
    platform_live_trading_kill_switch: false,
  },
  accountMe: null,
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
};

const UI_VIEW_MODE_KEY = "tradingbot.ui.view_mode";

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

function openAncestorDetails(el) {
  let p = el?.parentElement;
  while (p) {
    if (p.tagName === "DETAILS") p.open = true;
    p = p.parentElement;
  }
}

function handleRouteHash() {
  const id = window.location.hash.slice(1);
  if (!id) return;
  const el = document.getElementById(id);
  if (!el) return;
  openAncestorDetails(el);
  requestAnimationFrame(() => el.scrollIntoView({ behavior: "smooth", block: "start" }));
}

/** Map short query values to element ids, e.g. ?section=backtest → #backtestSection */
function applyQuerySectionDeepLink() {
  try {
    const u = new URL(window.location.href);
    let sec = (u.searchParams.get("section") || "").trim();
    if (!sec) return;
    const aliases = {
      backtest: "backtestSection",
      backtests: "backtestSection",
      pending: "pendingSection",
      trades: "pendingSection",
      scan: "workflowPrimary",
      workflow: "workflowPrimary",
    };
    const id = aliases[sec.toLowerCase()] || sec;
    if (!document.getElementById(id)) return;
    u.searchParams.delete("section");
    const q = u.searchParams.toString();
    window.history.replaceState({}, "", `${u.pathname}${q ? `?${q}` : ""}#${id}`);
  } catch (_) {
    /* ignore */
  }
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

async function submitEnableLiveTrading() {
  const ack = Boolean(document.getElementById("enableLiveRiskAck")?.checked);
  const phrase = document.getElementById("enableLiveTypedPhrase")?.value?.trim() || "";
  const out = await api.post("/api/settings/enable-live-trading", {
    risk_acknowledged: ack,
    typed_phrase: phrase,
  });
  if (!out.ok) {
    const msg = typeof out.error === "string" ? out.error : JSON.stringify(out.error || "Request failed");
    logEvent({ kind: "system", severity: "error", message: `Enable live trading failed: ${msg}` });
    updateActionCenter({ title: "Enable live trading", message: msg, severity: "error" });
    return;
  }
  logEvent({ kind: "system", severity: "info", message: "Live trading enabled for this account." });
  updateActionCenter({
    title: "Live trading enabled",
    message: "You can approve pending trades; type the ticker in the dialog to confirm each order.",
    severity: "success",
  });
  const phraseInput = document.getElementById("enableLiveTypedPhrase");
  if (phraseInput) phraseInput.value = "";
  await refreshAccountMe();
  await refreshPending();
}

async function refreshCritical() {
  await Promise.all([refreshStatus(), refreshAccountMe(), refreshPending()]);
}

const AUTH_TOKEN_KEY = "tradingbot.jwt";
const LEGACY_AUTH_TOKEN_KEYS = ["supabasetoken", "supabaseToken", "supabase_token"];
const BACKTEST_PREFS_KEY = "tradingbot.backtest.preferences";

let _resolveAuthReady;
const authSessionReady = new Promise((r) => {
  _resolveAuthReady = r;
});

function markAuthReady() {
  if (_resolveAuthReady) {
    _resolveAuthReady();
    _resolveAuthReady = null;
  }
}

async function getApiAccessToken() {
  const cookieSession = await ensureCookieAuthSession();
  if (cookieSession) return "";
  const manual = document.getElementById("jwtInput")?.value?.trim() || "";
  if (manual) return manual;
  if (state.config?.auth_mode === "supabase" && supabaseClient) {
    const { data, error } = await supabaseClient.auth.getSession();
    if (error) console.warn("auth.getSession", error);
    const sessionToken = (data?.session?.access_token || "").trim();
    if (sessionToken) return sessionToken;
  }
  return readStoredApiJwt();
}

function clearLegacyApiJwtKeys() {
  LEGACY_AUTH_TOKEN_KEYS.forEach((key) => localStorage.removeItem(key));
}

function readStoredApiJwt() {
  const current = (localStorage.getItem(AUTH_TOKEN_KEY) || "").trim();
  if (current) return current;
  for (const key of LEGACY_AUTH_TOKEN_KEYS) {
    const legacy = (localStorage.getItem(key) || "").trim();
    if (!legacy) continue;
    localStorage.setItem(AUTH_TOKEN_KEY, legacy);
    clearLegacyApiJwtKeys();
    return legacy;
  }
  return "";
}

function clearStoredApiJwt() {
  localStorage.removeItem(AUTH_TOKEN_KEY);
  clearLegacyApiJwtKeys();
}

async function ensureCookieAuthSession() {
  try {
    const out = await fetch("/api/auth/session", {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!out.ok) return false;
    const body = await out.json();
    const data = body?.data && typeof body.data === "object" ? body.data : {};
    return Boolean(data.authenticated);
  } catch {
    return false;
  }
}

async function createCookieAuthSession(accessToken) {
  const token = safeText(accessToken).trim();
  if (!token) return false;
  try {
    const out = await fetch("/api/auth/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ access_token: token }),
    });
    return out.ok;
  } catch {
    return false;
  }
}

async function clearCookieAuthSession() {
  try {
    await fetch("/api/auth/session", {
      method: "DELETE",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
  } catch {
    /* ignore */
  }
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

/** Set when /api/public-config exposes Supabase URL + anon key */
let supabaseClient = null;
const SUPABASE_ESM = "https://esm.sh/@supabase/supabase-js@2.49.1";

function persistApiJwtFromSession(session) {
  if (session?.access_token) {
    localStorage.setItem(AUTH_TOKEN_KEY, session.access_token);
    clearLegacyApiJwtKeys();
    void createCookieAuthSession(session.access_token);
    const inp = document.getElementById("jwtInput");
    if (inp) inp.value = "";
  }
}

function updateSupabaseAuthUI(session) {
  const out = document.getElementById("supabaseSignedOut");
  const inn = document.getElementById("supabaseSignedIn");
  const label = document.getElementById("supabaseUserLabel");
  if (!out || !inn) return;
  if (session?.user) {
    out.classList.add("hidden");
    inn.classList.remove("hidden");
    if (label) label.textContent = session.user.email || session.user.id || "Signed in";
  } else {
    inn.classList.add("hidden");
    out.classList.remove("hidden");
    if (label) label.textContent = "";
  }
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

  supabaseClient = createClient(url, anonKey, {
    auth: {
      autoRefreshToken: true,
      persistSession: true,
      detectSessionInUrl: true,
    },
  });

  const {
    data: { session },
  } = await supabaseClient.auth.getSession();
  persistApiJwtFromSession(session);
  updateSupabaseAuthUI(session);

  supabaseClient.auth.onAuthStateChange((_event, nextSession) => {
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
    const { error } = await supabaseClient.auth.signInWithPassword({ email, password });
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
    const { error } = await supabaseClient.auth.signUp({ email, password });
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
    await supabaseClient.auth.signOut();
    clearStoredApiJwt();
    await clearCookieAuthSession();
    const inp = document.getElementById("jwtInput");
    if (inp) inp.value = "";
    logEvent({ kind: "system", severity: "info", message: "Signed out." });
  });

  markAuthReady();
}

function updateActionCenter({ title = "System Messages", message = "", severity = "info" }) {
  const wrap = document.getElementById("actionCenter");
  const titleEl = document.getElementById("actionCenterTitle");
  const textEl = document.getElementById("actionCenterText");
  if (!wrap || !titleEl || !textEl) return;
  wrap.classList.remove("info", "success", "warn", "error");
  wrap.classList.add(["info", "success", "warn", "error"].includes(severity) ? severity : "info");
  titleEl.textContent = title;
  textEl.textContent = message || "Ready.";
}

const DIAG_LABELS = {
  watchlist_size: "Watchlist",
  stage2_fail: "Stage 2 failed",
  vcp_fail: "VCP failed",
  breakout_not_confirmed: "Breakout not confirmed",
  sector_not_winning: "Sector underperforming",
  too_few_candles: "Insufficient data",
  df_empty: "No price data",
  exceptions: "Processing errors",
  weak_mirofish_alignment: "Weak sentiment alignment",
  low_breakout_volume: "Low breakout volume",
  self_study_filtered: "Filtered by self-study",
  quality_gates_filtered: "Quality gate filtered",
  advisory_scored: "Advisory scored",
  advisory_high_confidence: "Advisory high-confidence",
  advisory_medium_confidence: "Advisory medium-confidence",
  advisory_low_confidence: "Advisory low-confidence",
};

const api = {
  async request(path, options = {}) {
    const timeoutMs = Number(options.timeoutMs || 90000);
    const fetchOptions = { ...options };
    delete fetchOptions.timeoutMs;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    const headers = {
      "Content-Type": "application/json",
      ...(fetchOptions.headers || {}),
    };

    const token = await getApiAccessToken();
    if (token) headers.Authorization = `Bearer ${token}`;

    try {
      const res = await fetch(path, {
        ...fetchOptions,
        headers,
        signal: controller.signal,
      });
      const text = await res.text();
      let data;
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        data = { ok: false, error: `Invalid JSON response (${res.status})` };
      }
      if (!res.ok) {
        return {
          ok: false,
          error: data?.error || data?.detail || `HTTP ${res.status}`,
          status: res.status,
          data: data?.data ?? null,
        };
      }
      return data;
    } catch (err) {
      if (err?.name === "AbortError") return { ok: false, error: "Request timed out. Please retry." };
      return { ok: false, error: err?.message || "Request failed." };
    } finally {
      clearTimeout(timeout);
    }
  },

  get(path, options = {}) {
    return this.request(path, { method: "GET", ...options });
  },

  post(path, body = {}, options = {}) {
    return this.request(path, { method: "POST", body: JSON.stringify(body), ...options });
  },

  patch(path, body = {}, options = {}) {
    return this.request(path, { method: "PATCH", body: JSON.stringify(body), ...options });
  },
};

function safeText(value) {
  if (value === null || value === undefined) return "—";
  return String(value);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function safeNum(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function prettyJson(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatMoney(value) {
  return `$${safeNum(value, 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function pct(value, digits = 1) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

/** Backtest metrics from API are already in percent points (e.g. 55.2 => 55.2%). */
function formatPercentPoints(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

const PRESET_SETTING_LABELS = {
  POSITION_SIZE_USD: "Position size (USD)",
  MAX_TRADES_PER_DAY: "Max trades per day",
  QUALITY_GATES_MODE: "Quality gates",
  EVENT_RISK_MODE: "Event risk mode",
  EVENT_ACTION: "Event action",
  EXEC_QUALITY_MODE: "Execution quality mode",
};

function presetSettingLabel(key) {
  return PRESET_SETTING_LABELS[key] || key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function renderPerformancePanel(rootEl, data, { error } = {}) {
  const rawDetails = document.getElementById("performanceRawDetails");
  const rawPre = document.getElementById("performanceRaw");
  if (!rootEl) return;
  if (rawPre && !error && data) rawPre.textContent = prettyJson(data);
  if (rawDetails) {
    if (error || !data) rawDetails.classList.add("hidden");
    else rawDetails.classList.remove("hidden");
  }
  if (error) {
    rootEl.innerHTML = `<div class="panel-error">${safeText(error)}</div>`;
    return;
  }
  if (!data || typeof data !== "object") {
    rootEl.innerHTML = `<div class="report-empty">No performance snapshot loaded yet.</div>`;
    return;
  }

  const bt = data.backtest && typeof data.backtest === "object" ? data.backtest : {};
  const sp = data.shadow_paper && typeof data.shadow_paper === "object" ? data.shadow_paper : {};
  const lv = data.live && typeof data.live === "object" ? data.live : {};
  const val = data.validation && typeof data.validation === "object" ? data.validation : {};
  const sg = data.separation_guard && typeof data.separation_guard === "object" ? data.separation_guard : {};

  const vstat = val.status && typeof val.status === "object" ? val.status : {};
  const runStatus = safeText(vstat.run_status);
  const passed = vstat.passed;
  let valBadgeClass = "bg-slate-900";
  let valBadgeText = runStatus || "unknown";
  if (passed === true) {
    valBadgeClass = "bg-green-900";
    valBadgeText = runStatus ? `${runStatus} · pass` : "pass";
  } else if (passed === false) {
    valBadgeClass = "bg-red-900";
    valBadgeText = runStatus ? `${runStatus} · fail` : "fail";
  } else if (runStatus === "idle" || vstat.exists === false) {
    valBadgeClass = "bg-slate-900";
    valBadgeText = runStatus || "idle";
  }
  const valMetaParts = [];
  if (vstat.source) valMetaParts.push(`source: ${safeText(vstat.source)}`);
  if (vstat.progress_pct != null && vstat.progress_pct !== "") valMetaParts.push(`progress: ${safeText(vstat.progress_pct)}%`);
  if (vstat.generated_at) valMetaParts.push(`updated: ${safeText(vstat.generated_at)}`);
  const valMeta = valMetaParts.length ? `<span class="muted">${valMetaParts.join(" · ")}</span>` : "";
  const artifacts = val.artifacts_present === true ? "present" : val.artifacts_present === false ? "missing" : "—";

  const outcomes = Array.isArray(lv.latest_outcomes) ? lv.latest_outcomes : [];
  let outcomesTable = "";
  if (outcomes.length) {
    const rows = outcomes
      .map((row) => {
        const o = row && typeof row === "object" ? row : {};
        return `<tr>
          <td>${safeText(o.ticker)}</td>
          <td>${safeText(o.side)}</td>
          <td>${safeText(o.qty)}</td>
          <td>${o.fill_price != null && o.fill_price !== "" ? safeText(o.fill_price) : "—"}</td>
          <td>${safeText(o.date)}</td>
          <td>${o.mirofish_conviction != null ? safeText(o.mirofish_conviction) : "—"}</td>
          <td>${safeText(o.sector_etf)}</td>
        </tr>`;
      })
      .join("");
    outcomesTable = `
      <div class="performance-outcomes-wrap">
        <h3>Latest recorded outcomes</h3>
        <div class="table-wrap">
          <table>
            <caption class="visually-hidden">Latest live trade outcomes</caption>
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Side</th>
                <th>Qty</th>
                <th>Fill</th>
                <th>Date</th>
                <th>Conviction</th>
                <th>Sector ETF</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
  }

  const calloutMsg = sg.message != null && String(sg.message).trim() ? safeText(sg.message) : "";
  const callout = calloutMsg
    ? `<p class="performance-callout" role="note">${calloutMsg}</p>`
    : "";

  rootEl.innerHTML = `
    <div class="performance-buckets">
      <div class="perf-bucket">
        <h3>Backtest</h3>
        <div class="perf-source">${safeText(bt.source)}</div>
        <div class="perf-metric"><span class="label">Run at</span><span class="value">${safeText(bt.run_at)}</span></div>
        <div class="perf-metric"><span class="label">Trades</span><span class="value">${safeText(bt.total_trades)}</span></div>
        <div class="perf-metric"><span class="label">Win rate</span><span class="value">${formatPercentPoints(bt.win_rate)}</span></div>
        <div class="perf-metric"><span class="label">Avg return</span><span class="value">${formatPercentPoints(bt.avg_return_pct)}</span></div>
        <div class="perf-metric"><span class="label">Max drawdown</span><span class="value">${formatPercentPoints(bt.max_drawdown_pct)}</span></div>
      </div>
      <div class="perf-bucket">
        <h3>Shadow / paper</h3>
        <div class="perf-source">${safeText(sp.source)}</div>
        <div class="perf-metric"><span class="label">Shadow actions</span><span class="value">${safeText(sp.shadow_actions)}</span></div>
        <p class="perf-bucket-note">${safeText(sp.notes)}</p>
      </div>
      <div class="perf-bucket">
        <h3>Live</h3>
        <div class="perf-source">${safeText(lv.source)}</div>
        <div class="perf-metric"><span class="label">Live actions</span><span class="value">${safeText(lv.live_actions)}</span></div>
        <div class="perf-metric"><span class="label">Recorded outcomes</span><span class="value">${safeText(lv.recorded_outcomes)}</span></div>
      </div>
    </div>
    <div class="performance-validation">
      <span class="health-badge ${valBadgeClass}">${safeText(valBadgeText)}</span>
      <span class="muted">Artifacts dir: <strong>${safeText(artifacts)}</strong></span>
      ${valMeta}
    </div>
    ${callout}
    ${outcomesTable || `<p class="muted perf-outcomes-empty">No recent outcome rows yet.</p>`}
  `;
  if (rawDetails && data && !error && getDisplayMode() === "pro") rawDetails.open = true;
}

function renderProfilePanel(rootEl, data, { error } = {}) {
  const rawDetails = document.getElementById("profileRawDetails");
  const rawPre = document.getElementById("profileRaw");
  if (!rootEl) return;
  if (rawPre && !error && data) rawPre.textContent = prettyJson(data);
  if (rawDetails) {
    if (error || !data) rawDetails.classList.add("hidden");
    else rawDetails.classList.remove("hidden");
  }
  if (error) {
    rootEl.innerHTML = `<div class="panel-error">${safeText(error)}</div>`;
    return;
  }
  if (!data || typeof data !== "object") {
    rootEl.innerHTML = `<div class="report-empty">No preset loaded.</div>`;
    return;
  }

  const profile = safeText(data.profile || "—");
  const mode = safeText(data.mode || "standard");
  const expertUi = mode === "expert";
  const autoOn = Boolean(data.automation_opt_in);
  const active = data.active_profile_settings && typeof data.active_profile_settings === "object" ? data.active_profile_settings : {};
  const keys = Object.keys(active).sort();
  const catalog = state.presetCatalog && typeof state.presetCatalog === "object" ? state.presetCatalog : {};
  const profileKey = String(data.profile || "").toLowerCase();
  const dispMap =
    catalog[profileKey] && catalog[profileKey].settings_display && typeof catalog[profileKey].settings_display === "object"
      ? catalog[profileKey].settings_display
      : {};

  const settingsRows = keys
    .map((k) => {
      const d = dispMap[k] && typeof dispMap[k] === "object" ? dispMap[k] : {};
      const label = safeText(d.label || presetSettingLabel(k));
      const plain = safeText(d.plain || active[k]);
      const raw = safeText(d.raw != null ? d.raw : active[k]);
      const valueCell = expertUi ? `${plain}<br/><code class="preset-value">${raw}</code>` : plain;
      return `<tr><th scope="row">${label}</th><td>${valueCell}</td></tr>`;
    })
    .join("");

  const expert = data.expert_runtime_overrides && typeof data.expert_runtime_overrides === "object" ? data.expert_runtime_overrides : null;
  let expertBlock = "";
  if (expert) {
    const ek = Object.keys(expert).sort();
    const expertRows = ek
      .map((k) => `<tr><th scope="row"><code>${safeText(k)}</code></th><td>${safeText(expert[k])}</td></tr>`)
      .join("");
    expertBlock = `
      <div class="preset-subsection preset-expert">
        <h3>Runtime env (read-only)</h3>
        <table class="preset-kv-table">
          <tbody>${expertRows || `<tr><td colspan="2" class="muted">No values</td></tr>`}</tbody>
        </table>
      </div>`;
  }

  rootEl.innerHTML = `
    <div class="preset-chips">
      <span class="preset-chip">Profile: ${profile}</span>
      <span class="preset-chip muted-chip">Mode: ${mode}</span>
      <span class="preset-chip ${autoOn ? "" : "muted-chip"}">${autoOn ? "Automation: on" : "Automation: off"}</span>
    </div>
    <div class="preset-subsection">
      <h3>Active preset parameters</h3>
      <table class="preset-kv-table">
        <tbody>${
          settingsRows || `<tr><td colspan="2" class="muted">No parameters in response.</td></tr>`
        }</tbody>
      </table>
    </div>
    ${expertBlock}
  `;
}

function renderPresetApplyPreview() {
  const root = document.getElementById("presetApplyPreview");
  if (!root) return;
  const saved = state.savedUiSettings;
  const catalog = state.presetCatalog;
  if (!saved || !catalog || typeof catalog !== "object") {
    root.innerHTML = `<p class="muted small">Load presets to see a change summary.</p>`;
    return;
  }
  const selProfile = document.getElementById("profileSelect")?.value || saved.profile;
  const selMode = document.getElementById("settingsModeSelect")?.value || saved.mode;
  const selAuto = Boolean(document.getElementById("automationOptIn")?.checked);

  const cur = String(saved.profile || "balanced").toLowerCase();
  const next = String(selProfile || "balanced").toLowerCase();
  const curSet = catalog[cur]?.settings || {};
  const nextSet = catalog[next]?.settings || {};
  const keys = [...new Set([...Object.keys(curSet), ...Object.keys(nextSet)])].sort();

  const parts = [];
  if (next !== cur) {
    const blurb = safeText(catalog[next]?.blurb || "");
    parts.push(
      `<li><strong>Profile:</strong> ${safeText(cur)} → ${safeText(next)}.${blurb ? ` ${blurb}` : ""}</li>`
    );
  }
  keys.forEach((k) => {
    if (curSet[k] !== nextSet[k]) {
      const d0 = catalog[cur]?.settings_display?.[k] || {};
      const d1 = catalog[next]?.settings_display?.[k] || {};
      const label = safeText(d1.label || d0.label || presetSettingLabel(k));
      const fromPlain = safeText(d0.plain || curSet[k]);
      const toPlain = safeText(d1.plain || nextSet[k]);
      parts.push(`<li><strong>${label}:</strong> ${fromPlain} → ${toPlain}</li>`);
    }
  });
  if (String(selMode) !== String(saved.mode)) {
    const hint =
      selMode === "expert" ? "You will see raw env values under presets." : "Raw env values stay hidden.";
    parts.push(`<li><strong>Dashboard mode:</strong> ${safeText(saved.mode)} → ${safeText(selMode)}. ${hint}</li>`);
  }
  if (selAuto !== Boolean(saved.automation_opt_in)) {
    parts.push(
      `<li><strong>Automation opt-in (saved setting):</strong> ${saved.automation_opt_in ? "on" : "off"} → ${selAuto ? "on" : "off"}. When off, API clients must pass an explicit live-confirmation flag; this dashboard still makes you type the ticker to approve.</li>`
    );
  }

  if (!parts.length) {
    root.innerHTML = `<p class="muted preset-preview-none">No changes to apply.</p>`;
    return;
  }
  root.innerHTML = `<h3 class="preset-preview-title">If you apply now</h3><ul class="preset-preview-list">${parts.join("")}</ul>`;
}

function timeAgo(iso) {
  if (!iso) return "unknown";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "unknown";
  const sec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

function durationSec(startIso, endIso) {
  const start = Date.parse(startIso || "");
  const end = Date.parse(endIso || "");
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  return Math.max(0, Math.floor((end - start) / 1000));
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

function statusClass(status) {
  const s = (status || "").toLowerCase();
  if (["executed", "approved", "connected", "ok"].includes(s)) return "pill good";
  if (["failed", "rejected", "expired", "disconnected", "fail"].includes(s)) return "pill bad";
  if (["pending", "degraded", "warn"].includes(s)) return "pill warn";
  if (["info"].includes(s)) return "pill info";
  return "pill neutral";
}

function sentimentTagClass(tag) {
  const t = String(tag || "").toUpperCase();
  if (t.includes("BULLISH")) return "pill good";
  if (t.includes("BEARISH")) return "pill bad";
  return "pill neutral";
}

function healthBadgeClass(ok) {
  return ok ? "health-badge bg-green-900" : "health-badge bg-red-900";
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

function verdictFromScore(score, high = 70, low = 45) {
  const n = safeNum(score, 0);
  if (n >= high) return "bullish";
  if (n <= low) return "bearish";
  return "neutral";
}

function logEvent({ message, kind = "system", severity = "info" }) {
  const list = document.getElementById("logList");
  if (!list) return;
  const item = document.createElement("li");
  item.innerHTML = `
    <div class="log-item">
      <span class="log-kind ${kind} ${severity === "error" ? "error" : ""}">${kind}</span>
      <span class="${statusClass(severity)}">${severity}</span>
      <span>${new Date().toLocaleTimeString()} - ${safeText(message)}</span>
    </div>
  `;
  list.prepend(item);
  while (list.children.length > 30) list.removeChild(list.lastChild);
  const mapped = severity === "error" ? "error" : severity === "warn" ? "warn" : "info";
  updateActionCenter({ title: `${kind.toUpperCase()} Update`, message: safeText(message), severity: mapped });
}

function setStatusPill(el, label) {
  const status = (label || "").toLowerCase();
  el.className = statusClass(status);
  const dotClass = status.includes("connect")
    ? "good"
    : status.includes("disconnect")
      ? "bad"
      : "warn";
  el.innerHTML = `<span class="status-dot ${dotClass}"></span>${safeText(label)}`;
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

function clampPct(v) {
  return Math.max(0, Math.min(100, safeNum(v, 0)));
}

function meterFromScore(score) {
  return clampPct(safeNum(score, 0));
}

function meterFromConviction(conviction) {
  return clampPct((safeNum(conviction, 0) + 100) / 2);
}

async function openQuickViewForTrade(row) {
  const panel = document.getElementById("quickViewPanel");
  const output = document.getElementById("quickViewOutput");
  panel.classList.add("open");
  output.textContent = "Loading decision card...";
  const out = await api.get(`/api/decision-card/${encodeURIComponent(row.ticker)}`);
  if (!out.ok) {
    output.textContent = `Quick view unavailable: ${out.error}`;
    return;
  }
  output.textContent = prettyJson(out.data);
}

function renderReportTabs(data) {
  const tabs = document.getElementById("reportTabs");
  tabs.innerHTML = "";
  if (!data) return;
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Report sections");
  const d = data.section && data.data ? { ticker: data.ticker, [data.section]: data.data } : data;
  const candidates = ["summary", "technical", "dcf", "comps", "health", "edgar", "mirofish", "synthesis"];
  const available = candidates.filter((key) => key === "summary" || d[key] !== undefined && d[key] !== null);
  if (!available.includes(state.activeReportTab)) state.activeReportTab = "summary";

  available.forEach((key) => {
    const btn = document.createElement("button");
    const tabId = `report-tab-${key}`;
    const panelId = `report-panel-${key}`;
    const selected = state.activeReportTab === key;
    btn.className = `report-tab ${state.activeReportTab === key ? "active" : ""}`;
    btn.id = tabId;
    btn.type = "button";
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", selected ? "true" : "false");
    btn.setAttribute("aria-controls", panelId);
    btn.tabIndex = selected ? 0 : -1;
    btn.textContent = key === "summary" ? "Summary" : key[0].toUpperCase() + key.slice(1);
    btn.addEventListener("click", () => {
      state.activeReportTab = key;
      renderReportTabs(data);
      renderReportVisual(data);
    });
    tabs.appendChild(btn);
  });
}

function renderReportVisual(data) {
  const root = document.getElementById("reportVisual");
  if (!root) return;
  if (!data) {
    root.innerHTML = `<div class="report-empty">No report data.</div>`;
    return;
  }

  const d = data.section && data.data ? { ticker: data.ticker, [data.section]: data.data } : data;
  const ticker = d.ticker || "—";
  const tech = d.technical || null;
  const dcf = d.dcf || null;
  const health = d.health || null;
  const comps = d.comps || null;
  const edgar = d.edgar || null;
  const miro = d.mirofish || null;
  const synthesis = d.synthesis || "";

  const sectionVerdicts = {
    technical: verdictFromScore(tech?.signal_score ?? 50, 65, 45),
    dcf: verdictFromScore(dcf?.margin_of_safety ?? 0, 10, -10),
    health: (health?.flags || []).length === 0 ? "bullish" : (health.flags.length >= 3 ? "bearish" : "neutral"),
    mirofish: verdictFromScore(miro?.conviction_score ?? 0, 30, -30),
  };

  const kpis = [
    { label: "Ticker", value: ticker },
    { label: "Stage 2", value: tech ? (tech.stage_2 ? "YES" : "NO") : "—" },
    { label: "Signal Score", value: tech?.signal_score != null ? `${safeNum(tech.signal_score).toFixed(0)}/100` : "—" },
    { label: "DCF MOS", value: dcf?.margin_of_safety != null ? `${safeNum(dcf.margin_of_safety).toFixed(1)}%` : "—" },
  ];

  const blocks = {
    summary: `
      <div class="report-section">
        <h4>Summary</h4>
        <div class="subtle">Top-level reading before diving into sections.</div>
        <ul class="report-bullets">
          <li>Technical Verdict: <span class="verdict ${sectionVerdicts.technical}">${sectionVerdicts.technical}</span></li>
          <li>DCF Verdict: <span class="verdict ${sectionVerdicts.dcf}">${sectionVerdicts.dcf}</span></li>
          <li>Health Verdict: <span class="verdict ${sectionVerdicts.health}">${sectionVerdicts.health}</span></li>
          <li>MiroFish Verdict: <span class="verdict ${sectionVerdicts.mirofish}">${sectionVerdicts.mirofish}</span></li>
        </ul>
      </div>`,
    technical: tech ? `
      <div class="report-section">
        <h4>Technical <span class="verdict ${sectionVerdicts.technical}">${sectionVerdicts.technical}</span></h4>
        <ul class="report-bullets">
          <li>Price: ${formatMoney(tech.current_price)}</li>
          <li>52w Range: ${formatMoney(tech.low_52w)} - ${formatMoney(tech.high_52w)}</li>
          <li>SMA 50/150/200: ${formatMoney(tech.sma_50)} / ${formatMoney(tech.sma_150)} / ${formatMoney(tech.sma_200)}</li>
          <li>VCP: ${tech.vcp ? "YES" : "NO"} | Sector: ${safeText(tech.sector_etf)}</li>
          <li>Takeaway: ${tech.stage_2 && tech.vcp ? "Trend and volume structure are aligned." : "Setup quality is incomplete."}</li>
        </ul>
      </div>` : "",
    dcf: dcf ? `
      <div class="report-section">
        <h4>DCF <span class="verdict ${sectionVerdicts.dcf}">${sectionVerdicts.dcf}</span></h4>
        <ul class="report-bullets">
          <li>Intrinsic Value: ${formatMoney(dcf.intrinsic_value)}</li>
          <li>Current Price: ${formatMoney(dcf.current_price)}</li>
          <li>Margin of Safety: ${safeNum(dcf.margin_of_safety).toFixed(1)}%</li>
          <li>Growth / WACC / Terminal: ${pct(dcf.growth_rate)} / ${pct(dcf.wacc)} / ${pct(dcf.terminal_growth)}</li>
          <li>Takeaway: ${safeNum(dcf.margin_of_safety) >= 0 ? "Valuation supports upside." : "Valuation implies premium pricing."}</li>
        </ul>
      </div>` : "",
    comps: comps ? `
      <div class="report-section">
        <h4>Comps</h4>
        <ul class="report-bullets">
          <li>Peers: ${(comps.peers || []).slice(0, 6).map((p) => p.ticker).join(", ") || "—"}</li>
          <li>Median P/E: ${safeText(comps.median_pe)} | Median P/S: ${safeText(comps.median_ps)}</li>
          <li>Implied P/E: ${formatMoney(comps.implied_price_pe)} | Implied P/S: ${formatMoney(comps.implied_price_ps)}</li>
          <li>Takeaway: Comps provide cross-check against standalone DCF assumptions.</li>
        </ul>
      </div>` : "",
    health: health ? `
      <div class="report-section">
        <h4>Health <span class="verdict ${sectionVerdicts.health}">${sectionVerdicts.health}</span></h4>
        <ul class="report-bullets">
          <li>Current Ratio: ${safeText(health.current_ratio)}</li>
          <li>Debt/Equity: ${safeText(health.debt_to_equity)}</li>
          <li>Interest Coverage: ${safeText(health.interest_coverage)}x</li>
          <li>ROE: ${pct(health.roe)} | Op Margin: ${pct(health.operating_margin)}</li>
          <li>Flags: ${(health.flags || []).length ? health.flags.slice(0, 3).join("; ") : "None"}</li>
        </ul>
      </div>` : "",
    edgar: edgar ? `
      <div class="report-section">
        <h4>EDGAR</h4>
        <ul class="report-bullets">
          <li>Risk Tag: ${safeText(edgar.risk_tag).toUpperCase()}</li>
          <li>Recent 8-K: ${edgar.recent_8k ? "YES" : "NO"}</li>
          <li>Filing Recency: ${safeText(edgar.filing_recency_days)} day(s)</li>
          <li>Takeaway: ${(edgar.risk_reasons || []).slice(0, 2).join("; ") || "No notable filing risks."}</li>
        </ul>
      </div>` : "",
    mirofish: miro ? `
      <div class="report-section">
        <h4>MiroFish <span class="verdict ${sectionVerdicts.mirofish}">${sectionVerdicts.mirofish}</span></h4>
        <ul class="report-bullets">
          <li>Conviction: ${safeText(miro.conviction_score)}</li>
          <li>Continuation: ${pct(miro.continuation_probability, 0)}</li>
          <li>Bull Trap: ${pct(miro.bull_trap_probability, 0)}</li>
          <li>Takeaway: ${safeText(miro.summary || "No summary provided.")}</li>
        </ul>
      </div>` : "",
    synthesis: synthesis ? `
      <div class="report-section">
        <h4>Synthesis</h4>
        <div class="report-text">${safeText(synthesis)}</div>
      </div>` : "",
  };

  const tab = state.activeReportTab || "summary";
  root.setAttribute("role", "tabpanel");
  root.setAttribute("id", `report-panel-${tab}`);
  root.setAttribute("aria-labelledby", `report-tab-${tab}`);
  root.innerHTML = `
    <div class="report-grid">
      ${kpis.map((k) => `<div class="report-kpi"><div class="label">${k.label}</div><div class="value">${safeText(k.value)}</div></div>`).join("")}
    </div>
    ${blocks[tab] || blocks.summary}
  `;
}

function applyReportViewMode() {
  const raw = document.getElementById("reportOutput");
  const visual = document.getElementById("reportVisual");
  const btn = document.getElementById("toggleReportViewBtn");
  if (!raw || !visual || !btn) return;
  if (state.reportRawView) {
    raw.style.display = "block";
    visual.style.display = "none";
    btn.textContent = "Show Visual";
  } else {
    raw.style.display = "none";
    visual.style.display = "grid";
    btn.textContent = "Show Raw JSON";
  }
}

function applySecCompareMode() {
  const modeEl = document.getElementById("secCompareMode");
  const tickerB = document.getElementById("secCompareTickerB");
  const changesOnly = document.getElementById("secCompareChangesOnly");
  if (!modeEl || !tickerB) return;
  const mode = modeEl.value;
  const requiresSecondTicker = mode === "ticker_vs_ticker";
  tickerB.disabled = !requiresSecondTicker;
  tickerB.placeholder = requiresSecondTicker ? "Ticker B (MSFT)" : "Not required for over-time mode";
  if (changesOnly) {
    changesOnly.disabled = mode !== "ticker_over_time";
    if (mode !== "ticker_over_time") changesOnly.checked = false;
  }
}

function renderSecAnalysisCard(label, analysis) {
  if (!analysis) return "";
  const themes = (analysis.key_themes || []).slice(0, 3).map((t) => `<li>${safeText(t)}</li>`).join("");
  const risks = (analysis.risk_terms || []).slice(0, 5).join(", ") || "None highlighted";
  const guidance = safeText(analysis.guidance_signal || "neutral");
  const takeaway = safeText(analysis.high_level_takeaway || "No takeaway.");
  return `
    <div class="compare-card">
      <h4>${safeText(label)}</h4>
      <ul class="report-bullets">
        <li>Form: ${safeText(analysis.form)} | Filed: ${safeText(analysis.filing_date)}</li>
        <li>Guidance: <span class="${statusClass(guidance === "negative" ? "bad" : guidance === "positive" ? "good" : "neutral")}">${guidance}</span></li>
        <li>Risk terms: ${safeText(risks)}</li>
        <li>Takeaway: ${takeaway}</li>
      </ul>
      <div class="subtle">Top themes</div>
      <ul class="report-bullets">${themes || "<li>No theme sentences extracted.</li>"}</ul>
    </div>
  `;
}

function toReadableDeltaLabel(key) {
  const map = {
    revenue_mentions: "Revenue references",
    profit_mentions: "Profitability references",
    cashflow_mentions: "Cash-flow references",
    debt_mentions: "Debt references",
    liquidity_mentions: "Liquidity references",
  };
  return map[key] || String(key || "").replaceAll("_", " ");
}

function buildNarrativeSummary(comparePayload) {
  const compare = comparePayload?.compare || {};
  if (compare.narrative_summary) return safeText(compare.narrative_summary);

  const similarities = compare.similarities || [];
  const differences = compare.differences || [];
  const material = compare.material_changes || [];
  const investor = compare.investor_takeaway || "No investor takeaway was generated.";

  const firstSimilarity = similarities[0] || "The filings share limited direct overlap.";
  const firstDifference = differences[0] || "No major contrast surfaced in the initial pass.";
  const firstMaterial = material[0] || "No strongly material disclosure change was detected.";
  return `${investor} ${firstSimilarity} ${firstDifference} ${firstMaterial}`;
}

function renderSecCompareEmpty(message) {
  const headlineRoot = document.getElementById("secCompareHeadline");
  const narrativeRoot = document.getElementById("secCompareNarrative");
  const changesRoot = document.getElementById("secCompareChanges");
  const evidenceRoot = document.getElementById("secCompareVisual");
  const msg = safeText(message || "No SEC compare data available.");
  if (headlineRoot) headlineRoot.innerHTML = `<div class="report-empty">${msg}</div>`;
  if (narrativeRoot) narrativeRoot.innerHTML = `<div class="report-empty">${msg}</div>`;
  if (changesRoot) changesRoot.innerHTML = `<div class="report-empty">${msg}</div>`;
  if (evidenceRoot) evidenceRoot.innerHTML = `<div class="report-empty">${msg}</div>`;
}

function renderSecCompareVisual(data) {
  const headlineRoot = document.getElementById("secCompareHeadline");
  const narrativeRoot = document.getElementById("secCompareNarrative");
  const changesRoot = document.getElementById("secCompareChanges");
  const evidenceRoot = document.getElementById("secCompareVisual");
  if (!headlineRoot || !narrativeRoot || !changesRoot || !evidenceRoot) return;
  if (!data || !data.ok) {
    renderSecCompareEmpty("No SEC compare data available.");
    return;
  }

  const compare = data.compare || {};
  const left = data.left || data.latest || null;
  const right = data.right || data.prior || null;
  const leftLabel = compare.left_label || "Left";
  const rightLabel = compare.right_label || "Right";
  const forensic = compare.forensic_divergence || {};
  const sentimentTag = safeText(compare.sentiment_tag || forensic.sentiment_tag || "[NEUTRAL/BOILERPLATE]");
  const similaritiesRaw = compare.top_commonalities || compare.similarities || [];
  const differencesRaw = compare.top_differences || compare.differences || [];
  const materialRaw = compare.material_changes || [];
  const similarities = similaritiesRaw.slice(0, 6).map((x) => `<li>${safeText(x)}</li>`).join("");
  const differences = differencesRaw.slice(0, 6).map((x) => `<li>${safeText(x)}</li>`).join("");
  const material = materialRaw.slice(0, 6).map((x) => `<li>${safeText(x)}</li>`).join("");
  const deltas = compare.metric_deltas || {};
  const deltaChips = Object.entries(deltas)
    .map(([k, v]) => `<span class="delta-chip">${safeText(toReadableDeltaLabel(k))}: ${safeNum(v, 0) >= 0 ? "+" : ""}${safeText(v)}</span>`)
    .join("");
  const headline = safeText(compare.summary_headline || compare.investor_takeaway || "SEC compare completed");
  const narrative = safeText(buildNarrativeSummary(data));
  const redFlags = Array.isArray(forensic.red_flag_ledger) ? forensic.red_flag_ledger : [];
  const moat = forensic.margin_moat_check || {};
  const moatBullets = Array.isArray(moat.bullets) ? moat.bullets : [];
  const tldrVerdict = safeText(forensic.tldr_verdict || compare.investor_takeaway || "No clear divergence verdict generated.");

  headlineRoot.innerHTML = `
    <div class="report-section compare-headline-card">
      <h4>SEC Compare Verdict</h4>
      <div><span class="${sentimentTagClass(sentimentTag)}">${sentimentTag}</span></div>
      <div class="compare-lead">${headline}</div>
      <div class="subtle">Mode: ${safeText(data.mode || compare.mode || "N/A")} | Form: ${safeText(data.form_type || "N/A")}</div>
    </div>
  `;

  narrativeRoot.innerHTML = `
    <div class="report-section compare-narrative-card">
      <h4>The "Red Flag" Ledger</h4>
      <ul class="report-bullets">
        ${(redFlags.length ? redFlags : differencesRaw.slice(0, 4)).map((x) => `<li>${safeText(x)}</li>`).join("") || "<li>No newly introduced legal-risk language flagged.</li>"}
      </ul>
      <div class="subtle">Focus: new legal/risk language in recent filing that did not appear in comparator.</div>
    </div>
  `;

  changesRoot.innerHTML = `
    <div class="report-section compare-changes-card">
      <h4>Margin &amp; Moat Check</h4>
      <ul class="report-bullets">
        ${(moatBullets.length ? moatBullets : [narrative]).map((x) => `<li>${safeText(x)}</li>`).join("")}
      </ul>
      <div class="subtle">Metric Context</div>
      <div>${deltaChips || "<span class='muted'>No material metric deltas captured.</span>"}</div>
      <div class="subtle">The "TL;DR Verdict"</div>
      <div class="compare-lead">${tldrVerdict}</div>
      <div class="subtle">Shared context</div>
      <ul class="report-bullets">${similarities || "<li>No major similarities highlighted.</li>"}</ul>
      <div class="subtle">Divergence context</div>
      <ul class="report-bullets">${material || differences || "<li>No major differences highlighted.</li>"}</ul>
    </div>
  `;

  evidenceRoot.innerHTML = `
    <div class="compare-grid">
      ${renderSecAnalysisCard(leftLabel, left)}
      ${renderSecAnalysisCard(rightLabel, right)}
    </div>
  `;
  const deep = document.getElementById("secCompareDeepPanel");
  if (deep && getDisplayMode() === "pro") deep.open = true;
}

async function buildFallbackSecCompare(mode, tickerA, tickerB, formType) {
  const safeForm = (formType || "10-K").toUpperCase();
  const fetchEdgar = async (ticker) => {
    const out = await api.get(`/api/report/${ticker}?section=edgar&skip_mirofish=true&skip_edgar=false`, { timeoutMs: 180000 });
    if (!out.ok) return { ok: false, error: out.error || `Failed report fetch for ${ticker}` };
    const sectionData = out.data?.data || out.data?.edgar || null;
    if (!sectionData) return { ok: false, error: `Missing EDGAR payload for ${ticker}` };
    const filings = (sectionData.recent_filings || []).filter((f) => String(f.form || "").toUpperCase() === safeForm);
    const filing = filings[0] || sectionData.recent_filings?.[0] || {};
    return {
      ok: true,
      ticker: ticker,
      form: filing.form || safeForm,
      filing_date: filing.date || "N/A",
      filing_url: filing.url || "",
      guidance_signal: "neutral",
      key_themes: (sectionData.risk_reasons || []).slice(0, 4).map((r) => `Risk note: ${r}`),
      risk_terms: (sectionData.risk_reasons || []).map((r) => String(r).toLowerCase()),
      high_level_takeaway: (sectionData.risk_reasons || []).length
        ? sectionData.risk_reasons.slice(0, 2).join("; ")
        : "No notable filing risks in current metadata snapshot.",
      kpi_signals: {
        revenue_mentions: [],
        profit_mentions: [],
        cashflow_mentions: [],
        debt_mentions: [],
        liquidity_mentions: [],
      },
    };
  };

  const toComparePayload = (left, right, compareMode, leftLabel, rightLabel) => {
    const leftRisks = new Set(left.risk_terms || []);
    const rightRisks = new Set(right.risk_terms || []);
    const commonRisks = [...leftRisks].filter((x) => rightRisks.has(x));
    const leftOnly = [...leftRisks].filter((x) => !rightRisks.has(x));
    const rightOnly = [...rightRisks].filter((x) => !leftRisks.has(x));
    const differences = [];
    if (leftOnly.length) differences.push(`${leftLabel} unique risk notes: ${leftOnly.slice(0, 4).join(", ")}.`);
    if (rightOnly.length) differences.push(`${rightLabel} unique risk notes: ${rightOnly.slice(0, 4).join(", ")}.`);
    if (!differences.length) differences.push("Risk posture appears similar based on EDGAR metadata.");
    const sentimentTag = differences.length > 1 ? "[BEARISH CHANGE]" : "[NEUTRAL/BOILERPLATE]";
    const redFlagLedger = differences.slice(0, 3);
    const marginMoatBullets = [
      `${leftLabel}: revenue references from metadata are limited; innovation signal may be undercounted in fallback mode.`,
      `${rightLabel}: revenue references from metadata are limited; innovation signal may be undercounted in fallback mode.`,
    ];
    const tldrVerdict = `${leftLabel} vs ${rightLabel} remains inconclusive under metadata-only mode; use full SEC compare endpoint for a reliable divergence call.`;
    return {
      ok: true,
      mode: compareMode,
      form_type: safeForm,
      left,
      right,
      compare: {
        ok: true,
        mode: compareMode,
        left_label: leftLabel,
        right_label: rightLabel,
        similarities: commonRisks.length
          ? [`Shared risk notes: ${commonRisks.slice(0, 5).join(", ")}.`]
          : ["Limited overlap from metadata-only filing notes."],
        differences,
        metric_deltas: {
          revenue_mentions: 0,
          profit_mentions: 0,
          cashflow_mentions: 0,
          r_and_d_mentions: 0,
          debt_mentions: 0,
          liquidity_mentions: 0,
        },
        sentiment_tag: sentimentTag,
        forensic_divergence: {
          sentiment_tag: sentimentTag,
          red_flag_ledger: redFlagLedger,
          margin_moat_check: {
            left_label: leftLabel,
            right_label: rightLabel,
            left_revenue_refs: 0,
            left_r_and_d_refs: 0,
            right_revenue_refs: 0,
            right_r_and_d_refs: 0,
            bullets: marginMoatBullets,
          },
          tldr_verdict: tldrVerdict,
        },
        material_changes: [],
        summary_headline: "Metadata-only compare completed.",
        narrative_summary: "This compare uses EDGAR metadata fallback only. It highlights broad risk-note overlap and differences but does not parse full filing text.",
        top_differences: differences.slice(0, 3),
        top_commonalities: commonRisks.length
          ? [`Shared risk notes: ${commonRisks.slice(0, 5).join(", ")}.`]
          : ["Limited overlap from metadata-only filing notes."],
        investor_takeaway: "Fallback compare is based on EDGAR metadata only. Enable SEC compare API for deeper filing-text analysis.",
      },
    };
  };

  if (mode === "ticker_vs_ticker") {
    const [left, right] = await Promise.all([fetchEdgar(tickerA), fetchEdgar(tickerB)]);
    if (!left.ok) return { ok: false, error: left.error };
    if (!right.ok) return { ok: false, error: right.error };
    return toComparePayload(left, right, mode, tickerA, tickerB);
  }

  const latest = await fetchEdgar(tickerA);
  if (!latest.ok) return { ok: false, error: latest.error };
  return toComparePayload(
    { ...latest, ticker: tickerA, filing_date: latest.filing_date || "latest" },
    { ...latest, ticker: tickerA, filing_date: "prior (metadata fallback)", high_level_takeaway: "Prior filing text compare unavailable in fallback mode." },
    mode,
    `${tickerA} latest`,
    `${tickerA} prior`,
  );
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
    checklistText = formatPreflightChecklistHtml(c);
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
  if (tickerInput) {
    tickerInput.value = "";
    tickerInput.placeholder = String(row.ticker || "TICKER");
  }
  state.approvingTradeId = row.id;
  dialog.showModal();
}

function applySchwabConnectButtonVisibility() {
  const pc = state.publicConfig || {};
  document.getElementById("onboardingSchwabBtn")?.classList.toggle("hidden", !pc.schwab_oauth);
  document.getElementById("onboardingSchwabMarketBtn")?.classList.toggle("hidden", !pc.schwab_market_oauth);
}

async function loadConfig() {
  const tokenInput = document.getElementById("jwtInput");
  const saveBtn = document.getElementById("saveJwtBtn");
  const manualDetails = document.getElementById("manualJwtDetails");
  const manualSummary = document.getElementById("manualJwtSummary");
  const supabaseBlock = document.getElementById("supabaseAuthBlock");

  let publicCfg = { supabase: null, saas_mode: false, schwab_oauth: false, schwab_market_oauth: false };
  try {
    const res = await fetch("/api/public-config", { headers: { Accept: "application/json" } });
    const body = res.ok ? await res.json() : {};
    if (body?.ok && body?.data) publicCfg = { ...publicCfg, ...body.data };
  } catch {
    /* offline or boot — fall back to manual JWT only */
  }
  state.publicConfig = publicCfg;
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
      manualDetails.classList.remove("hidden");
      manualDetails.open = false;
    }
    if (manualSummary) {
      manualSummary.textContent = "Session token";
      manualSummary.classList.add("manual-jwt-summary--hidden");
    }
    markAuthReady();
  }

  if (tokenInput) {
    tokenInput.value = readStoredApiJwt();
  }
  if (saveBtn) {
    saveBtn.addEventListener("click", () => {
      const val = tokenInput?.value?.trim() || "";
      if (val) {
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
  updateActionCenter({
    title: "Authentication Required",
    message: hasSupabaseUi
      ? "Sign in with Supabase to access protected APIs. Your session token is used automatically."
      : "Paste a valid Supabase JWT and click Save Token to access protected APIs.",
    severity: "warn",
  });

  const params = new URLSearchParams(window.location.search);
  const oauthSt = params.get("schwab_oauth");
  const marketOauthSt = params.get("schwab_market_oauth");
  if (oauthSt || marketOauthSt) {
    const msg = params.get("message") || "";
    const u = new URL(window.location.href);
    u.searchParams.delete("schwab_oauth");
    u.searchParams.delete("schwab_market_oauth");
    u.searchParams.delete("message");
    window.history.replaceState({}, "", u.pathname + (u.search ? u.search : ""));
    applySchwabConnectButtonVisibility();

    if (oauthSt) {
      if (oauthSt === "ok") {
        logEvent({ kind: "system", severity: "info", message: "Schwab account linked successfully." });
        updateActionCenter({
          title: "Schwab",
          message: "Brokerage side linked (balances, positions, orders). If you have not yet, also connect market data.",
          severity: "success",
        });
      } else {
        logEvent({ kind: "system", severity: "error", message: `Schwab OAuth: ${msg || "failed"}` });
        updateActionCenter({ title: "Schwab OAuth", message: msg || "Connection failed.", severity: "error" });
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
      } else {
        logEvent({ kind: "system", severity: "error", message: `Schwab market OAuth: ${msg || "failed"}` });
        updateActionCenter({
          title: "Schwab market OAuth",
          message: msg || "Connection failed.",
          severity: "error",
        });
      }
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
  const [statusRes, deepRes] = await Promise.all([
    api.get("/api/status"),
    api.get("/api/health/deep", { timeoutMs: 30000 }),
  ]);
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
    const status = await api.get(`/api/scan/${encodeURIComponent(taskId)}`);
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
    const status = await api.get("/api/scan/status");
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
      if (row) await openQuickViewForTrade(row);
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
  if (!typed) {
    updateActionCenter({
      title: "Confirm ticker",
      message: "Type the trade ticker in the box to confirm this live order.",
      severity: "warn",
    });
    return;
  }
  const out = await api.post(`/api/trades/${id}/approve?confirm_live=true`, { typed_ticker: typed });
  if (!out.ok) {
    logEvent({ kind: "trade", severity: "error", message: `Approve ${id} failed: ${out.error}` });
    updateActionCenter({ title: "Approval Failed", message: out.error, severity: "error" });
  } else {
    logEvent({ kind: "trade", severity: "info", message: `Approved ${id}: order submitted.` });
    updateActionCenter({ title: "Trade Approved", message: `Trade ${id} approved and submitted.`, severity: "success" });
  }
  await refreshPending();
}

async function refreshOnboarding() {
  const out = await api.get("/api/onboarding/status");
  const meta = document.getElementById("onboardingMeta");
  const output = document.getElementById("onboardingOutput");
  const section = document.getElementById("onboardingSection");
  if (!meta || !output) return;
  if (!out.ok) {
    output.textContent = `Onboarding status failed: ${out.error}`;
    return;
  }
  state.onboarding = out.data;
  if (section) {
    section.style.display = "block";
  }
  const conn = out.data?.connection_status || (out.data?.schwab_linked ? "connected" : "disconnected");
  const ah = out.data?.api_health || {};
  const apiLine = ah.schwab_linked
    ? `API: market ${ah.market_token_ok ? "ok" : "—"} · account ${ah.account_token_ok ? "ok" : "—"} · quotes ${ah.quote_ok ? "ok" : "—"}`
    : "API: connect Schwab to probe tokens and quotes.";
  const haltLine = state.publicConfig.platform_live_trading_kill_switch ? " · Global operator halt: ON" : "";
  if (!out.data?.onboarding_required) {
    meta.textContent = `Connection: ${conn} · ${apiLine}${haltLine}`;
    output.textContent = prettyJson(out.data);
    return;
  }
  const elapsed = out.data?.elapsed_minutes;
  const done = out.data?.completed_under_target;
  meta.textContent = `Connection: ${conn} · ${apiLine}${haltLine} · Elapsed: ${elapsed ?? "n/a"} min | ${done ? "PASS" : "IN PROGRESS"}`;
  output.textContent = prettyJson(out.data);
}

async function startOnboarding() {
  await runLazyApi("onboarding");
  const out = await api.post("/api/onboarding/start", {});
  if (!out.ok) {
    logEvent({ kind: "system", severity: "error", message: `Onboarding start failed: ${out.error}` });
    const pre = document.getElementById("onboardingOutput");
    if (pre) pre.textContent = `Start failed: ${out.error}`;
    updateActionCenter({ title: "Schwab setup", message: out.error || "Could not start onboarding.", severity: "error" });
    return;
  }
  logEvent({ kind: "system", severity: "info", message: "Setup wizard started." });
  await refreshOnboarding();
}

async function runOnboardingStep(step) {
  await runLazyApi("onboarding");
  const out = await api.post(`/api/onboarding/step/${step}`, {});
  if (!out.ok) {
    logEvent({ kind: "system", severity: "error", message: `Onboarding step failed: ${out.error}` });
    const pre = document.getElementById("onboardingOutput");
    if (pre) pre.textContent = `Step failed (${step}): ${out.error}`;
    updateActionCenter({ title: "Schwab setup", message: out.error || `Step ${step} failed.`, severity: "error" });
    return;
  }
  logEvent({ kind: "system", severity: "info", message: `Onboarding step complete: ${step}.` });
  await refreshOnboarding();
}

async function loadProfiles() {
  const mode = document.getElementById("settingsModeSelect")?.value || "standard";
  const expert = mode === "expert";
  const out = await api.get(`/api/settings/profiles?expert=${expert}`);
  const panel = document.getElementById("profilePanel");
  if (!panel) return;
  if (!out.ok) {
    renderProfilePanel(panel, null, { error: `Profile load failed: ${out.error}` });
    return;
  }
  state.profile = out.data;
  state.presetCatalog =
    out.data.preset_catalog && typeof out.data.preset_catalog === "object" ? out.data.preset_catalog : {};
  state.savedUiSettings = {
    profile: out.data.profile || "balanced",
    mode: out.data.mode || "standard",
    automation_opt_in: Boolean(out.data.automation_opt_in),
  };
  document.getElementById("profileSelect").value = out.data.profile || "balanced";
  document.getElementById("settingsModeSelect").value = out.data.mode || "standard";
  document.getElementById("automationOptIn").checked = Boolean(out.data.automation_opt_in);
  renderProfilePanel(panel, out.data);
  renderPresetApplyPreview();
}

async function applyProfile() {
  const profile = document.getElementById("profileSelect").value;
  const mode = document.getElementById("settingsModeSelect").value;
  const automationOptIn = document.getElementById("automationOptIn").checked;
  const out = await api.post(`/api/settings/profile?profile=${encodeURIComponent(profile)}&mode=${encodeURIComponent(mode)}&automation_opt_in=${automationOptIn}`, {});
  const panel = document.getElementById("profilePanel");
  if (!out.ok) {
    if (panel) renderProfilePanel(panel, null, { error: `Apply preset failed: ${out.error}` });
    logEvent({ kind: "system", severity: "error", message: `Preset apply failed: ${out.error}` });
    return;
  }
  logEvent({
    kind: "system",
    severity: "info",
    message: `Preset: ${profile}, automation ${automationOptIn ? "on" : "off"}, ${mode} mode.`,
  });
  updateActionCenter({
    title: "Preset applied",
    message: `${profile} · ${mode} mode · automation ${automationOptIn ? "on" : "off"}`,
    severity: "success",
  });
  await loadProfiles();
}

function renderDecisionCardView(data, error) {
  const ph = document.getElementById("decisionPlaceholder");
  const sum = document.getElementById("decisionSummary");
  const det = document.getElementById("decisionJsonDetails");
  const pre = document.getElementById("decisionOutput");
  if (!pre) return;
  if (error) {
    if (ph) {
      ph.textContent = error;
      ph.classList.remove("hidden");
    }
    if (sum) {
      sum.classList.add("hidden");
      sum.innerHTML = "";
    }
    if (det) det.classList.add("hidden");
    pre.textContent = "";
    return;
  }
  if (ph) ph.classList.add("hidden");
  const ez = data.entry_zone || {};
  const sz = data.size || {};
  const conf = data.confidence || {};
  const blocked = Boolean(data.checklist && data.checklist.blocked);
  const scoreN = Number(conf.signal_score);
  const scoreTxt = Number.isFinite(scoreN) ? scoreN.toFixed(1) : "—";
  const verdict = blocked
    ? "Safety checks say do not send this live yet."
    : "Passes current safety snapshot; you still confirm each live order in the queue.";
  if (sum) {
    sum.classList.remove("hidden");
    sum.innerHTML = `
      <h4 class="tool-summary-title">${safeText(data.ticker)}</h4>
      <ul class="tool-summary-list">
        <li><strong>Size:</strong> ${safeNum(sz.qty, 0)} shares (~${formatMoney(sz.usd || 0)}).</li>
        <li><strong>Entry zone:</strong> ${safeText(ez.low)} – ${safeText(ez.high)}.</li>
        <li><strong>Stop idea:</strong> near ${safeText(data.stop_invalidation)}.</li>
        <li><strong>Confidence:</strong> ${safeText(conf.bucket)} (score ${scoreTxt}).</li>
        <li><strong>Live readiness:</strong> ${verdict}</li>
      </ul>
    `;
  }
  if (det) det.classList.remove("hidden");
  pre.textContent = prettyJson(data);
}

async function loadDecisionCard() {
  const ticker = document.getElementById("decisionTickerInput").value.trim().toUpperCase();
  if (!ticker) return;
  const out = await api.get(`/api/decision-card/${ticker}`);
  if (!out.ok) {
    renderDecisionCardView(null, `Decision card failed: ${out.error}`);
    return;
  }
  renderDecisionCardView(out.data, null);
}

function renderRecoveryView(data, error) {
  const ph = document.getElementById("recoveryPlaceholder");
  const sum = document.getElementById("recoverySummary");
  const det = document.getElementById("recoveryJsonDetails");
  const pre = document.getElementById("recoveryOutput");
  if (!pre) return;
  if (error) {
    if (ph) {
      ph.textContent = error;
      ph.classList.remove("hidden");
    }
    if (sum) {
      sum.classList.add("hidden");
      sum.innerHTML = "";
    }
    if (det) det.classList.add("hidden");
    pre.textContent = "";
    return;
  }
  if (ph) ph.classList.add("hidden");
  if (sum) {
    sum.classList.remove("hidden");
    sum.innerHTML = `
      <h4 class="tool-summary-title">${safeText(data.title)}</h4>
      <p class="tool-summary-p">${safeText(data.summary)}</p>
      <p class="tool-summary-next"><strong>Next step:</strong> ${safeText(data.fix_path)}</p>
    `;
  }
  if (det) det.classList.remove("hidden");
  pre.textContent = prettyJson(data);
}

async function mapRecovery() {
  const source = document.getElementById("recoverySource").value;
  const message = document.getElementById("recoveryMessage").value.trim();
  if (!message) return;
  const out = await api.get(`/api/recovery/map?source=${encodeURIComponent(source)}&error=${encodeURIComponent(message)}`);
  if (!out.ok) {
    renderRecoveryView(null, `Recovery mapping failed: ${out.error}`);
    return;
  }
  renderRecoveryView(out.data, null);
}

async function refreshPerformance() {
  const out = await api.get("/api/performance");
  const panel = document.getElementById("performancePanel");
  if (!panel) return;
  if (!out.ok) {
    renderPerformancePanel(panel, null, { error: `Performance load failed: ${out.error}` });
    return;
  }
  state.performance = out.data;
  renderPerformancePanel(panel, out.data);
}

function renderCalibrationPanel(panel, data, error) {
  if (!panel) return;
  if (error) {
    panel.innerHTML = `<div class="report-empty">${escapeHtml(error)}</div>`;
    return;
  }
  if (!data) {
    panel.innerHTML = `<div class="report-empty">No data.</div>`;
    return;
  }
  if (data.empty) {
    panel.innerHTML = `<div class="report-empty">${escapeHtml(data.hint || "No calibration snapshot yet.")}</div>`;
    return;
  }
  const parts = [];
  if (data.self_study) {
    parts.push(
      `<h3 class="field-label">Self-study</h3><pre class="code-block code-block--tight">${escapeHtml(
        prettyJson(data.self_study)
      )}</pre>`
    );
  }
  if (data.hypothesis_ledger) {
    parts.push(
      `<h3 class="field-label">Hypothesis ledger</h3><pre class="code-block code-block--tight">${escapeHtml(
        prettyJson(data.hypothesis_ledger)
      )}</pre>`
    );
  }
  panel.innerHTML =
    parts.length > 0
      ? parts.join("")
      : `<div class="muted">Unrecognized snapshot shape.</div><pre class="code-block code-block--tight">${escapeHtml(
          prettyJson(data)
        )}</pre>`;
}

async function refreshCalibration() {
  const panel = document.getElementById("calibrationPanel");
  if (!panel) return;
  const out = await api.get("/api/calibration/summary");
  if (!out.ok) {
    renderCalibrationPanel(panel, null, `Calibration load failed: ${out.error}`);
    return;
  }
  state.calibration = out.data;
  renderCalibrationPanel(panel, out.data, null);
}

async function submitTradingHaltSave() {
  if (!state.publicConfig.saas_mode) return;
  const halted = Boolean(document.getElementById("tradingHaltedCheckbox")?.checked);
  const out = await api.patch("/api/settings/trading-halt", { halted });
  if (!out.ok) {
    const msg = typeof out.error === "string" ? out.error : JSON.stringify(out.error || "Request failed");
    updateActionCenter({ title: "Trading pause", message: msg, severity: "error" });
    return;
  }
  updateActionCenter({
    title: halted ? "Trading paused" : "Trading pause cleared",
    message: halted
      ? "New live approvals are blocked until you turn this off."
      : "You may approve live trades again when live trading is enabled.",
    severity: "success",
  });
  await refreshAccountMe();
}

function setDefaultBacktestDates() {
  const startEl = document.getElementById("btStart");
  const endEl = document.getElementById("btEnd");
  if (startEl?.value && endEl?.value) return;
  const end = new Date();
  const start = new Date();
  start.setFullYear(end.getFullYear() - 5);
  const fmt = (d) => d.toISOString().slice(0, 10);
  if (startEl && !startEl.value) startEl.value = fmt(start);
  if (endEl && !endEl.value) endEl.value = fmt(end);
}

function restoreBacktestFormFromStorage() {
  try {
    const raw = localStorage.getItem(BACKTEST_PREFS_KEY);
    if (!raw) return false;
    const o = JSON.parse(raw);
    if (!o || typeof o !== "object") return false;
    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (!el || val === undefined || val === null) return;
      el.value = String(val);
    };
    setVal("btUniverse", o.universe);
    setVal("btTickers", o.tickers);
    setVal("btTheory", o.theory);
    setVal("btStart", o.start);
    setVal("btEnd", o.end);
    setVal("btSlippage", o.slippage);
    setVal("btFeeShare", o.feeShare);
    setVal("btMinFee", o.minFee);
    setVal("btMaxAdv", o.maxAdv);
    setVal("btQualityGates", o.qualityGates);
    setVal("btBreakoutConfirm", o.breakoutConfirm);
    setVal("btForensicMode", o.forensicMode);
    setVal("btPead", o.pead);
    const skip = document.getElementById("btSkipMirofish");
    if (skip && typeof o.skipMirofish === "boolean") skip.checked = o.skipMirofish;
    return true;
  } catch {
    return false;
  }
}

function snapshotBacktestFormForStorage() {
  return {
    universe: document.getElementById("btUniverse")?.value ?? "",
    tickers: document.getElementById("btTickers")?.value ?? "",
    theory: document.getElementById("btTheory")?.value ?? "",
    start: document.getElementById("btStart")?.value ?? "",
    end: document.getElementById("btEnd")?.value ?? "",
    slippage: document.getElementById("btSlippage")?.value ?? "",
    feeShare: document.getElementById("btFeeShare")?.value ?? "",
    minFee: document.getElementById("btMinFee")?.value ?? "",
    maxAdv: document.getElementById("btMaxAdv")?.value ?? "",
    qualityGates: document.getElementById("btQualityGates")?.value ?? "",
    breakoutConfirm: document.getElementById("btBreakoutConfirm")?.value ?? "",
    forensicMode: document.getElementById("btForensicMode")?.value ?? "",
    pead: document.getElementById("btPead")?.value ?? "",
    skipMirofish: Boolean(document.getElementById("btSkipMirofish")?.checked),
  };
}

let _backtestPersistTimer = null;
function schedulePersistBacktestForm() {
  if (_backtestPersistTimer) clearTimeout(_backtestPersistTimer);
  _backtestPersistTimer = setTimeout(() => {
    _backtestPersistTimer = null;
    try {
      localStorage.setItem(BACKTEST_PREFS_KEY, JSON.stringify(snapshotBacktestFormForStorage()));
    } catch {
      /* quota */
    }
  }, 400);
}

function wireBacktestFormPersistence() {
  const root = document.getElementById("backtestSection");
  if (!root) return;
  root.addEventListener("input", schedulePersistBacktestForm);
  root.addEventListener("change", schedulePersistBacktestForm);
}

function resetBacktestFormToDefaults() {
  localStorage.removeItem(BACKTEST_PREFS_KEY);
  const u = document.getElementById("btUniverse");
  if (u) u.value = "watchlist";
  const tick = document.getElementById("btTickers");
  if (tick) tick.value = "";
  const th = document.getElementById("btTheory");
  if (th) th.value = "";
  const s = document.getElementById("btStart");
  const e = document.getElementById("btEnd");
  if (s) s.value = "";
  if (e) e.value = "";
  setDefaultBacktestDates();
  const slip = document.getElementById("btSlippage");
  if (slip) slip.value = "15";
  const fee = document.getElementById("btFeeShare");
  if (fee) fee.value = "0.005";
  const minf = document.getElementById("btMinFee");
  if (minf) minf.value = "1";
  const adv = document.getElementById("btMaxAdv");
  if (adv) adv.value = "0.02";
  ["btQualityGates", "btBreakoutConfirm", "btForensicMode", "btPead"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  const skip = document.getElementById("btSkipMirofish");
  if (skip) skip.checked = false;
  syncBtUniverseRow();
  setBtMetaMessage("Form reset to defaults. Queue when ready.");
  logEvent({ kind: "system", severity: "info", message: "Backtest form reset." });
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

function setBacktestQueueUiBusy(busy) {
  state.backtestQueueBusy = busy;
  const btn = document.getElementById("btQueueBtn");
  if (btn) btn.disabled = busy;
  const spin = document.getElementById("btMetaSpinner");
  const metaText = document.getElementById("btMetaText");
  if (spin) spin.classList.toggle("hidden", !busy);
  if (metaText && busy && !metaText.dataset.sticky) metaText.textContent = "Running…";
}

function setBtMetaMessage(text, { sticky = false } = {}) {
  const metaText = document.getElementById("btMetaText");
  if (!metaText) return;
  metaText.textContent = text;
  if (sticky) metaText.dataset.sticky = "1";
  else delete metaText.dataset.sticky;
}

function syncBtUniverseRow() {
  const sel = document.getElementById("btUniverse");
  const row = document.getElementById("btTickersRow");
  if (!row) return;
  const mode = sel?.value || "watchlist";
  row.classList.toggle("hidden", mode !== "tickers");
}

function applyBacktestPresetYears(years) {
  const end = new Date();
  const start = new Date();
  start.setFullYear(end.getFullYear() - Number(years));
  const fmt = (d) => d.toISOString().slice(0, 10);
  const startEl = document.getElementById("btStart");
  const endEl = document.getElementById("btEnd");
  if (startEl) startEl.value = fmt(start);
  if (endEl) endEl.value = fmt(end);
  setBtMetaMessage(`Date range set to last ${years} year(s).`);
  schedulePersistBacktestForm();
}

function collectBacktestOverrides() {
  const o = {};
  const q = document.getElementById("btQualityGates")?.value || "";
  if (q) o.quality_gates_mode = q;
  const bo = document.getElementById("btBreakoutConfirm")?.value || "";
  if (bo === "on") o.breakout_confirm_enabled = true;
  if (bo === "off") o.breakout_confirm_enabled = false;
  const pead = document.getElementById("btPead")?.value || "";
  if (pead === "on") o.pead_enabled = true;
  if (pead === "off") o.pead_enabled = false;
  if (document.getElementById("btSkipMirofish")?.checked) o.skip_mirofish = true;
  const fm = document.getElementById("btForensicMode")?.value || "";
  if (fm === "disabled") o.forensic_enabled = false;
  else if (fm === "shadow" || fm === "soft" || fm === "hard") {
    o.forensic_enabled = true;
    o.forensic_filter_mode = fm;
  } else if (fm === "off") {
    o.forensic_enabled = true;
    o.forensic_filter_mode = "off";
  }
  return Object.keys(o).length ? o : null;
}

function collectBacktestSpecFromForm() {
  const theory = document.getElementById("btTheory")?.value?.trim() || "";
  const universe = document.getElementById("btUniverse")?.value || "watchlist";
  const tickersRaw = document.getElementById("btTickers")?.value || "";
  const tickers = tickersRaw
    .split(/[\s,]+/)
    .map((t) => t.trim().toUpperCase())
    .filter(Boolean);
  const start = document.getElementById("btStart")?.value;
  const end = document.getElementById("btEnd")?.value;
  const slip = Number(document.getElementById("btSlippage")?.value);
  const fee = Number(document.getElementById("btFeeShare")?.value);
  const minf = Number(document.getElementById("btMinFee")?.value);
  const adv = Number(document.getElementById("btMaxAdv")?.value);
  const spec = {
    schema_version: 1,
    universe_mode: universe === "tickers" ? "tickers" : "watchlist",
    tickers: universe === "tickers" ? tickers : [],
    start_date: start,
    end_date: end,
  };
  if (theory) spec.theory_name = theory;
  if (Number.isFinite(slip)) spec.slippage_bps_per_side = slip;
  if (Number.isFinite(fee)) spec.fee_per_share = fee;
  if (Number.isFinite(minf)) spec.min_fee_per_order = minf;
  if (Number.isFinite(adv)) spec.max_adv_participation = adv;
  const ov = collectBacktestOverrides();
  if (ov) spec.overrides = ov;
  return spec;
}

function renderBacktestResultSummary(result) {
  const box = document.getElementById("btResultSummary");
  if (!box) return;
  if (!result || typeof result !== "object") {
    box.innerHTML = "";
    return;
  }
  const tt = result.total_trades;
  if (tt === undefined || tt === null) {
    box.innerHTML = "";
    return;
  }
  const findings = typeof result.findings === "string" ? result.findings : "";
  box.innerHTML = `
    <div class="bt-metric-grid">
      <div class="bt-metric"><div class="bt-metric-label">Trades</div><div class="bt-metric-value">${safeText(tt)}</div></div>
      <div class="bt-metric"><div class="bt-metric-label">Win rate (net)</div><div class="bt-metric-value">${formatPercentPoints(result.win_rate_net, 1)}</div></div>
      <div class="bt-metric"><div class="bt-metric-label">Total return (net)</div><div class="bt-metric-value">${formatPercentPoints(result.total_return_net_pct, 2)}</div></div>
      <div class="bt-metric"><div class="bt-metric-label">CAGR (net)</div><div class="bt-metric-value">${formatPercentPoints(result.cagr_net_pct, 2)}</div></div>
      <div class="bt-metric"><div class="bt-metric-label">Max drawdown (net)</div><div class="bt-metric-value">${formatPercentPoints(result.max_drawdown_net_pct, 2)}</div></div>
    </div>
    ${findings ? `<div class="bt-findings">${escapeHtml(findings)}</div>` : ""}
  `;
}

function renderBacktestResultRaw(result, fallbackText) {
  const pre = document.getElementById("btResult");
  const details = document.getElementById("btResultRawDetails");
  if (!pre) return;
  if (result && typeof result === "object") {
    pre.textContent = prettyJson(result);
    if (details) details.open = getDisplayMode() === "pro";
  } else {
    pre.textContent = fallbackText || "No run yet.";
    if (details) details.open = false;
  }
}

function backtestSpecSummaryLine(spec) {
  if (!spec || typeof spec !== "object") return "";
  const mode = spec.universe_mode === "tickers" ? "custom tickers" : "watchlist";
  const dr = spec.start_date && spec.end_date ? `${safeText(spec.start_date)} → ${safeText(spec.end_date)}` : "";
  const n = Array.isArray(spec.tickers) ? spec.tickers.length : 0;
  const tickPart = spec.universe_mode === "tickers" && n ? ` · ${n} names` : "";
  return `${mode}${tickPart}${dr ? ` · ${dr}` : ""}`;
}

function strategyChatPayloadMessages() {
  return (Array.isArray(state.strategyChatMessages) ? state.strategyChatMessages : [])
    .filter((m) => m && (m.role === "user" || m.role === "assistant"))
    .map((m) => ({ role: m.role, content: String(m.content ?? "") }));
}

function scrollStrategyChatToEnd() {
  const el = document.getElementById("scMessages");
  if (el) el.scrollTop = el.scrollHeight;
}

function renderStrategyChatMessages() {
  const el = document.getElementById("scMessages");
  const chips = document.getElementById("scEmptyChips");
  if (!el) return;
  const msgs = Array.isArray(state.strategyChatMessages) ? state.strategyChatMessages : [];
  el.innerHTML = "";
  if (!msgs.length) {
    const hint = document.createElement("div");
    hint.className = "chat-empty-hint";
    hint.textContent = "Describe the universe, date range, and any rule tweaks. Examples below.";
    el.appendChild(hint);
    if (chips) chips.classList.remove("hidden");
    return;
  }
  if (chips) chips.classList.add("hidden");
  msgs.forEach((m) => {
    const wrap = document.createElement("div");
    const role = m.role === "user" ? "user" : "assistant";
    wrap.className = `chat-bubble chat-bubble-${role}`;
    const roleEl = document.createElement("div");
    roleEl.className = "chat-bubble-role";
    roleEl.textContent = role === "user" ? "You" : "Assistant";
    wrap.appendChild(roleEl);
    const body = document.createElement("div");
    body.textContent = m.content != null ? String(m.content) : "";
    wrap.appendChild(body);
    if (role === "assistant" && Array.isArray(m.toolResults) && m.toolResults.length) {
      const det = document.createElement("details");
      det.className = "chat-tool-details";
      const sum = document.createElement("summary");
      sum.textContent = "Tool calls & raw results";
      det.appendChild(sum);
      const pre = document.createElement("pre");
      pre.className = "code-block";
      pre.textContent = prettyJson(m.toolResults);
      det.appendChild(pre);
      wrap.appendChild(det);
    }
    el.appendChild(wrap);
  });
  scrollStrategyChatToEnd();
}

function hideScQueueCallout() {
  const c = document.getElementById("scQueueCallout");
  if (c) {
    c.classList.add("hidden");
    c.innerHTML = "";
  }
}

function showScQueueCallout(taskId, runId) {
  const c = document.getElementById("scQueueCallout");
  if (!c || !taskId) return;
  const tid = safeText(taskId);
  const rid = runId ? safeText(runId) : "";
  c.classList.remove("hidden");
  c.innerHTML = `
    <strong>Backtest queued.</strong> It runs in the background (often a few minutes). Results appear in <strong>Recent runs</strong> below when finished.
    <div class="callout-actions">
      <code id="scTaskIdCopy">${tid}</code>
      <button type="button" class="btn small secondary" id="scCopyTaskBtn">Copy task id</button>
      <button type="button" class="btn small secondary" id="scSwitchFormTabBtn">Open form tab</button>
    </div>
    ${rid ? `<div class="muted" style="margin-top:8px;font-size:0.82rem">Run id: ${rid.slice(0, 12)}…</div>` : ""}
  `;
  document.getElementById("scCopyTaskBtn")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(tid);
      logEvent({ kind: "system", severity: "info", message: "Task id copied." });
    } catch {
      logEvent({ kind: "system", severity: "warn", message: "Could not copy task id." });
    }
  });
  document.getElementById("scSwitchFormTabBtn")?.addEventListener("click", () => switchBacktestHubTab("form"));
}

function switchBacktestHubTab(which) {
  const formTab = document.getElementById("btHubTabForm");
  const chatTab = document.getElementById("btHubTabChat");
  const formPanel = document.getElementById("btHubPanelForm");
  const chatPanel = document.getElementById("strategyChatPanel");
  const isForm = which === "form";
  if (formTab && chatTab) {
    formTab.classList.toggle("tab-btn-active", isForm);
    chatTab.classList.toggle("tab-btn-active", !isForm);
    formTab.setAttribute("aria-selected", isForm ? "true" : "false");
    chatTab.setAttribute("aria-selected", isForm ? "false" : "true");
  }
  if (formPanel) formPanel.classList.toggle("hidden", !isForm);
  if (chatPanel) chatPanel.classList.toggle("hidden", isForm);
  if (!isForm) scrollStrategyChatToEnd();
}

async function refreshBacktestRuns() {
  const list = document.getElementById("btRunList");
  const out = await api.get("/api/backtest-runs?limit=15");
  if (!list) return;
  if (!out.ok) {
    list.innerHTML = `<li class="muted">List failed: ${safeText(out.error)}</li>`;
    return;
  }
  const rows = Array.isArray(out.data) ? out.data : [];
  if (!rows.length) {
    list.innerHTML = `<li class="muted">No backtests yet.</li>`;
    return;
  }
  list.innerHTML = rows
    .map((r) => {
      const tid = r.celery_task_id ? `${safeText(r.celery_task_id).slice(0, 12)}…` : "—";
      const specLine = backtestSpecSummaryLine(r.spec);
      const sum = r.result_summary && typeof r.result_summary === "object" ? r.result_summary : null;
      let metrics = "";
      if (sum) {
        metrics = `<div class="bt-run-metrics">
          Trades ${safeText(sum.total_trades)} · Win ${formatPercentPoints(sum.win_rate_net, 1)} ·
          Return ${formatPercentPoints(sum.total_return_net_pct, 2)} · CAGR ${formatPercentPoints(sum.cagr_net_pct, 2)} ·
          Max DD ${formatPercentPoints(sum.max_drawdown_net_pct, 2)}
        </div>`;
        if (sum.findings_preview) {
          metrics += `<div class="muted" style="margin-top:4px;font-size:0.78rem">${escapeHtml(sum.findings_preview)}</div>`;
        }
      } else if (r.error_message) {
        metrics = `<div class="bt-run-metrics muted">${safeText(r.error_message)}</div>`;
      }
      return `<li class="bt-run-item"><strong>${safeText(r.status)}</strong> · task ${tid} · ${safeText(r.created_at)}
        <div class="bt-run-spec">${safeText(specLine)}</div>${metrics}</li>`;
    })
    .join("");
}

async function pollBacktestTask(taskId) {
  const t0 = Date.now();
  const pre = document.getElementById("btResult");
  setJobProgress("btJobProgress", "btJobProgressLabel", 0.08, "Queued…");
  for (let i = 0; i < 120; i++) {
    const elapsed = Math.floor((Date.now() - t0) / 1000);
    setBtMetaMessage(`Running… ${elapsed}s · waiting for worker`, { sticky: true });
    const st = await api.get(`/api/backtest-runs/tasks/${encodeURIComponent(taskId)}`, { timeoutMs: 120000 });
    if (!st.ok) {
      setBtMetaMessage(`Status poll failed: ${st.error}`, { sticky: true });
      return;
    }
    const d = st.data || {};
    const celery = safeText(d.celery_status || "").toLowerCase();
    setBtMetaMessage(`Running… ${elapsed}s · status: ${celery} · saved: ${safeText(d.db_status || "—")}`, { sticky: true });
    const progFrac =
      celery === "pending" || celery === "received"
        ? 0.15
        : celery === "started" || celery === "retry"
          ? 0.5 + Math.min(0.45, (i / 120) * 0.45)
          : 0.2;
    setJobProgress("btJobProgress", "btJobProgressLabel", progFrac, `${celery} · ${elapsed}s`);
    if (celery === "success" && d.result && pre) {
      renderBacktestResultSummary(d.result);
      renderBacktestResultRaw(d.result, "");
      setBtMetaMessage("Complete. Summary above; full JSON below.", { sticky: true });
      setJobProgress("btJobProgress", "btJobProgressLabel", 1, "Complete");
      await refreshBacktestRuns();
      return;
    }
    if (celery === "failure" || celery === "revoked") {
      renderBacktestResultSummary(null);
      renderBacktestResultRaw(null, prettyJson(d.task_result || d));
      setBtMetaMessage("Run finished with an error.", { sticky: true });
      setJobProgress("btJobProgress", "btJobProgressLabel", 0, "");
      await refreshBacktestRuns();
      return;
    }
    if (d.db_status === "failed" && d.error_message) {
      renderBacktestResultSummary(null);
      if (pre) pre.textContent = safeText(d.error_message);
      setBtMetaMessage(safeText(d.error_message), { sticky: true });
      setJobProgress("btJobProgress", "btJobProgressLabel", 0, "");
      await refreshBacktestRuns();
      return;
    }
    await new Promise((r) => setTimeout(r, 3000));
  }
  setBtMetaMessage("Still running; use Refresh list or check back later.", { sticky: true });
  setJobProgress("btJobProgress", "btJobProgressLabel", 0.9, "Still running…");
}

async function queueUserBacktest() {
  if (state.backtestQueueBusy) return;
  const pre = document.getElementById("btResult");
  const start = document.getElementById("btStart")?.value;
  const end = document.getElementById("btEnd")?.value;
  if (!start || !end) {
    setBtMetaMessage("Choose start and end dates.");
    return;
  }
  const spec = collectBacktestSpecFromForm();
  if (spec.universe_mode === "tickers" && (!spec.tickers || !spec.tickers.length)) {
    setBtMetaMessage("Add at least one ticker, or switch universe to saved watchlist.");
    return;
  }
  setBacktestQueueUiBusy(true);
  setBtMetaMessage("Queueing…", { sticky: true });
  try {
    const out = await api.post("/api/backtest-runs", { spec }, { timeoutMs: 120000 });
    if (!out.ok) {
      setBtMetaMessage(safeText(out.error), { sticky: true });
      logEvent({ kind: "system", severity: "error", message: `Backtest queue failed: ${out.error}` });
      return;
    }
    const taskId = out.data?.task_id;
    setBtMetaMessage(taskId ? `Queued. Tracking task ${safeText(taskId).slice(0, 14)}…` : "Queued.", { sticky: true });
    logEvent({ kind: "system", severity: "info", message: "Backtest queued." });
    if (taskId) await pollBacktestTask(taskId);
    else await refreshBacktestRuns();
  } finally {
    setBacktestQueueUiBusy(false);
  }
}

async function sendStrategyChat() {
  if (state.strategyChatBusy) return;
  const input = document.getElementById("scInput");
  const text = input?.value?.trim() || "";
  if (!text) return;
  if (!Array.isArray(state.strategyChatMessages)) state.strategyChatMessages = [];
  hideScQueueCallout();
  state.strategyChatMessages.push({ role: "user", content: text });
  input.value = "";
  renderStrategyChatMessages();
  state.strategyChatBusy = true;
  const sendBtn = document.getElementById("scSendBtn");
  if (sendBtn) sendBtn.disabled = true;
  try {
    const out = await api.post("/api/strategy-chat", { messages: strategyChatPayloadMessages() }, { timeoutMs: 180000 });
    if (!out.ok) {
      logEvent({ kind: "system", severity: "error", message: `Strategy chat: ${out.error}` });
      state.strategyChatMessages.push({ role: "assistant", content: `Error: ${out.error}` });
      renderStrategyChatMessages();
      return;
    }
    const assistant = out.data?.message || "";
    const tools = out.data?.tool_results;
    state.strategyChatMessages.push({
      role: "assistant",
      content: assistant || "(empty reply)",
      toolResults: Array.isArray(tools) && tools.length ? tools : null,
    });
    if (Array.isArray(tools)) {
      for (const t of tools) {
        if (t && t.tool === "queue_backtest" && t.result && t.result.task_id) {
          showScQueueCallout(t.result.task_id, t.result.run_id);
          break;
        }
      }
    }
    renderStrategyChatMessages();
    await refreshBacktestRuns();
  } finally {
    state.strategyChatBusy = false;
    if (sendBtn) sendBtn.disabled = false;
  }
}

async function refreshPortfolio() {
  const out = await api.get("/api/portfolio");
  const body = document.getElementById("portfolioBody");
  const meta = document.getElementById("portfolioMeta");
  body.innerHTML = "";
  if (!out.ok) {
    meta.textContent = "Portfolio unavailable.";
    body.innerHTML = `<tr><td colspan="5" class="muted">${safeText(out.error)}</td></tr>`;
    logEvent({ kind: "system", severity: "warn", message: `Portfolio load failed: ${out.error}` });
    return;
  }
  const data = out.data;
  meta.textContent = `${data.positions_count} position(s) • ${formatMoney(data.total_market_value)}`;
  if (!data.positions.length) {
    body.innerHTML = `
      <tr>
        <td colspan="5" class="muted">
          <div class="empty-state-cell">
            <svg class="empty-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M12 3v18M3 12h18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
              <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.5"/>
            </svg>
            <div>No open positions yet.</div>
            <button id="portfolioEmptyCtaBtn" class="btn small secondary" type="button">Run Scan to Begin</button>
          </div>
        </td>
      </tr>
    `;
    const cta = document.getElementById("portfolioEmptyCtaBtn");
    if (cta) cta.addEventListener("click", runScan);
    return;
  }
  data.positions.slice(0, 25).forEach((p) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${safeText(p.symbol)}</td>
      <td>${safeText(p.qty)}</td>
      <td>${formatMoney(p.last)}</td>
      <td>${formatMoney(p.market_value)}</td>
      <td>${safeNum(p.pl_pct) >= 0 ? "+" : ""}${safeText(p.pl_pct)}%</td>
    `;
    body.appendChild(tr);
  });
}

async function refreshSectors() {
  const out = await api.get("/api/sectors");
  const grid = document.getElementById("sectorGrid");
  grid.innerHTML = "";
  if (!out.ok) {
    grid.innerHTML = `<div class="muted">Sectors unavailable: ${safeText(out.error)}</div>`;
    logEvent({ kind: "system", severity: "warn", message: `Sector load failed: ${out.error}` });
    return;
  }
  const rows = out.data.rows || [];
  if (!rows.length) {
    grid.innerHTML = `<div class="muted">No sector data.</div>`;
    return;
  }
  const maxAbsVs = Math.max(1, ...rows.map((r) => Math.abs(safeNum(r.vs_spy, 0))));
  rows.forEach((row) => {
    const card = document.createElement("div");
    card.className = `sector-card ${row.winning ? "win" : "loss"}`;
    const vs = safeNum(row.vs_spy, 0);
    const barPct = Math.round((Math.abs(vs) / maxAbsVs) * 100);
    card.innerHTML = `
      <div class="${row.winning ? "sector-winning" : "sector-lagging"}"><strong>${safeText(row.etf)}</strong> ${safeText(row.name || "")}</div>
      <div class="${row.winning ? "sector-winning" : "sector-lagging"} mono-nums">${safeNum(row.return_pct).toFixed(2)}% vs SPY ${vs.toFixed(2)}%</div>
      <div class="sector-bar-track" aria-hidden="true" title="Relative strength vs SPY (within this grid)">
        <div class="sector-bar-fill ${row.winning ? "sector-bar-fill--win" : "sector-bar-fill--loss"}" style="width:${barPct}%"></div>
      </div>
      <div class="${row.winning ? "pill good" : "pill bad"}">${row.winning ? "Winning" : "Lagging"}</div>
    `;
    grid.appendChild(card);
  });
}

async function quickCheck() {
  const ticker = document.getElementById("tickerInput").value.trim().toUpperCase();
  if (!ticker) return;
  const outEl = document.getElementById("checkOutput");
  outEl.textContent = "Loading...";
  const out = await api.get(`/api/check/${ticker}`);
  if (!out.ok) {
    outEl.textContent = out.error;
    logEvent({ kind: "system", severity: "error", message: `Check ${ticker} failed: ${out.error}` });
    return;
  }
  outEl.textContent = JSON.stringify(out.data, null, 2);
  logEvent({ kind: "system", severity: "info", message: `Check complete for ${ticker}.` });
}

async function runReport() {
  const ticker = document.getElementById("reportTickerInput").value.trim().toUpperCase();
  if (!ticker) return;
  const section = document.getElementById("reportSection").value.trim();
  const skipMirofish = document.getElementById("skipMirofish").checked;
  const skipEdgar = document.getElementById("skipEdgar").checked;
  const btn = document.getElementById("reportBtn");
  const output = document.getElementById("reportOutput");
  const visual = document.getElementById("reportVisual");

  btn.disabled = true;
  btn.textContent = "Running...";
  output.textContent = "Generating report...";
  visual.innerHTML = `<div class="report-empty">Generating visual report...</div>`;
  updateActionCenter({ title: "Report Running", message: `Generating report for ${ticker}...`, severity: "info" });

  try {
    const qs = new URLSearchParams();
    if (section) qs.set("section", section);
    qs.set("skip_mirofish", String(skipMirofish));
    qs.set("skip_edgar", String(skipEdgar));
    const out = await api.get(`/api/report/${ticker}?${qs.toString()}`, { timeoutMs: 300000 });
    if (!out.ok) {
      output.textContent = out.error || "Report failed.";
      visual.innerHTML = `<div class="report-empty">${safeText(out.error || "Report failed.")}</div>`;
      logEvent({ kind: "report", severity: "error", message: `Report ${ticker} failed: ${out.error}` });
      return;
    }
    state.lastReportData = out.data;
    state.activeReportTab = "summary";
    output.textContent = JSON.stringify(out.data, null, 2);
    renderReportTabs(out.data);
    renderReportVisual(out.data);
    logEvent({ kind: "report", severity: "info", message: `Report complete for ${ticker}${section ? ` (${section})` : ""}.` });
    updateActionCenter({ title: "Report Complete", message: `Full report ready for ${ticker}.`, severity: "success" });
  } finally {
    btn.disabled = false;
    btn.textContent = "Run Report";
  }
}

async function runSecCompare() {
  const mode = document.getElementById("secCompareMode").value.trim();
  const tickerA = document.getElementById("secCompareTickerA").value.trim().toUpperCase();
  const tickerB = document.getElementById("secCompareTickerB").value.trim().toUpperCase();
  const formType = document.getElementById("secCompareFormType").value.trim().toUpperCase();
  const highlightChangesOnly = document.getElementById("secCompareChangesOnly")?.checked ? "true" : "false";
  const btn = document.getElementById("secCompareBtn");
  const meta = document.getElementById("secCompareMeta");

  if (!tickerA) return;
  if (mode === "ticker_vs_ticker" && !tickerB) return;

  btn.disabled = true;
  meta.textContent = "Running SEC compare...";
  renderSecCompareEmpty("Running SEC compare...");
  updateActionCenter({ title: "SEC Compare Running", message: "Comparing filing evidence. This can take a moment.", severity: "info" });
  try {
    const qs = new URLSearchParams();
    qs.set("mode", mode);
    qs.set("ticker", tickerA);
    qs.set("form_type", formType);
    qs.set("highlight_changes_only", highlightChangesOnly);
    if (mode === "ticker_vs_ticker") qs.set("ticker_b", tickerB);
    const out = await api.get(`/api/sec/compare?${qs.toString()}`, { timeoutMs: 300000 });
    let payload = out.ok ? out.data : null;
    if (!out.ok && (out.status === 404 || String(out.error || "").toLowerCase().includes("not found"))) {
      meta.textContent = "SEC compare endpoint not found; using metadata fallback.";
      const fallback = await buildFallbackSecCompare(mode, tickerA, tickerB, formType);
      if (!fallback.ok) {
        meta.textContent = `SEC compare failed: ${safeText(fallback.error)}`;
        renderSecCompareEmpty(safeText(fallback.error || "Compare failed."));
        logEvent({ kind: "report", severity: "error", message: `SEC compare fallback failed: ${fallback.error}` });
        return;
      }
      payload = fallback;
    } else if (!out.ok) {
      meta.textContent = `SEC compare failed: ${safeText(out.error)}`;
      renderSecCompareEmpty(safeText(out.error || "Compare failed."));
      logEvent({ kind: "report", severity: "error", message: `SEC compare failed: ${out.error}` });
      return;
    }
    state.secCompareResult = payload;
    meta.textContent = `SEC compare complete (${mode}, ${formType}).`;
    renderSecCompareVisual(payload);
    logEvent({ kind: "report", severity: "info", message: `SEC compare complete for ${tickerA}${tickerB ? ` vs ${tickerB}` : ""}.` });
    updateActionCenter({
      title: "SEC Compare Complete",
      message: `Compare finished for ${tickerA}${tickerB ? ` vs ${tickerB}` : ""}.`,
      severity: "success",
    });
  } finally {
    btn.disabled = false;
  }
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
  document.getElementById("scanBtn").addEventListener("click", runScan);
  document.getElementById("scanApplyBacktestSpecBtn")?.addEventListener("click", () => void fillScanOptionsFromLatestBacktest());
  document.getElementById("scanClearOptionsBtn")?.addEventListener("click", () => {
    const ta = document.getElementById("scanOptionsJson");
    if (ta) ta.value = "";
    state.scanRunOptions = null;
  });
  document.getElementById("refreshBtn").addEventListener("click", refreshAll);
  document.getElementById("onboardingStartBtn")?.addEventListener("click", startOnboarding);
  document.getElementById("onboardingConnectBtn")?.addEventListener("click", () => runOnboardingStep("connect"));
  document.getElementById("onboardingVerifyBtn")?.addEventListener("click", () => runOnboardingStep("verify_token_health"));
  document.getElementById("onboardingScanBtn")?.addEventListener("click", () => runOnboardingStep("test_scan"));
  document.getElementById("onboardingPaperBtn")?.addEventListener("click", () => runOnboardingStep("test_paper_order"));
  document.getElementById("onboardingSchwabBtn")?.addEventListener("click", async () => {
    if (!state.publicConfig?.schwab_oauth) {
      logEvent({ kind: "system", severity: "warn", message: "Schwab OAuth is not configured on this server." });
      return;
    }
    const out = await api.get("/api/oauth/schwab/authorize-url");
    if (!out.ok || !out.data?.url) {
      logEvent({ kind: "system", severity: "error", message: out.error || "Could not start Schwab OAuth." });
      return;
    }
    window.location.href = out.data.url;
  });
  document.getElementById("onboardingSchwabMarketBtn")?.addEventListener("click", async () => {
    if (!state.publicConfig?.schwab_market_oauth) {
      logEvent({
        kind: "system",
        severity: "warn",
        message: "Schwab market OAuth is not configured on this server.",
      });
      return;
    }
    const out = await api.get("/api/oauth/schwab/market/authorize-url");
    if (!out.ok || !out.data?.url) {
      logEvent({
        kind: "system",
        severity: "error",
        message: out.error || "Could not start Schwab market OAuth.",
      });
      return;
    }
    window.location.href = out.data.url;
  });
  document.getElementById("applyProfileBtn").addEventListener("click", applyProfile);
  document.getElementById("enableLiveTradingBtn")?.addEventListener("click", () => void submitEnableLiveTrading());
  document.getElementById("saveTradingHaltBtn")?.addEventListener("click", () => void submitTradingHaltSave());
  document.getElementById("calibrationRefreshBtn")?.addEventListener("click", () => void refreshCalibration());
  document.getElementById("settingsModeSelect").addEventListener("change", loadProfiles);
  document.getElementById("profileSelect")?.addEventListener("change", renderPresetApplyPreview);
  document.getElementById("automationOptIn")?.addEventListener("change", renderPresetApplyPreview);
  document.getElementById("decisionBtn").addEventListener("click", loadDecisionCard);
  document.getElementById("recoveryBtn").addEventListener("click", mapRecovery);
  document.getElementById("performanceRefreshBtn").addEventListener("click", refreshPerformance);
  document.getElementById("closeQuickViewBtn").addEventListener("click", () => {
    document.getElementById("quickViewPanel").classList.remove("open");
  });
  document.getElementById("activityDrawerToggle").addEventListener("click", () => {
    const body = document.getElementById("activityDrawerBody");
    const toggle = document.getElementById("activityDrawerToggle");
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", expanded ? "false" : "true");
    body.classList.toggle("open", !expanded);
  });
  document.getElementById("checkBtn").addEventListener("click", quickCheck);
  document.getElementById("reportBtn").addEventListener("click", runReport);
  document.getElementById("secCompareBtn").addEventListener("click", runSecCompare);
  document.getElementById("secCompareMode").addEventListener("change", applySecCompareMode);
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
  document.getElementById("toggleReportViewBtn").addEventListener("click", () => {
    state.reportRawView = !state.reportRawView;
    applyReportViewMode();
  });
  document.getElementById("pendingFilter").addEventListener("change", refreshPending);
  document.getElementById("pendingSort").addEventListener("change", refreshPending);
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

  const dialog = document.getElementById("approveDialog");
  document.getElementById("confirmApproveBtn").addEventListener("click", async (e) => {
    e.preventDefault();
    const id = state.approvingTradeId;
    if (!id) {
      dialog.close();
      return;
    }
    document.getElementById("confirmApproveBtn").disabled = true;
    await approveTradeById(id);
    document.getElementById("confirmApproveBtn").disabled = false;
    state.approvingTradeId = null;
    dialog.close();
  });
  document.getElementById("cancelApproveBtn").addEventListener("click", () => {
    state.approvingTradeId = null;
    dialog.close();
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

  window.addEventListener("hashchange", handleRouteHash);

  document.getElementById("displayModeSelect")?.addEventListener("change", (e) => {
    const v = e.target.value;
    applyDisplayMode(v);
    if (v === "pro" && state.performance) {
      const panel = document.getElementById("performancePanel");
      if (panel) renderPerformancePanel(panel, state.performance);
    }
  });
}

(async () => {
  wireEvents();
  applyDisplayMode(getDisplayMode());
  applyReportViewMode();
  applySecCompareMode();
  await loadConfig();
  await authSessionReady;
  const token = await getApiAccessToken();
  if (token) {
    await refreshCritical();
    markDeferredDataPlaceholders();
    setupLazySectionLoading();
  } else if (state.config?.auth_mode === "supabase") {
    updateActionCenter({
      title: "Sign in",
      message: "Sign in with Supabase to load portfolio, pending trades, and billing-protected actions.",
      severity: "warn",
    });
    setupLazySectionLoading();
  } else {
    await refreshAll();
    setupLazySectionLoading();
  }
  applyQuerySectionDeepLink();
  handleRouteHash();
  logEvent({ kind: "system", severity: "info", message: "Dashboard loaded." });
})();

