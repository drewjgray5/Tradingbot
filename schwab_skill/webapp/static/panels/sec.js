/**
 * SEC compare panel — diff filings between two tickers, or one ticker
 * over time. Renders the verdict card, narrative card, change card,
 * and the per-side analysis grid; falls back to an EDGAR-metadata-only
 * compare when the dedicated `/api/sec/compare` endpoint isn't
 * deployed.
 *
 * `runSecCompare` accepts an injected `getDisplayMode` so the deep-dive
 * `<details>` element can auto-expand for "pro" users.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { safeText, safeNum } from "../modules/format.js";
import { logEvent, updateActionCenter, statusClass, sentimentTagClass } from "../modules/logger.js";

export function applySecCompareMode() {
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

export function renderSecAnalysisCard(label, analysis) {
  if (!analysis) return "";
  const themes = (analysis.key_themes || []).slice(0, 3).map((t) => `<li>${safeText(t)}</li>`).join("");
  const risks = (analysis.risk_terms || []).slice(0, 5).join(", ") || "None highlighted";
  const guidance = safeText(analysis.guidance_signal || "neutral");
  const takeaway = safeText(analysis.high_level_takeaway || "No takeaway.");
  const verdict = safeText(analysis.verdict || "neutral");
  const confidence = Number.isFinite(Number(analysis.confidence)) ? Number(analysis.confidence) : null;
  const why = (analysis.why || []).slice(0, 3);
  const evidence = (analysis.evidence || []).slice(0, 2);
  const limits = (analysis.limits || []).slice(0, 3);
  const analysisMode = safeText(analysis.analysis_mode || "full_text");
  const warning = analysisMode !== "full_text" || limits.length
    ? `<div class="report-callout warn">Mode: ${analysisMode}. ${limits.length ? `Limits: ${safeText(limits.join("; "))}` : "Reduced confidence mode."}</div>`
    : "";
  return `
    <div class="compare-card">
      <h4>${safeText(label)}</h4>
      <div class="subtle">Verdict: <span class="${statusClass(verdict === "bullish" ? "good" : verdict === "bearish" ? "bad" : "neutral")}">${verdict}</span>${confidence !== null ? ` | Confidence: ${safeText(confidence)}/100` : ""}</div>
      ${warning}
      <ul class="report-bullets">
        <li>Form: ${safeText(analysis.form)} | Filed: ${safeText(analysis.filing_date)}</li>
        <li>Guidance: <span class="${statusClass(guidance === "negative" ? "bad" : guidance === "positive" ? "good" : "neutral")}">${guidance}</span></li>
        <li>Risk terms: ${safeText(risks)}</li>
        <li>Takeaway: ${takeaway}</li>
      </ul>
      ${why.length ? `<div class="subtle">Why this verdict</div><ul class="report-bullets">${why.map((w) => `<li>${safeText(w)}</li>`).join("")}</ul>` : ""}
      ${evidence.length ? `<div class="subtle">Top evidence</div><ul class="report-bullets">${evidence.map((ev) => `<li>${safeText(ev.claim || "Evidence")}: ${safeText(ev.quote || "")}</li>`).join("")}</ul>` : ""}
      <div class="subtle">Top themes</div>
      <ul class="report-bullets">${themes || "<li>No theme sentences extracted.</li>"}</ul>
    </div>
  `;
}

export function toReadableDeltaLabel(key) {
  const map = {
    revenue_mentions: "Revenue references",
    profit_mentions: "Profitability references",
    cashflow_mentions: "Cash-flow references",
    debt_mentions: "Debt references",
    liquidity_mentions: "Liquidity references",
  };
  return map[key] || String(key || "").replaceAll("_", " ");
}

export function buildNarrativeSummary(comparePayload) {
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

export function renderSecCompareEmpty(message) {
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

export function renderSecCompareVisual(data, { getDisplayMode = () => "balanced" } = {}) {
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
  const compareConfidence = Number.isFinite(Number(compare.compare_confidence)) ? Number(compare.compare_confidence) : null;
  const analysisMode = safeText(compare.analysis_mode || data.analysis_mode || "full_text");
  const compareLimits = (compare.limits || []).slice(0, 3);
  const rationale = (compare.change_summary?.plain_english_rationale || []).slice(0, 3);
  const evidenceRanked = (compare.evidence || compare.change_summary?.evidence_ranked || []).slice(0, 4);
  const warning = analysisMode !== "full_text" || compareLimits.length;

  headlineRoot.innerHTML = `
    <div class="report-section compare-headline-card">
      <h4>SEC Compare Verdict</h4>
      <div><span class="${sentimentTagClass(sentimentTag)}">${sentimentTag}</span></div>
      <div class="compare-lead">${headline}</div>
      <div class="subtle">Mode: ${safeText(data.mode || compare.mode || "N/A")} | Form: ${safeText(data.form_type || "N/A")} | Analysis: ${analysisMode}${compareConfidence !== null ? ` | Confidence: ${safeText(compareConfidence)}/100` : ""}</div>
      ${warning ? `<div class="report-callout warn">Reduced confidence context. ${compareLimits.length ? `Limits: ${safeText(compareLimits.join("; "))}` : "Metadata fallback or partial evidence mode."}</div>` : ""}
    </div>
  `;

  narrativeRoot.innerHTML = `
    <div class="report-section compare-narrative-card">
      <h4>The "Red Flag" Ledger</h4>
      <ul class="report-bullets">
        ${(redFlags.length ? redFlags : differencesRaw.slice(0, 4)).map((x) => `<li>${safeText(x)}</li>`).join("") || "<li>No newly introduced legal-risk language flagged.</li>"}
      </ul>
      ${rationale.length ? `<div class="subtle">Why this verdict</div><ul class="report-bullets">${rationale.map((x) => `<li>${safeText(x)}</li>`).join("")}</ul>` : ""}
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
      ${evidenceRanked.length ? `<div class="subtle">Top evidence snippets</div><ul class="report-bullets">${evidenceRanked.map((ev) => `<li>${safeText(ev.claim || "Evidence")}: ${safeText(ev.quote || "")}</li>`).join("")}</ul>` : ""}
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

export async function buildFallbackSecCompare(mode, tickerA, tickerB, formType) {
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
        analysis_mode: "metadata_fallback",
        compare_confidence: 25,
        limits: ["Metadata-only fallback (full filing text unavailable)"],
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

export async function runSecCompare({ getDisplayMode = () => "balanced" } = {}) {
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
    renderSecCompareVisual(payload, { getDisplayMode });
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
