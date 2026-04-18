/**
 * Pure formatting helpers. No DOM, no API calls, no module state.
 *
 * These are referenced from virtually every render function in the dashboard,
 * so the contract here is *do not introduce side effects*. If you need to
 * touch the DOM, put it in a panel module instead.
 *
 * Every function in this file is a literal extraction from the legacy
 * `app.js`; behaviour is byte-identical.
 */

export function safeText(value) {
  if (value === null || value === undefined) return "—";
  return String(value);
}

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function safeNum(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

export function prettyJson(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function formatMoney(value) {
  return `$${safeNum(value, 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

/** Format a fraction (0.42) as "42.0%". */
export function pct(value, digits = 1) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

/** Backtest metrics from API are already in percent points (e.g. 55.2 => 55.2%). */
export function formatPercentPoints(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

export function clampPct(v) {
  return Math.max(0, Math.min(100, safeNum(v, 0)));
}

/** Translate a 0–100 score into a coarse verdict label. */
export function verdictFromScore(score, high = 70, low = 45) {
  const n = safeNum(score, 0);
  if (n >= high) return "bullish";
  if (n <= low) return "bearish";
  return "neutral";
}

export function timeAgo(iso) {
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

export function durationSec(startIso, endIso) {
  const start = Date.parse(startIso || "");
  const end = Date.parse(endIso || "");
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  return Math.max(0, Math.floor((end - start) / 1000));
}
