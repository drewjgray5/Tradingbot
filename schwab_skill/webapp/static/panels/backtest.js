/**
 * Backtest panel — owns the queue form, the result summary card, the
 * recent-runs list, the form-persistence cache (localStorage), and the
 * background-task poller for queued runs.
 *
 * Public surface:
 *   - setDefaultBacktestDates / restoreBacktestFormFromStorage /
 *     wireBacktestFormPersistence / resetBacktestFormToDefaults:
 *     form lifecycle helpers used by `wireEvents` at boot time.
 *   - syncBtUniverseRow / applyBacktestPresetYears: form interactions.
 *   - collectBacktestSpecFromForm / collectBacktestOverrides: build the
 *     payload sent to `/api/backtest-runs`. `collectBacktestSpecFromForm`
 *     is also reused by the scan panel via `scanBodyFromBacktestSpec`.
 *   - renderBacktestResultSummary / renderBacktestResultRaw /
 *     backtestSpecSummaryLine: shared by the panel itself and the
 *     scan-from-latest-backtest helper.
 *   - switchBacktestHubTab / refreshBacktestRuns: tab-switching + recent
 *     runs list, also called by the strategy-chat panel.
 *   - queueUserBacktest: the "Run backtest" button entrypoint.
 *
 * Injected dependencies:
 *   - `setJobProgress`: shared scan/backtest progress-bar helper.
 *   - `getDisplayMode`: controls auto-opening the raw-JSON panel for pro.
 */

import { state, BACKTEST_PREFS_KEY } from "../modules/state.js";
import { api } from "../modules/api.js";
import {
  safeText,
  escapeHtml,
  prettyJson,
  formatPercentPoints,
} from "../modules/format.js";
import { logEvent } from "../modules/logger.js";
import { scrollStrategyChatToEnd } from "./strategyChat.js";

export function setDefaultBacktestDates() {
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

export function restoreBacktestFormFromStorage() {
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

export function snapshotBacktestFormForStorage() {
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
export function schedulePersistBacktestForm() {
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

export function wireBacktestFormPersistence() {
  const root = document.getElementById("backtestSection");
  if (!root) return;
  root.addEventListener("input", schedulePersistBacktestForm);
  root.addEventListener("change", schedulePersistBacktestForm);
}

export function resetBacktestFormToDefaults() {
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

export function setBacktestQueueUiBusy(busy) {
  state.backtestQueueBusy = busy;
  const btn = document.getElementById("btQueueBtn");
  if (btn) btn.disabled = busy;
  const spin = document.getElementById("btMetaSpinner");
  const metaText = document.getElementById("btMetaText");
  if (spin) spin.classList.toggle("hidden", !busy);
  if (metaText && busy && !metaText.dataset.sticky) metaText.textContent = "Running…";
}

export function setBtMetaMessage(text, { sticky = false } = {}) {
  const metaText = document.getElementById("btMetaText");
  if (!metaText) return;
  metaText.textContent = text;
  if (sticky) metaText.dataset.sticky = "1";
  else delete metaText.dataset.sticky;
}

export function syncBtUniverseRow() {
  const sel = document.getElementById("btUniverse");
  const row = document.getElementById("btTickersRow");
  if (!row) return;
  const mode = sel?.value || "watchlist";
  row.classList.toggle("hidden", mode !== "tickers");
}

export function applyBacktestPresetYears(years) {
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

export function collectBacktestOverrides() {
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

export function collectBacktestSpecFromForm() {
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

export function renderBacktestResultSummary(result) {
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

export function renderBacktestResultRaw(result, fallbackText, { getDisplayMode = () => "balanced" } = {}) {
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

export function backtestSpecSummaryLine(spec) {
  if (!spec || typeof spec !== "object") return "";
  const mode = spec.universe_mode === "tickers" ? "custom tickers" : "watchlist";
  const dr = spec.start_date && spec.end_date ? `${safeText(spec.start_date)} → ${safeText(spec.end_date)}` : "";
  const n = Array.isArray(spec.tickers) ? spec.tickers.length : 0;
  const tickPart = spec.universe_mode === "tickers" && n ? ` · ${n} names` : "";
  return `${mode}${tickPart}${dr ? ` · ${dr}` : ""}`;
}

export function switchBacktestHubTab(which) {
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

export async function refreshBacktestRuns() {
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

export async function pollBacktestTask(taskId, { setJobProgress = () => {}, getDisplayMode = () => "balanced" } = {}) {
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
      renderBacktestResultRaw(d.result, "", { getDisplayMode });
      setBtMetaMessage("Complete. Summary above; full JSON below.", { sticky: true });
      setJobProgress("btJobProgress", "btJobProgressLabel", 1, "Complete");
      await refreshBacktestRuns();
      return;
    }
    if (celery === "failure" || celery === "revoked") {
      renderBacktestResultSummary(null);
      renderBacktestResultRaw(null, prettyJson(d.task_result || d), { getDisplayMode });
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

export async function queueUserBacktest({ setJobProgress = () => {}, getDisplayMode = () => "balanced" } = {}) {
  if (state.backtestQueueBusy) return;
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
    if (taskId) await pollBacktestTask(taskId, { setJobProgress, getDisplayMode });
    else await refreshBacktestRuns();
  } finally {
    setBacktestQueueUiBusy(false);
  }
}
