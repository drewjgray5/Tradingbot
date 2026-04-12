/**
 * Minimal scan UI for external users: auth, status, scan, blockers, small results table.
 * Works with local webapp (threaded scan + /api/scan/status) and SaaS (Celery + /api/scan/{id}).
 */

const AUTH_TOKEN_KEY = "tradingbot.jwt";
const LEGACY_AUTH_TOKEN_KEYS = ["supabasetoken", "supabaseToken", "supabase_token"];
const SUPABASE_ESM = "https://esm.sh/@supabase/supabase-js@2.49.1";

const state = {
  publicConfig: { supabase: null, saas_mode: false },
  config: { auth_mode: "jwt" },
  latestSignals: [],
};

let supabaseClient = null;

function jsonHeaders(extra = {}) {
  return { "Content-Type": "application/json", ...extra };
}

async function applyCookieSessionToken(token) {
  const clean = String(token || "").trim();
  if (!clean) return;
  try {
    await fetch("/api/auth/session", {
      method: "POST",
      credentials: "include",
      headers: jsonHeaders({ Accept: "application/json" }),
      body: JSON.stringify({ access_token: clean }),
    });
  } catch (err) {
    console.warn("simple auth/session set failed", err);
  }
}

async function clearCookieSession() {
  try {
    await fetch("/api/auth/session", {
      method: "DELETE",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
  } catch (err) {
    console.warn("simple auth/session clear failed", err);
  }
}

function safeText(value) {
  if (value === null || value === undefined) return "—";
  return String(value);
}

function safeNum(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function setMessage(text, kind = "muted") {
  const el = document.getElementById("simpleMessage");
  if (!el) return;
  el.textContent = text || "";
  el.classList.remove("warn", "error", "ok");
  if (kind === "warn") el.classList.add("warn");
  else if (kind === "error") el.classList.add("error");
  else if (kind === "ok") el.classList.add("ok");
}

function setProgress(fraction, label) {
  const bar = document.getElementById("simpleProgress");
  const lbl = document.getElementById("simpleProgressLabel");
  const wrap = document.getElementById("simpleProgressWrap");
  const pct = Math.max(0, Math.min(100, Math.round((fraction || 0) * 100)));
  if (bar && bar.tagName === "PROGRESS") bar.value = pct;
  if (lbl) lbl.textContent = label || "";
  if (wrap) wrap.classList.toggle("hidden", pct <= 0 && !label);
}

const DIAG_LABELS = {
  watchlist_size: "Watchlist symbols",
  stage2_fail: "Failed Stage 2 trend",
  vcp_fail: "Failed VCP / consolidation",
  breakout_not_confirmed: "Breakout not confirmed",
  sector_not_winning: "Sector not winning vs peers",
  too_few_candles: "Insufficient price history",
  df_empty: "No price dataframe",
  exceptions: "Processing exceptions",
  scan_blocked: "Scan blocked by risk gate",
  quality_gates_filtered: "Quality gates removed",
  self_study_filtered: "Self-study filter",
};

function diagnosticsHeadline(diagOrSummary) {
  if (!diagOrSummary || typeof diagOrSummary !== "object") return "";
  const headline = safeText(diagOrSummary.headline || "").trim();
  if (headline && headline !== "—") return headline;
  const dq = safeText(diagOrSummary.data_quality || "").trim().toLowerCase();
  if (dq && dq !== "ok") {
    const rs = Array.isArray(diagOrSummary.data_quality_reasons) ? diagOrSummary.data_quality_reasons : [];
    const rtxt = rs
      .slice(0, 2)
      .map((x) => safeText(x))
      .filter(Boolean)
      .join("; ");
    return rtxt ? `Data quality: ${dq} — ${rtxt}.` : `Data quality: ${dq}.`;
  }
  if (safeNum(diagOrSummary.scan_blocked, 0) > 0) {
    const reason = safeText(diagOrSummary.scan_blocked_reason || "").trim();
    if (reason === "bear_regime_spy_below_200sma") return "Scan blocked: SPY below 200-day average.";
    return "Scan blocked by an active risk gate.";
  }
  return "";
}

function buildScanMeta(signals, count) {
  const total = count ?? signals.length;
  return `Found ${total} signal(s).`;
}

function formatStrategySummary(summary) {
  if (!summary || typeof summary !== "object") return "";
  const dominant = safeText(summary.dominant_live_strategy || "");
  const total = safeNum(summary.total_ranked, 0);
  const count = safeNum(summary.dominant_count, 0);
  if (!dominant || dominant === "—" || total <= 0 || count <= 0) return "";
  return ` Dominant strategy: ${dominant} (${count}/${total}).`;
}

function renderBlockers(diagnostics, signalCount) {
  const ul = document.getElementById("simpleBlockers");
  if (!ul) return;
  ul.innerHTML = "";
  const diag = diagnostics && typeof diagnostics === "object" ? diagnostics : {};
  const watch = safeNum(diag.watchlist_size, 0);
  const li0 = document.createElement("li");
  li0.innerHTML = `<strong>Watchlist</strong>: ${watch} symbol(s) scanned.`;
  ul.appendChild(li0);

  const blockers = Object.entries(diag)
    .filter(([k, v]) => safeNum(v, 0) > 0 && k !== "watchlist_size")
    .map(([k, v]) => ({
      key: k,
      label: DIAG_LABELS[k] || k.replaceAll("_", " "),
      value: safeNum(v, 0),
    }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 8);

  if (!blockers.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent =
      signalCount > 0
        ? "No major rejection counters reported; see table below."
        : "No rejection counters yet — if the watchlist is non-zero, tighten or loosen strategy presets on the full dashboard.";
    ul.appendChild(li);
    return;
  }

  blockers.forEach((b) => {
    const li = document.createElement("li");
    li.innerHTML = `${b.label}: <strong>${b.value}</strong>`;
    ul.appendChild(li);
  });
}

function renderTable(signals) {
  const body = document.getElementById("simpleTableBody");
  if (!body) return;
  body.innerHTML = "";
  if (!signals.length) {
    body.innerHTML = `<tr><td colspan="3" class="muted">No candidates passed the current rules.</td></tr>`;
    return;
  }
  signals.forEach((sig) => {
    const tr = document.createElement("tr");
    const ticker = sig.ticker || sig.symbol || "?";
    const score = sig.signal_score ?? sig.score;
    const scoreText = score != null && Number.isFinite(Number(score)) ? Number(score).toFixed(1) : "—";
    tr.innerHTML = `
      <td><strong>${safeText(ticker)}</strong></td>
      <td>${scoreText}</td>
      <td>${safeText(sig.sector_etf)}</td>
    `;
    body.appendChild(tr);
  });
}

async function getApiAccessToken() {
  const manual = document.getElementById("simpleJwt")?.value?.trim() || "";
  if (manual) return manual;
  if (state.config?.auth_mode === "supabase" && supabaseClient) {
    const { data, error } = await supabaseClient.auth.getSession();
    if (error) console.warn("getSession", error);
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
        data = { ok: false, error: `Invalid JSON (${res.status})` };
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
      if (err?.name === "AbortError") return { ok: false, error: "Request timed out." };
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
};

function persistJwt(session) {
  if (session?.access_token) {
    const token = String(session.access_token).trim();
    if (!token) return;
    localStorage.setItem(AUTH_TOKEN_KEY, token);
    clearLegacyApiJwtKeys();
    void applyCookieSessionToken(token);
    const inp = document.getElementById("simpleJwt");
    if (inp) inp.value = "";
  }
}

function updateSbUi(session) {
  const out = document.getElementById("simpleSbOut");
  const inn = document.getElementById("simpleSbIn");
  const label = document.getElementById("simpleSbLabel");
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

async function initSupabase(url, anonKey) {
  let createClient;
  try {
    const mod = await import(SUPABASE_ESM);
    createClient = mod.createClient;
  } catch (e) {
    console.warn(e);
    setMessage("Could not load Supabase from CDN; paste a JWT below.", "warn");
    return;
  }
  supabaseClient = createClient(url, anonKey, {
    auth: { autoRefreshToken: true, persistSession: true, detectSessionInUrl: true },
  });
  const {
    data: { session },
  } = await supabaseClient.auth.getSession();
  persistJwt(session);
  updateSbUi(session);
  supabaseClient.auth.onAuthStateChange((_e, next) => {
    persistJwt(next);
    updateSbUi(next);
  });
  document.getElementById("simpleSbSignIn")?.addEventListener("click", async () => {
    const email = document.getElementById("simpleSbEmail")?.value?.trim() || "";
    const password = document.getElementById("simpleSbPass")?.value || "";
    if (!email || !password) {
      setMessage("Enter email and password.", "warn");
      return;
    }
    const { error } = await supabaseClient.auth.signInWithPassword({ email, password });
    if (error) setMessage(error.message, "error");
    else setMessage("Signed in.", "ok");
  });
  document.getElementById("simpleSbSignUp")?.addEventListener("click", async () => {
    const email = document.getElementById("simpleSbEmail")?.value?.trim() || "";
    const password = document.getElementById("simpleSbPass")?.value || "";
    if (!email || !password) {
      setMessage("Enter email and password to sign up.", "warn");
      return;
    }
    const { error } = await supabaseClient.auth.signUp({ email, password });
    if (error) setMessage(error.message, "error");
    else setMessage("Check email if confirmation is required, then sign in.", "ok");
  });
  document.getElementById("simpleSbSignOut")?.addEventListener("click", async () => {
    await supabaseClient.auth.signOut();
    await clearCookieSession();
    clearStoredApiJwt();
    const inp = document.getElementById("simpleJwt");
    if (inp) inp.value = "";
    setMessage("Signed out.", "ok");
  });
}

async function loadPublicConfig() {
  const res = await fetch("/api/public-config", { headers: { Accept: "application/json" } });
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    /* ignore */
  }
  const payload = data?.data && typeof data.data === "object" ? data.data : data;
  state.publicConfig = {
    supabase: payload?.supabase || null,
    saas_mode: Boolean(payload?.saas_mode),
  };
  const sb = state.publicConfig.supabase;
  const sbWrap = document.getElementById("simpleSupabase");
  if (sb?.url && sb?.anon_key) {
    state.config = { auth_mode: "supabase" };
    if (sbWrap) sbWrap.classList.remove("hidden");
    await initSupabase(sb.url, sb.anon_key);
  } else {
    state.config = { auth_mode: "jwt" };
    if (sbWrap) sbWrap.classList.add("hidden");
  }
}

async function hydrateSimpleScanFromStatus(s) {
  const last = s.last_scan;
  if (!last || !last.at) return;
  const headline = document.getElementById("simpleScanHeadline");
  const diag = last.diagnostics || last.diagnostics_summary || {};

  if (state.publicConfig.saas_mode) {
    const jobId = safeText(last.job_id || "").trim();
    const foundRaw = last.signals_found;
    const foundN = foundRaw === null || foundRaw === undefined ? null : safeNum(foundRaw, 0);
    if (jobId && foundN === 0) {
      state.latestSignals = [];
      const hl = diagnosticsHeadline(diag) || buildScanMeta([], 0);
      if (headline) headline.textContent = hl + formatStrategySummary(last.strategy_summary);
      renderBlockers(diag, 0);
      renderTable([]);
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
    const hl = diagnosticsHeadline(diag) || buildScanMeta(signals, last.signals_found ?? signals.length);
    if (headline) headline.textContent = hl + formatStrategySummary(last.strategy_summary);
    renderBlockers(diag, safeNum(last.signals_found, signals.length));
    renderTable(signals);
    return;
  }

  const localSignals = Array.isArray(last.signals) ? last.signals : [];
  state.latestSignals = localSignals;
  const hl = diagnosticsHeadline(diag) || buildScanMeta(localSignals, last.signals_found);
  if (headline) headline.textContent = hl + formatStrategySummary(last.strategy_summary);
  renderBlockers(diag, safeNum(last.signals_found, localSignals.length));
  renderTable(localSignals);
}

async function refreshStatus() {
  const statusRes = await api.get("/api/status");
  if (!statusRes.ok) {
    setMessage(`Status failed: ${statusRes.error}`, "error");
    return;
  }
  const s = statusRes.data || {};
  document.getElementById("simpleMarket").textContent = safeText(s.market_state || (s.market_token_ok ? "Connected" : "Disconnected"));
  document.getElementById("simpleAccount").textContent = safeText(s.account_state || (s.account_token_ok ? "Connected" : "Disconnected"));

  const deep = await api.get("/api/health/deep", { timeoutMs: 25000 });
  const quoteOk = deep.ok && deep.data?.quote_ok;
  document.getElementById("simpleQuotes").textContent = quoteOk ? "OK" : deep.ok ? "Degraded" : "Unknown";

  const last = s.last_scan;
  const lastEl = document.getElementById("simpleLastScan");
  if (last?.at) {
    const ts = new Date(last.at);
    const when = Number.isNaN(ts.getTime()) ? "recent" : ts.toLocaleString();
    lastEl.textContent = `${safeNum(last.signals_found, 0)} signals · ${when}`;
  } else {
    lastEl.textContent = "Never";
  }

  try {
    await hydrateSimpleScanFromStatus(s);
  } catch (e) {
    console.warn("hydrateSimpleScanFromStatus", e);
  }
}

async function waitSaaS(taskId) {
  const headline = document.getElementById("simpleScanHeadline");
  setProgress(0.05, "Queued…");
  for (let i = 0; i < 180; i++) {
    const status = await api.get(`/api/scan/${encodeURIComponent(taskId)}`);
    if (!status.ok) {
      headline.textContent = "Scan failed.";
      setMessage(status.error, "error");
      setProgress(0, "");
      return;
    }
    const data = status.data || {};
    const celery = safeText(data.status || "").toLowerCase();
    if (celery === "pending" || celery === "received") {
      headline.textContent = "Scan queued…";
      setProgress(0.12, "Waiting for worker");
      await new Promise((r) => setTimeout(r, 2000));
      continue;
    }
    if (celery === "started" || celery === "retry") {
      headline.textContent = "Scan running…";
      setProgress(0.55, "Running");
      await new Promise((r) => setTimeout(r, 3000));
      continue;
    }
    if (celery === "success") {
      const result = data.result;
      if (!result || typeof result !== "object" || result.ok === false) {
        headline.textContent = "Scan failed.";
        setMessage(safeText(result?.error || "Invalid result"), "error");
        setProgress(0, "");
        return;
      }
      const jobId = result.job_id;
      let listOut;
      if (jobId) listOut = await api.get(`/api/scan-results?limit=500&job_id=${encodeURIComponent(jobId)}`);
      else listOut = { ok: false, error: "Missing job_id" };
      if (!listOut.ok) {
        headline.textContent = "Could not load results.";
        setMessage(listOut.error, "error");
        setProgress(0, "");
        return;
      }
      const rows = Array.isArray(listOut.data) ? listOut.data : [];
      const signals = rows.map((r) => r.payload).filter((p) => p && typeof p === "object");
      state.latestSignals = signals;
      const diag = result.diagnostics || {};
      const n = safeNum(result.signals_found, signals.length);
      const hl = diagnosticsHeadline(diag) || buildScanMeta(signals, n);
      headline.textContent = hl + formatStrategySummary(result.strategy_summary);
      renderBlockers(diag, n);
      renderTable(signals);
      setMessage(`Scan complete: ${n} signal(s).`, n > 0 ? "ok" : "muted");
      setProgress(1, "Complete");
      await refreshStatus();
      return;
    }
    if (celery === "failure" || celery === "revoked") {
      headline.textContent = "Scan failed.";
      const res = data.result;
      const msg =
        typeof res === "string"
          ? res
          : res && typeof res === "object"
            ? safeText(res.error || res.message || JSON.stringify(res))
            : "Task failed";
      setMessage(msg, "error");
      setProgress(0, "");
      return;
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  setMessage("Scan still running — open the full dashboard or retry later.", "warn");
}

function applyLocalScanPayload(data) {
  const headline = document.getElementById("simpleScanHeadline");
  const signals = data.signals || [];
  state.latestSignals = signals;
  const diag = data.diagnostics || data.diagnostics_summary || {};
  const n = safeNum(data.signals_found, signals.length);
  const hl =
    diagnosticsHeadline(data.diagnostics_summary || data.diagnostics || {}) || buildScanMeta(signals, n);
  headline.textContent = hl + formatStrategySummary(data.strategy_summary);
  renderBlockers(diag, n);
  renderTable(signals);
  setMessage(`Scan complete: ${n} signal(s).`, n > 0 ? "ok" : "muted");
}

async function waitLocal() {
  const headline = document.getElementById("simpleScanHeadline");
  setProgress(0.1, "Starting…");
  for (let i = 0; i < 180; i++) {
    const status = await api.get("/api/scan/status");
    if (!status.ok) {
      headline.textContent = "Scan failed.";
      setMessage(status.error, "error");
      setProgress(0, "");
      return;
    }
    const data = status.data || {};
    if (data.status === "failed") {
      headline.textContent = "Scan failed.";
      setMessage(safeText(data.error || "Scan worker error"), "error");
      setProgress(0, "");
      return;
    }
    if (data.status === "running") {
      const elapsed = data.elapsed_seconds ?? (data.started_at ? Math.max(0, Math.floor((Date.now() - Date.parse(data.started_at)) / 1000)) : null);
      headline.textContent = elapsed != null ? `Scan running… ${elapsed}s` : "Scan running…";
      setProgress(0.5, "Running");
      await new Promise((r) => setTimeout(r, 5000));
      continue;
    }
    if (data.status === "completed") {
      applyLocalScanPayload(data);
      setProgress(1, "Complete");
      await refreshStatus();
      return;
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  setMessage("Timed out waiting for local scan.", "warn");
}

async function runScan() {
  const btn = document.getElementById("simpleScanBtn");
  const headline = document.getElementById("simpleScanHeadline");
  btn.disabled = true;
  btn.textContent = "Scanning…";
  setMessage("");
  setProgress(0, "");
  headline.textContent = "Starting scan…";
  try {
    const out = await api.post("/api/scan?async_mode=true", {});
    if (!out.ok) {
      headline.textContent = "Scan failed.";
      setMessage(out.error, "error");
      return;
    }
    const d = out.data || {};
    if (d.task_id) {
      await waitSaaS(d.task_id);
      return;
    }
    if (d.status === "running") {
      await waitLocal();
      return;
    }
    if (d.status === "completed") {
      applyLocalScanPayload(d);
      setProgress(1, "Complete");
      await refreshStatus();
      return;
    }
    if (d.status === "failed") {
      headline.textContent = "Scan failed.";
      setMessage(safeText(d.error || "Scan failed"), "error");
      return;
    }
    if (Array.isArray(d.signals)) {
      state.latestSignals = d.signals;
      const diag = d.diagnostics || d.diagnostics_summary || {};
      const n = safeNum(d.signals_found, d.signals.length);
      const hl = diagnosticsHeadline(d.diagnostics_summary || d.diagnostics || {}) || buildScanMeta(d.signals, n);
      headline.textContent = hl + formatStrategySummary(d.strategy_summary);
      renderBlockers(diag, n);
      renderTable(d.signals);
      setMessage(`Scan complete: ${n} signal(s).`, n > 0 ? "ok" : "muted");
      await refreshStatus();
      return;
    }
    headline.textContent = "Unexpected response.";
    setMessage("Try the full dashboard or check API version.", "warn");
  } finally {
    btn.disabled = false;
    btn.textContent = "Run scan";
  }
}

function wireJwt() {
  const saved = readStoredApiJwt();
  const inp = document.getElementById("simpleJwt");
  if (inp && saved && !inp.value) inp.placeholder = "Token saved in browser";
  document.getElementById("simpleJwtSave")?.addEventListener("click", () => {
    const v = document.getElementById("simpleJwt")?.value?.trim() || "";
    if (v) {
      localStorage.setItem(AUTH_TOKEN_KEY, v);
      clearLegacyApiJwtKeys();
      void applyCookieSessionToken(v);
    } else {
      clearStoredApiJwt();
      void clearCookieSession();
    }
    setMessage(v ? "Token saved." : "Cleared — enter a token to save.", v ? "ok" : "warn");
  });
}

async function main() {
  wireJwt();
  await loadPublicConfig();
  await refreshStatus();
  document.getElementById("simpleRefreshStatus")?.addEventListener("click", () => refreshStatus());
  document.getElementById("simpleScanBtn")?.addEventListener("click", () => runScan());
}

main().catch((e) => setMessage(String(e?.message || e), "error"));
