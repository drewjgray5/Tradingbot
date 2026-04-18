/**
 * Calibration panel — surfaces self-study and hypothesis-ledger
 * snapshots from `/api/calibration/summary`, plus the "trading halt"
 * toggle (SaaS-only).
 *
 * `submitTradingHaltSave` is wired from the global "trading halt"
 * checkbox in `wireEvents`; it accepts an injected `refreshAccountMe`
 * so we can re-read /api/me without a circular import.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { escapeHtml, safeNum, prettyJson } from "../modules/format.js";
import { updateActionCenter } from "../modules/logger.js";

export function renderCalibrationPanel(panel, data, error) {
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
    const ss = data.self_study;
    let ssHtml = '<div class="preset-subsection"><h3>Self-study</h3>';
    if (ss.min_conviction_threshold != null) {
      ssHtml += `<div class="perf-metric"><span class="label">Min conviction threshold</span><span class="value">${safeNum(ss.min_conviction_threshold, 1)}</span></div>`;
    }
    if (ss.round_trips != null) {
      ssHtml += `<div class="perf-metric"><span class="label">Round trips</span><span class="value">${safeNum(ss.round_trips, 0)}</span></div>`;
    }
    if (ss.win_rate != null) {
      ssHtml += `<div class="perf-metric"><span class="label">Win rate</span><span class="value">${(safeNum(ss.win_rate, 2) * 100).toFixed(1)}%</span></div>`;
    }
    if (ss.avg_return_pct != null) {
      ssHtml += `<div class="perf-metric"><span class="label">Avg return</span><span class="value">${safeNum(ss.avg_return_pct, 2).toFixed(2)}%</span></div>`;
    }
    ssHtml += `<details class="tool-json-details" style="margin-top: 8px;"><summary>Raw data</summary><pre class="code-block code-block--tight">${escapeHtml(prettyJson(ss))}</pre></details>`;
    ssHtml += "</div>";
    parts.push(ssHtml);
  }
  if (data.hypothesis_ledger) {
    const hl = data.hypothesis_ledger;
    let hlHtml = '<div class="preset-subsection"><h3>Hypothesis ledger</h3>';
    if (hl.total_hypotheses != null) {
      hlHtml += `<div class="perf-metric"><span class="label">Total hypotheses</span><span class="value">${safeNum(hl.total_hypotheses, 0)}</span></div>`;
    }
    if (hl.scored != null) {
      hlHtml += `<div class="perf-metric"><span class="label">Scored</span><span class="value">${safeNum(hl.scored, 0)}</span></div>`;
    }
    if (hl.hit_rate != null) {
      hlHtml += `<div class="perf-metric"><span class="label">Hit rate</span><span class="value">${(safeNum(hl.hit_rate, 2) * 100).toFixed(1)}%</span></div>`;
    }
    hlHtml += `<details class="tool-json-details" style="margin-top: 8px;"><summary>Raw data</summary><pre class="code-block code-block--tight">${escapeHtml(prettyJson(hl))}</pre></details>`;
    hlHtml += "</div>";
    parts.push(hlHtml);
  }
  panel.innerHTML =
    parts.length > 0
      ? parts.join("")
      : `<div class="muted">No calibration data available yet.</div>`;
}

export async function refreshCalibration() {
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

export async function submitTradingHaltSave({ refreshAccountMe = async () => {} } = {}) {
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
