const state = {
  latestSignals: [],
  approvingTradeId: null,
  approvingChecklist: null,
  pendingFilter: "all",
  pendingSort: "newest",
  config: { auth_mode: "jwt" },
  reportRawView: false,
  lastReportData: null,
  activeReportTab: "summary",
  secCompareResult: null,
  onboarding: null,
  profile: null,
  performance: null,
};

const AUTH_TOKEN_KEY = "tradingbot.jwt";

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

    const tokenInput = document.getElementById("jwtInput")?.value?.trim();
    const tokenStored = localStorage.getItem(AUTH_TOKEN_KEY) || "";
    const token = tokenInput || tokenStored;
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
};

function safeText(value) {
  if (value === null || value === undefined) return "—";
  return String(value);
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
  funnelPairs.forEach(([label, value]) => {
    const node = document.createElement("div");
    node.className = "funnel-node";
    node.innerHTML = `<div class="label">${label}</div><div class="value">${value}</div>`;
    funnelEl.appendChild(node);
  });

  Object.entries(diag).slice(0, 8).forEach(([key, value]) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${DIAG_LABELS[key] || key}: ${value}`;
    chipWrap.appendChild(chip);
  });
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
      <td><button class="btn small secondary" data-idx="${idx}">Queue</button></td>
    `;
    body.appendChild(tr);
  });

  body.querySelectorAll("button[data-idx]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const clicked = e.currentTarget;
      clicked.disabled = true;
      const idx = Number(clicked.getAttribute("data-idx"));
      const sig = state.latestSignals[idx];
      const payload = {
        ticker: sig.ticker || sig.symbol,
        price: sig.price ?? sig.current_price ?? null,
        signal: sig,
        note: "Queued from scan table",
      };
      const out = await api.post("/api/pending-trades", payload);
      if (!out.ok) {
        logEvent({ kind: "trade", severity: "error", message: `Queue failed: ${out.error}` });
        clicked.disabled = false;
        return;
      }
      logEvent({ kind: "trade", severity: "info", message: `Queued ${payload.ticker} (${out.data.id})` });
      await refreshPending();
      clicked.disabled = false;
    });
  });
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
    checklistText = `<br/><strong>Pre-trade checklist:</strong><br/>
      risk %: ${safeText(c.risk_percent_estimate)} | max/day: ${safeText(c.max_daily_trades)} | live today: ${safeText(c.live_trades_today)}<br/>
      event risk flagged: ${safeText(c?.event_risk?.flagged || false)} | regime mode: ${safeText(c?.regime_status?.mode || "off")}<br/>
      blocked: ${safeText(c.blocked)} ${Array.isArray(c.block_reasons) && c.block_reasons.length ? `(${c.block_reasons.join(", ")})` : ""}`;
  } else {
    checklistText = `<br/><span class="muted">Checklist unavailable: ${safeText(preflight.error)}</span>`;
  }
  summary.innerHTML = `
    Approve BUY ${row.qty} ${row.ticker} @ ${row.price ? formatMoney(row.price) : "market"}?<br/>
    Est. value: <strong>${formatMoney(est)}</strong><br/>
    <span class="muted">${riskHint}</span>
    ${checklistText}
  `;
  state.approvingTradeId = row.id;
  dialog.showModal();
}

async function loadConfig() {
  const tokenInput = document.getElementById("jwtInput");
  const saveBtn = document.getElementById("saveJwtBtn");
  if (tokenInput) {
    tokenInput.value = localStorage.getItem(AUTH_TOKEN_KEY) || "";
  }
  if (saveBtn) {
    saveBtn.addEventListener("click", () => {
      const val = tokenInput?.value?.trim() || "";
      if (val) {
        localStorage.setItem(AUTH_TOKEN_KEY, val);
        logEvent({ kind: "system", severity: "info", message: "JWT token saved locally." });
      } else {
        localStorage.removeItem(AUTH_TOKEN_KEY);
        logEvent({ kind: "system", severity: "warn", message: "JWT token cleared." });
      }
    });
  }
  state.config = { auth_mode: "jwt" };
  updateActionCenter({
    title: "Authentication Required",
    message: "Paste a valid Supabase JWT and click Save Token to access protected APIs.",
    severity: "warn",
  });
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
    const metrics = deepRes.data.metrics || {};
    const req = safeNum(metrics.requests_total, 0);
    const err = safeNum(metrics.errors_total, 0);
    const rate = req > 0 ? `${((err / req) * 100).toFixed(1)}%` : "0.0%";
    errEl.className = statusClass(err > 0 ? "warn" : "info");
    errEl.textContent = `${rate} (${err}/${req})`;
  } else {
    setStatusPill(quoteEl, "Unknown");
    errEl.className = "pill neutral";
    errEl.textContent = "--";
  }

  const authOk = Boolean(status.market_token_ok && status.account_token_ok);
  const quoteOk = Boolean(deepRes.ok && deepRes.data?.quote_ok);
  const req = safeNum(deepRes?.data?.metrics?.requests_total, 0);
  const err = safeNum(deepRes?.data?.metrics?.errors_total, 0);
  const errRate = req > 0 ? (err / req) * 100 : 0;

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
}

async function runScan() {
  const btn = document.getElementById("scanBtn");
  btn.disabled = true;
  btn.textContent = "Scanning...";
  setLoading({ scan: "Scanning market candidates..." });
  updateActionCenter({ title: "Scan Running", message: "Market scan is running. Results will stream into this page.", severity: "info" });
  try {
    const out = await api.post("/api/scan?async_mode=true", {});
    if (!out.ok) {
      document.getElementById("scanMeta").textContent = "Scan failed.";
      updateTopStrategyChip(null);
      logEvent({ kind: "scan", severity: "error", message: out.error });
      return;
    }
    if (out.data?.status === "running") {
      logEvent({
        kind: "scan",
        severity: "info",
        message: out.data?.started ? "Scan started in background." : "Scan already running; monitoring progress.",
      });
      await waitForScanCompletion();
      await refreshStatus();
      return;
    }
    if (out.data?.signals) {
      state.latestSignals = out.data.signals || [];
      const headline = diagnosticsHeadline(out.data.diagnostics_summary || out.data.diagnostics || {});
      document.getElementById("scanMeta").textContent =
        (headline || buildScanMeta(state.latestSignals, out.data.signals_found))
        + formatStrategySummary(out.data.strategy_summary);
      updateTopStrategyChip(out.data.strategy_summary);
      renderDiagnostics(out.data.diagnostics || out.data.diagnostics_summary || {});
      renderScanRows(state.latestSignals);
      logEvent({ kind: "scan", severity: "info", message: `Scan complete: ${out.data.signals_found} signal(s).` });
      updateActionCenter({
        title: "Scan Complete",
        message: `Found ${out.data.signals_found} signal(s). Review queue candidates in Scan Results.`,
        severity: "success",
      });
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Run Scan";
  }
}

async function waitForScanCompletion() {
  const maxPolls = 180;
  for (let i = 0; i < maxPolls; i++) {
    const status = await api.get("/api/scan/status");
    if (!status.ok) {
      logEvent({ kind: "scan", severity: "error", message: `Scan status failed: ${status.error}` });
      return;
    }
    const data = status.data || {};
    if (data.status === "running") {
      updateTopStrategyChip(null);
      const elapsed = data.elapsed_seconds ?? (
        data.started_at ? Math.max(0, Math.floor((Date.now() - Date.parse(data.started_at)) / 1000)) : null
      );
      document.getElementById("scanMeta").textContent = elapsed !== null ? `Scan running... ${elapsed}s elapsed` : "Scan running...";
      await new Promise((r) => setTimeout(r, 5000));
      continue;
    }
    if (data.status === "completed") {
      state.latestSignals = data.signals || [];
      const headline = diagnosticsHeadline(data.diagnostics_summary || data.diagnostics || {});
      document.getElementById("scanMeta").textContent =
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
      document.getElementById("scanMeta").textContent = "Scan failed.";
      updateTopStrategyChip(null);
      logEvent({ kind: "scan", severity: "error", message: data.error || "unknown error" });
      return;
    }
    if (data.status === "idle" && data.last_scan) {
      document.getElementById("scanMeta").textContent = `Last scan: ${data.last_scan.signals_found ?? 0} signal(s).`;
      updateTopStrategyChip(data.last_scan.strategy_summary || null);
      return;
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  document.getElementById("scanMeta").textContent = "Scan still running. Use Refresh to check progress.";
  updateTopStrategyChip(null);
  logEvent({ kind: "scan", severity: "warn", message: "Scan still running in background; polling window ended." });
}

async function refreshPending() {
  const filter = document.getElementById("pendingFilter")?.value || state.pendingFilter;
  const sort = document.getElementById("pendingSort")?.value || state.pendingSort;
  state.pendingFilter = filter;
  state.pendingSort = sort;
  const query = new URLSearchParams({ status: filter, sort });
  const out = await api.get(`/api/pending-trades?${query.toString()}`);
  if (!out.ok) {
    logEvent({ kind: "trade", severity: "error", message: `Pending trades load failed: ${out.error}` });
    return;
  }
  const rows = out.data || [];
  document.getElementById("pendingCount").textContent = String(rows.filter((r) => r.status === "pending").length);

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
          <button class="btn small approve-btn" data-approve="${row.id}" ${row.status !== "pending" ? "disabled" : ""}>Approve</button>
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
}

async function approveTradeById(id) {
  const out = await api.post(`/api/trades/${id}/approve?confirm_live=true`, {});
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
    section.style.display = out.data?.onboarding_required ? "block" : "none";
  }
  if (!out.data?.onboarding_required) {
    meta.textContent = "Onboarding complete: Schwab account linked.";
    output.textContent = prettyJson(out.data);
    return;
  }
  const elapsed = out.data?.elapsed_minutes;
  const done = out.data?.completed_under_target;
  meta.textContent = `Elapsed: ${elapsed ?? "n/a"} min | target <= 20 min | ${done ? "PASS" : "IN PROGRESS"}`;
  output.textContent = prettyJson(out.data);
}

async function startOnboarding() {
  const out = await api.post("/api/onboarding/start", {});
  if (!out.ok) {
    logEvent({ kind: "system", severity: "error", message: `Onboarding start failed: ${out.error}` });
    return;
  }
  logEvent({ kind: "system", severity: "info", message: "Onboarding wizard started." });
  await refreshOnboarding();
}

async function runOnboardingStep(step) {
  const out = await api.post(`/api/onboarding/step/${step}`, {});
  if (!out.ok) {
    logEvent({ kind: "system", severity: "error", message: `Onboarding step failed: ${out.error}` });
    return;
  }
  logEvent({ kind: "system", severity: "info", message: `Onboarding step complete: ${step}.` });
  await refreshOnboarding();
}

async function loadProfiles() {
  const mode = document.getElementById("settingsModeSelect")?.value || "standard";
  const expert = mode === "expert";
  const out = await api.get(`/api/settings/profiles?expert=${expert}`);
  const output = document.getElementById("profileOutput");
  if (!output) return;
  if (!out.ok) {
    output.textContent = `Profile load failed: ${out.error}`;
    return;
  }
  state.profile = out.data;
  document.getElementById("profileSelect").value = out.data.profile || "balanced";
  document.getElementById("settingsModeSelect").value = out.data.mode || "standard";
  document.getElementById("automationOptIn").checked = Boolean(out.data.automation_opt_in);
  output.textContent = prettyJson(out.data);
}

async function applyProfile() {
  const profile = document.getElementById("profileSelect").value;
  const mode = document.getElementById("settingsModeSelect").value;
  const automationOptIn = document.getElementById("automationOptIn").checked;
  const out = await api.post(`/api/settings/profile?profile=${encodeURIComponent(profile)}&mode=${encodeURIComponent(mode)}&automation_opt_in=${automationOptIn}`, {});
  const output = document.getElementById("profileOutput");
  if (!out.ok) {
    output.textContent = `Apply preset failed: ${out.error}`;
    logEvent({ kind: "system", severity: "error", message: `Preset apply failed: ${out.error}` });
    return;
  }
  output.textContent = prettyJson(out.data);
  logEvent({ kind: "system", severity: "info", message: `Applied ${profile} profile (${mode} mode).` });
}

async function loadDecisionCard() {
  const ticker = document.getElementById("decisionTickerInput").value.trim().toUpperCase();
  if (!ticker) return;
  const out = await api.get(`/api/decision-card/${ticker}`);
  const output = document.getElementById("decisionOutput");
  if (!out.ok) {
    output.textContent = `Decision card failed: ${out.error}`;
    return;
  }
  output.textContent = prettyJson(out.data);
}

async function mapRecovery() {
  const source = document.getElementById("recoverySource").value;
  const message = document.getElementById("recoveryMessage").value.trim();
  if (!message) return;
  const out = await api.get(`/api/recovery/map?source=${encodeURIComponent(source)}&error=${encodeURIComponent(message)}`);
  const output = document.getElementById("recoveryOutput");
  if (!out.ok) {
    output.textContent = `Recovery mapping failed: ${out.error}`;
    return;
  }
  output.textContent = prettyJson(out.data);
}

async function refreshPerformance() {
  const out = await api.get("/api/performance");
  const output = document.getElementById("performanceOutput");
  if (!output) return;
  if (!out.ok) {
    output.textContent = `Performance load failed: ${out.error}`;
    return;
  }
  state.performance = out.data;
  output.textContent = prettyJson(out.data);
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
  rows.forEach((row) => {
    const card = document.createElement("div");
    card.className = `sector-card ${row.winning ? "win" : "loss"}`;
    card.innerHTML = `
      <div class="${row.winning ? "sector-winning" : "sector-lagging"}"><strong>${safeText(row.etf)}</strong> ${safeText(row.name || "")}</div>
      <div class="${row.winning ? "sector-winning" : "sector-lagging"}">${safeNum(row.return_pct).toFixed(2)}% vs SPY ${safeNum(row.vs_spy).toFixed(2)}%</div>
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
  setLoading({ portfolio: "Loading portfolio..." });
  await Promise.all([
    refreshStatus(),
    refreshPending(),
    refreshPortfolio(),
    refreshSectors(),
    refreshOnboarding(),
    loadProfiles(),
    refreshPerformance(),
  ]);
}

function wireEvents() {
  document.getElementById("scanBtn").addEventListener("click", runScan);
  document.getElementById("refreshBtn").addEventListener("click", refreshAll);
  document.getElementById("onboardingStartBtn").addEventListener("click", startOnboarding);
  document.getElementById("onboardingConnectBtn").addEventListener("click", () => runOnboardingStep("connect"));
  document.getElementById("onboardingVerifyBtn").addEventListener("click", () => runOnboardingStep("verify_token_health"));
  document.getElementById("onboardingScanBtn").addEventListener("click", () => runOnboardingStep("test_scan"));
  document.getElementById("onboardingPaperBtn").addEventListener("click", () => runOnboardingStep("test_paper_order"));
  document.getElementById("applyProfileBtn").addEventListener("click", applyProfile);
  document.getElementById("settingsModeSelect").addEventListener("change", loadProfiles);
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
}

(async () => {
  wireEvents();
  applyReportViewMode();
  applySecCompareMode();
  await loadConfig();
  await refreshAll();
  logEvent({ kind: "system", severity: "info", message: "Dashboard loaded." });
})();

