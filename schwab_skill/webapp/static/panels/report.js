/**
 * Stock report panel — `/api/report/<ticker>` is the data source.
 *
 * `renderReportTabs` and `renderReportVisual` are tightly coupled: the
 * tabs call back into the visual renderer (and re-render themselves) on
 * each tab click. They share `state.activeReportTab` to track the
 * selection, and `state.reportRawView` toggles the raw-JSON view.
 *
 * `runReport` is wired from the "Run Report" button in `wireEvents`.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { safeText, safeNum, formatMoney, pct, verdictFromScore } from "../modules/format.js";
import { logEvent, updateActionCenter } from "../modules/logger.js";

export function renderReportTabs(data) {
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

export function renderReportVisual(data) {
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

export function applyReportViewMode() {
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

export async function runReport() {
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
