/**
 * Lightweight UI feedback layer used by every panel.
 *
 * Three concerns:
 *  - `logEvent` writes a row to the activity drawer (`#logList`) and
 *    updates the action centre at the same time.
 *  - `updateActionCenter` updates the persistent banner shown above the
 *    main scan results.
 *  - `updateActivityBadge` keeps the activity-drawer-toggle's unread
 *    counter in sync with the log list.
 *
 * The legacy `app.js` reassigned `logEvent` after declaration to chain
 * `updateActivityBadge`; here the chain is built into `logEvent` itself,
 * which is cleaner and module-friendly.
 *
 * `statusClass`, `sentimentTagClass`, `healthBadgeClass`, and `setStatusPill`
 * are tiny class-selectors used by panel renderers; they live here because
 * they're consumed by the same callers as `logEvent`/`updateActionCenter`.
 */

import { safeText } from "./format.js";

export function updateActionCenter({ title = "System Messages", message = "", severity = "info" } = {}) {
  const wrap = document.getElementById("actionCenter");
  const titleEl = document.getElementById("actionCenterTitle");
  const textEl = document.getElementById("actionCenterText");
  if (!wrap || !titleEl || !textEl) return;
  wrap.classList.remove("info", "success", "warn", "error");
  wrap.classList.add(["info", "success", "warn", "error"].includes(severity) ? severity : "info");
  titleEl.textContent = title;
  textEl.textContent = message || "Ready.";
}

export function updateActivityBadge() {
  const toggle = document.getElementById("activityDrawerToggle");
  const list = document.getElementById("logList");
  if (!toggle || !list) return;
  const count = list.children.length;
  let badge = toggle.querySelector(".activity-badge");
  if (count > 0) {
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "activity-badge";
      toggle.appendChild(badge);
    }
    badge.textContent = count > 99 ? "99+" : String(count);
  } else if (badge) {
    badge.remove();
  }
}

export function logEvent({ message, kind = "system", severity = "info" } = {}) {
  const list = document.getElementById("logList");
  if (!list) {
    // No log list (e.g. simple page) — still surface to action centre.
    updateActionCenter({
      title: `${(kind || "system").toUpperCase()} Update`,
      message: safeText(message),
      severity: severity === "error" ? "error" : severity === "warn" ? "warn" : "info",
    });
    return;
  }
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
  updateActivityBadge();
}

export function statusClass(status) {
  const s = (status || "").toLowerCase();
  if (["executed", "approved", "connected", "ok"].includes(s)) return "pill good";
  if (["failed", "rejected", "expired", "disconnected", "fail"].includes(s)) return "pill bad";
  if (["pending", "degraded", "warn"].includes(s)) return "pill warn";
  if (["info"].includes(s)) return "pill info";
  return "pill neutral";
}

export function sentimentTagClass(tag) {
  const t = String(tag || "").toUpperCase();
  if (t.includes("BULLISH")) return "pill good";
  if (t.includes("BEARISH")) return "pill bad";
  return "pill neutral";
}

export function healthBadgeClass(ok) {
  return ok ? "health-badge bg-green-900" : "health-badge bg-red-900";
}

export function setStatusPill(el, label) {
  if (!el) return;
  const status = (label || "").toLowerCase();
  el.className = statusClass(status);
  const dotClass = status.includes("connect")
    ? "good"
    : status.includes("disconnect")
      ? "bad"
      : "warn";
  el.innerHTML = `<span class="status-dot ${dotClass}"></span>${safeText(label)}`;
}

/** Diagnostics keys -> human labels. Used by `buildDiagnosticsSummary` and
 * the scan diagnostics panel; lives here because it's read alongside the
 * other UI feedback helpers. */
export const DIAG_LABELS = {
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
