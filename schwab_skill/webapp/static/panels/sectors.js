/**
 * Sector-strength panel — draws one card per sector ETF with its
 * SPY-relative bar. Pure DOM render driven by `/api/sectors`.
 */

import { api } from "../modules/api.js";
import { safeText, safeNum } from "../modules/format.js";
import { logEvent } from "../modules/logger.js";

export async function refreshSectors() {
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
