/**
 * Unified slide-in trade drawer.
 *
 * Replaces the previous trio of single-purpose panels:
 *   - panels/quickView.js       (slide-in decision card for a pending trade)
 *   - panels/decisionCard.js    (in-page decision card form + render)
 *   - panels/recovery.js        (in-page failure-recovery form + render)
 *
 * The drawer hosts two tabs — Decision and Recovery — that share the
 * same surface so any "explain this trade" or "explain this error"
 * entry point opens the same UI. The drawer participates in keyboard
 * focus management (Esc closes, backdrop click closes) and exposes a
 * tiny imperative API (`openTradeDrawer`, `openTradeDrawerForTrade`,
 * `closeTradeDrawer`) so call sites in app.js and commandPalette.js can
 * use it without owning the DOM.
 *
 * DOM contract — the drawer expects these IDs in index.html:
 *   #tradeDrawer, #tradeDrawerCloseBtn, #tradeDrawerBackdrop,
 *   #tradeDrawerTabDecision, #tradeDrawerTabRecovery,
 *   #tradeDrawerPanelDecision, #tradeDrawerPanelRecovery,
 *   #tradeDrawerDecisionTicker, #tradeDrawerDecisionBtn,
 *   #tradeDrawerDecisionPlaceholder, #tradeDrawerDecisionSummary,
 *   #tradeDrawerDecisionJsonDetails, #tradeDrawerDecisionOutput,
 *   #tradeDrawerRecoverySource, #tradeDrawerRecoveryMessage,
 *   #tradeDrawerRecoveryBtn, #tradeDrawerRecoveryPlaceholder,
 *   #tradeDrawerRecoverySummary, #tradeDrawerRecoveryJsonDetails,
 *   #tradeDrawerRecoveryOutput.
 */

import { api } from "../modules/api.js";
import { safeText, safeNum, formatMoney, prettyJson } from "../modules/format.js";

const TABS = ["decision", "recovery"];
let _wired = false;

function $(id) {
  return document.getElementById(id);
}

function setActiveTab(tab) {
  if (!TABS.includes(tab)) tab = "decision";
  for (const t of TABS) {
    const btn = $(`tradeDrawerTab${t === "decision" ? "Decision" : "Recovery"}`);
    const panel = $(`tradeDrawerPanel${t === "decision" ? "Decision" : "Recovery"}`);
    const active = t === tab;
    if (btn) {
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
      btn.tabIndex = active ? 0 : -1;
    }
    if (panel) panel.classList.toggle("hidden", !active);
  }
}

/**
 * Render the decision-card summary for either the in-drawer Decision
 * tab or the legacy in-page #decisionSection. Pass an `idPrefix` of
 * `"tradeDrawerDecision"` for the drawer or `"decision"` for legacy.
 */
function renderDecisionInto(idPrefix, data, error) {
  const ph = $(`${idPrefix}Placeholder`);
  const sum = $(`${idPrefix}Summary`);
  const det = $(`${idPrefix}JsonDetails`);
  const pre = $(`${idPrefix}Output`);
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
    if (pre) pre.textContent = "";
    return;
  }
  if (ph) ph.classList.add("hidden");
  const d = data || {};
  const ez = d.entry_zone || {};
  const sz = d.size || {};
  const conf = d.confidence || {};
  const blocked = Boolean(d.checklist && d.checklist.blocked);
  const scoreN = Number(conf.signal_score);
  const scoreTxt = Number.isFinite(scoreN) ? scoreN.toFixed(1) : "—";
  const verdict = blocked
    ? "Blocked by safety checks."
    : "Passes current safety snapshot.";
  const verdictClass = blocked ? "bad" : "good";
  if (sum) {
    sum.classList.remove("hidden");
    sum.innerHTML = `
      <h4 class="tool-summary-title">${safeText(d.ticker)}</h4>
      <ul class="tool-summary-list">
        <li><strong>Size:</strong> ${safeNum(sz.qty, 0)} shares (~${formatMoney(sz.usd || 0)})</li>
        <li><strong>Entry zone:</strong> $${safeText(ez.low)} – $${safeText(ez.high)}</li>
        <li><strong>Stop idea:</strong> $${safeText(d.stop_invalidation)}</li>
        <li><strong>Confidence:</strong> ${safeText(conf.bucket)} (score ${scoreTxt})</li>
        <li><strong>Status:</strong> <span class="pill ${verdictClass} small">${verdict}</span></li>
      </ul>
      ${(d.key_reasons || []).length ? `<div class="chip-row" style="margin-top: 8px;">${d.key_reasons.map(r => `<span class="chip">${safeText(r)}</span>`).join("")}</div>` : ""}
    `;
  }
  if (det) det.classList.remove("hidden");
  if (pre) pre.textContent = prettyJson(data);
}

function renderRecoveryInto(idPrefix, data, error) {
  const ph = $(`${idPrefix}Placeholder`);
  const sum = $(`${idPrefix}Summary`);
  const det = $(`${idPrefix}JsonDetails`);
  const pre = $(`${idPrefix}Output`);
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
    if (pre) pre.textContent = "";
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
  if (pre) pre.textContent = prettyJson(data);
}

async function fetchDecision(ticker) {
  const sym = ticker.toUpperCase().trim();
  if (!sym) return { ok: false, error: "Enter a ticker first." };
  return api.get(`/api/decision-card/${encodeURIComponent(sym)}`);
}

async function fetchRecovery(source, message) {
  const msg = (message || "").trim();
  if (!msg) return { ok: false, error: "Paste an error message first." };
  return api.get(
    `/api/recovery/map?source=${encodeURIComponent(source)}&error=${encodeURIComponent(msg)}`,
  );
}

/** Run the Decision lookup driven by the drawer's own ticker input. */
export async function loadDecisionInDrawer() {
  const inputEl = $("tradeDrawerDecisionTicker");
  const ticker = inputEl ? inputEl.value : "";
  renderDecisionInto("tradeDrawerDecision", null, "Loading decision card…");
  const out = await fetchDecision(ticker);
  if (!out.ok) {
    renderDecisionInto("tradeDrawerDecision", null, `Decision card failed: ${out.error}`);
    return;
  }
  renderDecisionInto("tradeDrawerDecision", out.data, null);
}

/** Run the Recovery lookup driven by the drawer's own inputs. */
export async function loadRecoveryInDrawer() {
  const sourceEl = $("tradeDrawerRecoverySource");
  const messageEl = $("tradeDrawerRecoveryMessage");
  const source = sourceEl ? sourceEl.value : "schwab_auth";
  const message = messageEl ? messageEl.value : "";
  renderRecoveryInto("tradeDrawerRecovery", null, "Mapping recovery…");
  const out = await fetchRecovery(source, message);
  if (!out.ok) {
    renderRecoveryInto("tradeDrawerRecovery", null, `Recovery mapping failed: ${out.error}`);
    return;
  }
  renderRecoveryInto("tradeDrawerRecovery", out.data, null);
}

/**
 * Open the drawer to a specific tab and optionally prefill inputs.
 *
 * @param {object} [opts]
 * @param {"decision"|"recovery"} [opts.tab="decision"]
 * @param {string} [opts.ticker]            – prefill + auto-load the decision card
 * @param {string} [opts.recoverySource]    – prefill source dropdown
 * @param {string} [opts.recoveryMessage]   – prefill error message + auto-map
 */
export function openTradeDrawer(opts = {}) {
  const drawer = $("tradeDrawer");
  if (!drawer) return;
  ensureWired();
  const tab = opts.tab && TABS.includes(opts.tab) ? opts.tab : "decision";
  setActiveTab(tab);
  drawer.classList.add("open");
  drawer.removeAttribute("hidden");
  document.body.classList.add("trade-drawer-open");
  if (tab === "decision") {
    if (opts.ticker) {
      const inputEl = $("tradeDrawerDecisionTicker");
      if (inputEl) inputEl.value = String(opts.ticker).toUpperCase();
      void loadDecisionInDrawer();
    }
    queueMicrotask(() => $("tradeDrawerDecisionTicker")?.focus());
  } else {
    if (opts.recoverySource) {
      const sel = $("tradeDrawerRecoverySource");
      if (sel) sel.value = opts.recoverySource;
    }
    if (opts.recoveryMessage) {
      const msg = $("tradeDrawerRecoveryMessage");
      if (msg) msg.value = opts.recoveryMessage;
      void loadRecoveryInDrawer();
    }
    queueMicrotask(() => $("tradeDrawerRecoveryMessage")?.focus());
  }
}

/** Backwards-compatible entry point used by the pending-trade Quick View row action. */
export async function openTradeDrawerForTrade(row) {
  if (!row || !row.ticker) return;
  openTradeDrawer({ tab: "decision", ticker: row.ticker });
}

export function closeTradeDrawer() {
  const drawer = $("tradeDrawer");
  if (!drawer) return;
  drawer.classList.remove("open");
  // Hide after the slide animation so screen readers don't announce it.
  setTimeout(() => {
    if (!drawer.classList.contains("open")) drawer.setAttribute("hidden", "");
  }, 220);
  document.body.classList.remove("trade-drawer-open");
}

/**
 * Wire the drawer's internal events. Called once on first open so the
 * legacy in-page sections that delegate into the drawer don't have to
 * pay the cost of binding listeners up front.
 */
function ensureWired() {
  if (_wired) return;
  _wired = true;

  const closeBtn = $("tradeDrawerCloseBtn");
  closeBtn?.addEventListener("click", () => closeTradeDrawer());

  const backdrop = $("tradeDrawerBackdrop");
  backdrop?.addEventListener("click", () => closeTradeDrawer());

  const decisionBtn = $("tradeDrawerDecisionBtn");
  decisionBtn?.addEventListener("click", () => void loadDecisionInDrawer());
  const decisionInput = $("tradeDrawerDecisionTicker");
  decisionInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void loadDecisionInDrawer();
    }
  });

  const recoveryBtn = $("tradeDrawerRecoveryBtn");
  recoveryBtn?.addEventListener("click", () => void loadRecoveryInDrawer());
  const recoveryInput = $("tradeDrawerRecoveryMessage");
  recoveryInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void loadRecoveryInDrawer();
    }
  });

  for (const t of TABS) {
    const btn = $(`tradeDrawerTab${t === "decision" ? "Decision" : "Recovery"}`);
    btn?.addEventListener("click", () => setActiveTab(t));
  }

  // Esc closes when the drawer is open.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const drawer = $("tradeDrawer");
    if (drawer?.classList.contains("open")) {
      e.preventDefault();
      closeTradeDrawer();
    }
  });
}

// Legacy adapters --------------------------------------------------------
//
// The in-page "Decision Card" and "Failure Recovery" sections still
// exist as thin landing cards with a CTA that opens the drawer. We keep
// these named exports so the small footprint of legacy event handlers
// in app.js doesn't have to know about the drawer's internals.

/** Open the drawer's Decision tab; ignored arguments preserved for compat. */
export function loadDecisionCard() {
  // Older code path read #decisionTickerInput; honour it if present so
  // existing keyboard shortcuts still pre-fill the lookup.
  const legacyInput = $("decisionTickerInput");
  const ticker = legacyInput?.value?.trim();
  openTradeDrawer({ tab: "decision", ticker });
}

/** Open the drawer's Recovery tab; ignored arguments preserved for compat. */
export function mapRecovery() {
  const legacySource = $("recoverySource");
  const legacyMessage = $("recoveryMessage");
  openTradeDrawer({
    tab: "recovery",
    recoverySource: legacySource?.value,
    recoveryMessage: legacyMessage?.value?.trim(),
  });
}
