/**
 * Performance + Challenger + Evolve panels.
 *
 * `renderPerformancePanel` paints the three "buckets" (backtest, shadow,
 * live) plus the validation badge, callout, and recent-outcomes table.
 * It also opportunistically renders the challenger card when
 * `data.challenger.available` is true.
 *
 * `renderChallengerPanel` paints the champion-vs-challenger comparison
 * card; `renderEvolvePanel` paints the feature-importance + suggested-
 * threshold-overrides card from the learning-engine output.
 *
 * `refreshPerformance` pulls `/api/performance` and toggles the Evolve /
 * Challenger buttons based on whether their preconditions are met.
 *
 * `getDisplayMode` is injected so the raw-JSON `<details>` element can
 * auto-open for "pro" users.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { safeText, prettyJson, formatPercentPoints } from "../modules/format.js";

export function renderPerformancePanel(rootEl, data, { error, getDisplayMode = () => "balanced" } = {}) {
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

  const ch = data && data.challenger && typeof data.challenger === "object" ? data.challenger : null;
  if (ch && ch.available && ch.latest) {
    const challengerPanel = document.getElementById("challengerPanel");
    if (challengerPanel) renderChallengerPanel(challengerPanel, ch);
  }
}

export function renderChallengerPanel(rootEl, ch) {
  if (!rootEl || !ch) return;
  const latest = ch.latest && typeof ch.latest === "object" ? ch.latest : null;
  const wr = ch.win_rate && typeof ch.win_rate === "object" ? ch.win_rate : {};
  if (!latest) {
    rootEl.innerHTML = "";
    return;
  }
  const v = safeText(latest.verdict || "?");
  const delta = latest.score_delta != null ? Number(latest.score_delta).toFixed(1) : "?";
  const champ = latest.champion || {};
  const chall = latest.challenger || {};
  let verdictClass = "bg-slate-900";
  if (v === "challenger_better") verdictClass = "bg-green-900";
  else if (v === "champion_better") verdictClass = "bg-red-900";

  const overrides = latest.env_overrides && typeof latest.env_overrides === "object"
    ? Object.entries(latest.env_overrides).map(([k, val]) => `<code>${safeText(k)}=${safeText(val)}</code>`).join(", ")
    : "none";

  let wrLine = "";
  if (wr.total_runs > 0) {
    wrLine = `<p class="muted">Overall: ${safeText(wr.total_runs)} runs — Challenger wins ${safeText(wr.challenger_wins)}, Champion wins ${safeText(wr.champion_wins)}, Ties ${safeText(wr.ties)} (${safeText(wr.challenger_win_rate_pct)}% challenger win rate)</p>`;
  }

  rootEl.innerHTML = `
    <h3>Champion vs Challenger</h3>
    <div class="performance-buckets">
      <div class="perf-bucket">
        <h3>Champion (current)</h3>
        <div class="perf-metric"><span class="label">Signals</span><span class="value">${safeText(champ.count)}</span></div>
        <div class="perf-metric"><span class="label">Avg score</span><span class="value">${safeText(champ.avg_score)}</span></div>
        <div class="perf-metric"><span class="label">Top ticker</span><span class="value">${safeText(champ.top_ticker || "—")}</span></div>
      </div>
      <div class="perf-bucket">
        <h3>Challenger (suggested)</h3>
        <div class="perf-metric"><span class="label">Signals</span><span class="value">${safeText(chall.count)}</span></div>
        <div class="perf-metric"><span class="label">Avg score</span><span class="value">${safeText(chall.avg_score)}</span></div>
        <div class="perf-metric"><span class="label">Top ticker</span><span class="value">${safeText(chall.top_ticker || "—")}</span></div>
      </div>
    </div>
    <div class="performance-validation">
      <span class="health-badge ${verdictClass}">${v.replace(/_/g, " ")}</span>
      <span class="muted">Score delta: <strong>${delta}</strong></span>
      <span class="muted">Run: ${safeText(latest.run_at || "?")}</span>
    </div>
    <p class="muted" style="margin-top:0.5rem">Overrides tested: ${overrides}</p>
    ${wrLine}
  `;
}

export function renderEvolvePanel(rootEl, data) {
  const rawDetails = document.getElementById("learningRawDetails");
  const rawPre = document.getElementById("learningRaw");
  if (!rootEl) return;
  if (rawPre && data) rawPre.textContent = prettyJson(data);
  if (rawDetails && data) rawDetails.classList.remove("hidden");

  if (!data || typeof data !== "object") {
    rootEl.innerHTML = `<div class="report-empty">No learning engine results yet.</div>`;
    return;
  }
  if (data.status !== "ok") {
    rootEl.innerHTML = `<div class="panel-error">${safeText(data.message || data.error || data.status)}</div>`;
    return;
  }

  const training = data.training || {};
  const importance = training.feature_importance || [];
  const updates = data.updates || [];
  const r2Train = Number(training.r2_train != null ? training.r2_train : 0);
  const r2Val = training.r2_validation == null ? null : Number(training.r2_validation);
  const r2Label = r2Val == null
    ? `train R² = ${r2Train.toFixed(4)}`
    : `train R² = ${r2Train.toFixed(4)}, val R² = ${r2Val.toFixed(4)}`;

  const impRows = importance.slice(0, 10).map((f) => {
    const barW = Math.min(100, Math.round((f.importance || 0) * 200));
    return `<tr>
      <td>${safeText(f.feature)}</td>
      <td>${Number(f.importance).toFixed(4)}</td>
      <td><div style="background:var(--accent);height:12px;width:${barW}%;border-radius:4px;"></div></td>
    </tr>`;
  }).join("");

  const updateRows = updates.map((u) => `<tr>
    <td><code>${safeText(u.env_key)}</code></td>
    <td>${safeText(u.current_value)}</td>
    <td><strong>${safeText(u.suggested_value)}</strong></td>
    <td>${Number(u.importance).toFixed(3)}</td>
    <td class="muted">${safeText(u.rationale).substring(0, 120)}</td>
  </tr>`).join("");

  rootEl.innerHTML = `
    <div class="perf-bucket" style="margin-bottom:1rem">
      <h3>Feature Importance (${r2Label}, n = ${safeText(training.n_samples)})</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Feature</th><th>Importance</th><th></th></tr></thead>
          <tbody>${impRows || '<tr><td colspan="3" class="muted">No features analyzed</td></tr>'}</tbody>
        </table>
      </div>
    </div>
    ${updates.length ? `
    <div class="perf-bucket">
      <h3>Suggested Threshold Adjustments (${updates.length})</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Parameter</th><th>Current</th><th>Suggested</th><th>Importance</th><th>Rationale</th></tr></thead>
          <tbody>${updateRows}</tbody>
        </table>
      </div>
      <p class="muted" style="margin-top:0.5rem">Run a <strong>Challenger Scan</strong> to test these adjustments before applying.</p>
    </div>` : '<p class="muted">No threshold adjustments suggested at this time.</p>'}
  `;
}

export async function refreshPerformance({ getDisplayMode = () => "balanced" } = {}) {
  const out = await api.get("/api/performance");
  const panel = document.getElementById("performancePanel");
  const evolveBtn = document.getElementById("evolveBtn");
  const challengerBtn = document.getElementById("challengerBtn");
  if (!panel) return;
  if (!out.ok) {
    renderPerformancePanel(panel, null, { error: `Performance load failed: ${out.error}`, getDisplayMode });
    if (evolveBtn) {
      evolveBtn.disabled = true;
      evolveBtn.title = "Performance data unavailable.";
    }
    if (challengerBtn) {
      challengerBtn.disabled = true;
      challengerBtn.title = "Performance data unavailable.";
    }
    return;
  }
  state.performance = out.data;
  renderPerformancePanel(panel, out.data, { getDisplayMode });
  const outcomeCount = Number(out.data?.live?.recorded_outcomes || 0);
  if (evolveBtn) {
    const canRunEvolve = outcomeCount > 0;
    evolveBtn.disabled = !canRunEvolve;
    evolveBtn.title = canRunEvolve
      ? ""
      : "No persisted trade outcomes yet. Execute trades first, then run analysis.";
  }
  const canRunChallenger = Boolean(out.data?.challenger?.can_run);
  if (challengerBtn) {
    challengerBtn.disabled = !canRunChallenger;
    challengerBtn.title = canRunChallenger
      ? ""
      : "Run Post-Mortem Analysis first to generate strategy overrides.";
  }
}
