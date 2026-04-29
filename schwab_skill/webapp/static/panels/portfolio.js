/**
 * Portfolio panel: positions table + risk analytics card.
 *
 * `refreshPortfolio` paints the positions table and shows setup guidance
 * when no account positions are available.
 *
 * `loadPortfolioRisk` paints the risk analytics block underneath
 * (concentration, sector allocation, position weights, day-PL movers,
 * and a single high-level recommendation).
 */

import { api } from "../modules/api.js";
import { safeText, safeNum, formatMoney, formatDecimal } from "../modules/format.js";
import { logEvent } from "../modules/logger.js";
import { state } from "../modules/state.js";

export async function refreshPortfolio() {
  const out = await api.get("/api/portfolio");
  const body = document.getElementById("portfolioBody");
  const meta = document.getElementById("portfolioMeta");
  body.innerHTML = "";
  if (!out.ok) {
    state.lastPortfolioData = null;
    meta.textContent = "Portfolio unavailable.";
    body.innerHTML = `<tr><td colspan="5" class="muted">${safeText(out.error)}</td></tr>`;
    logEvent({ kind: "system", severity: "warn", message: `Portfolio load failed: ${out.error}` });
    return;
  }
  const data = out.data;
  state.lastPortfolioData = data;
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
            <div>No open positions in this account yet.</div>
            <a href="#settingsSection" class="btn small secondary">Open Setup</a>
          </div>
        </td>
      </tr>
    `;
    return;
  }
  data.positions.slice(0, 25).forEach((p) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${safeText(p.symbol)}</td>
      <td>${safeText(p.qty)}</td>
      <td>${formatMoney(p.last)}</td>
      <td>${formatMoney(p.market_value)}</td>
      <td>${safeNum(p.pl_pct) >= 0 ? "+" : ""}${formatDecimal(p.pl_pct, 2, "0.00")}%</td>
    `;
    body.appendChild(tr);
  });
}

export async function loadPortfolioRisk() {
  const panel = document.getElementById("portfolioRiskContent");
  if (!panel) return;
  panel.innerHTML = `<div class="muted">Loading risk analytics...</div>`;
  const out = await api.get("/api/portfolio/risk");
  if (!out.ok) {
    state.lastPortfolioRiskData = null;
    const hint =
      out.status === 409
        ? "Link Schwab account + market data in Setup, then retry."
        : out.status === 401
          ? "Sign in first to load tenant-scoped portfolio analytics."
          : "Retry in a moment. If this persists, check backend logs.";
    panel.innerHTML = `<div class="muted">Risk analytics unavailable: ${safeText(out.error)}</div><div class="muted small">${safeText(hint)}</div><button id="portfolioRiskRetryBtn" class="btn small secondary" type="button" style="margin-top:0.5rem">Retry</button>`;
    document.getElementById("portfolioRiskRetryBtn")?.addEventListener("click", () => void loadPortfolioRisk());
    return;
  }
  const d = out.data;
  state.lastPortfolioRiskData = d;
  if (!d.position_count) {
    const emptyRec = d.recommendation || {};
    panel.innerHTML = `
      <div class="muted">No positions to analyze.</div>
      <div class="risk-recommendation-card" style="margin-top:0.65rem">
        <div class="risk-section-title">Recommendation</div>
        <div>${safeText(emptyRec.headline || "Build a diversified starter allocation")}</div>
        <div class="muted small">${safeText(emptyRec.suggested_action || "When adding positions, spread exposure across multiple sectors and avoid oversized initial positions.")}</div>
      </div>`;
    return;
  }

  const conc = d.concentration || {};
  const concColor = conc.hhi > 2500 ? "var(--bad)" : conc.hhi > 1500 ? "var(--warn)" : "var(--good)";
  const dayColor = d.day_pl_total >= 0 ? "var(--good)" : "var(--bad)";

  let html = `
    <div class="risk-kpi-row">
      <div class="risk-kpi">
        <div class="risk-kpi-value">${formatMoney(d.total_value)}</div>
        <div class="risk-kpi-label">Total Value</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value" style="color:${dayColor}">${d.day_pl_total >= 0 ? "+" : ""}${formatMoney(d.day_pl_total)}</div>
        <div class="risk-kpi-label">Day P/L</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value" style="color:${concColor}">${safeText(conc.hhi_label || "N/A")}</div>
        <div class="risk-kpi-label">Concentration (HHI ${safeText(conc.hhi)})</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${safeText(conc.top_position_pct)}%</div>
        <div class="risk-kpi-label">Largest Position</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${safeText(conc.top_5_pct)}%</div>
        <div class="risk-kpi-label">Top 5 Weight</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${safeText(conc.sector_count)}</div>
        <div class="risk-kpi-label">Sectors</div>
      </div>
    </div>`;

  if (d.recommendation) {
    const rec = d.recommendation;
    const priority = String(rec.priority || "low").toLowerCase();
    const priorityColor = priority === "high" ? "var(--bad)" : priority === "medium" ? "var(--warn)" : "var(--good)";
    html += `
      <div class="risk-section-title">Recommendation</div>
      <div class="risk-recommendation-card">
        <div style="font-weight:600;color:${priorityColor}">${safeText(rec.headline || "Portfolio recommendation")}</div>
        <div class="muted" style="margin-top:0.2rem">${safeText(rec.reason || "")}</div>
        <div class="muted small" style="margin-top:0.35rem">${safeText(rec.suggested_action || "")}</div>
      </div>`;
  }

  if (d.sector_allocation && d.sector_allocation.length) {
    const maxSector = Math.max(1, ...d.sector_allocation.map((s) => s.weight_pct));
    html += `<div class="risk-section-title">Sector Allocation</div><div class="risk-sector-bars">`;
    d.sector_allocation.forEach((s) => {
      const barW = Math.max(2, Math.round((s.weight_pct / maxSector) * 100));
      html += `
        <div class="risk-sector-row">
          <span class="risk-sector-name">${safeText(s.sector)}</span>
          <div class="risk-sector-bar-track">
            <div class="risk-sector-bar-fill" style="width:${barW}%"></div>
          </div>
          <span class="risk-sector-pct mono-nums">${formatDecimal(s.weight_pct, 2)}%</span>
          <span class="risk-sector-val muted mono-nums">${formatMoney(s.value)}</span>
        </div>`;
    });
    html += `</div>`;
  }

  if (d.positions_weighted && d.positions_weighted.length) {
    const maxW = Math.max(1, ...d.positions_weighted.map((p) => p.weight_pct));
    html += `<div class="risk-section-title">Position Weights</div><div class="risk-weight-grid">`;
    d.positions_weighted.slice(0, 15).forEach((p) => {
      const barW = Math.max(2, Math.round((p.weight_pct / maxW) * 100));
      const plColor = p.pl_pct >= 0 ? "var(--good)" : "var(--bad)";
      html += `
        <div class="risk-weight-row">
          <span class="risk-weight-sym">${safeText(p.symbol)}</span>
          <div class="risk-sector-bar-track">
            <div class="risk-weight-bar-fill" style="width:${barW}%"></div>
          </div>
          <span class="risk-sector-pct mono-nums">${formatDecimal(p.weight_pct, 2)}%</span>
          <span class="mono-nums" style="color:${plColor};min-width:52px;text-align:right">${p.pl_pct >= 0 ? "+" : ""}${formatDecimal(p.pl_pct, 2)}%</span>
        </div>`;
    });
    html += `</div>`;
  }

  if (d.day_pl_breakdown && d.day_pl_breakdown.length) {
    html += `<div class="risk-section-title">Day P/L Movers</div><div class="risk-pl-list">`;
    d.day_pl_breakdown.slice(0, 8).forEach((p) => {
      const color = p.day_pl >= 0 ? "var(--good)" : "var(--bad)";
      html += `
        <div class="risk-pl-row">
          <span class="risk-weight-sym">${safeText(p.symbol)}</span>
          <span class="mono-nums" style="color:${color}">${p.day_pl >= 0 ? "+" : ""}${formatMoney(p.day_pl)}</span>
        </div>`;
    });
    html += `</div>`;
  }

  panel.innerHTML = html;
}
