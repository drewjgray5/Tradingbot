/**
 * Cmd-K command palette.
 *
 * Action callbacks (`runLazyApi`, `applyDisplayMode`, `openTradeDrawer`)
 * are injected by the caller so this module stays decoupled from the
 * larger `app.js` graph.
 *
 * `setupCommandPalette({ runLazyApi, applyDisplayMode, openTradeDrawer })`
 * must be called once at bootstrap. Without the deps it falls back to
 * no-ops, which keeps the palette navigable but disables the lazy-loaded
 * section jumps and drawer entry points.
 */

import { safeText } from "./format.js";

let _actions = [];

function buildActions({ runLazyApi, applyDisplayMode, openTradeDrawer }) {
  const lazyJump = (key, sectionId) => () => {
    if (typeof runLazyApi === "function") runLazyApi(key);
    document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth" });
  };
  const setDisplayMode = (mode) => () => {
    if (typeof applyDisplayMode === "function") applyDisplayMode(mode);
    const sel = document.getElementById("displayModeSelect");
    if (sel) sel.value = mode;
  };
  return [
    { id: "scan", label: "Run Scan", shortcut: "S", icon: "search", action: () => document.getElementById("scanBtn")?.click() },
    { id: "refresh", label: "Refresh All", shortcut: "R", icon: "refresh", action: () => document.getElementById("refreshBtn")?.click() },
    { id: "ticker", label: "Quick Ticker Check", shortcut: "T", icon: "chart", action: () => { document.getElementById("tickerInput")?.focus(); document.getElementById("quickCheckSection")?.scrollIntoView({ behavior: "smooth" }); } },
    { id: "pending", label: "Go to Pending Trades", icon: "list", action: () => document.getElementById("pendingSection")?.scrollIntoView({ behavior: "smooth" }) },
    { id: "portfolio", label: "Go to Portfolio", icon: "wallet", action: lazyJump("portfolio", "portfolioSection") },
    { id: "sectors", label: "Go to Sectors", icon: "grid", action: lazyJump("sectors", "sectorsSection") },
    { id: "backtest", label: "Go to Backtests", icon: "clock", action: lazyJump("backtest", "backtestSection") },
    { id: "performance", label: "Go to Performance", icon: "trending", action: lazyJump("performance", "performanceSection") },
    { id: "onboarding", label: "Go to Setup / Onboarding", icon: "settings", action: lazyJump("onboarding", "onboardingSection") },
    { id: "calibration", label: "Go to Calibration", icon: "tune", action: lazyJump("calibration", "calibrationSection") },
    { id: "sec", label: "SEC Filing Compare", icon: "file", action: () => document.getElementById("secCompareSection")?.scrollIntoView({ behavior: "smooth" }) },
    { id: "report", label: "Full Report", icon: "doc", action: () => document.getElementById("fullReportSection")?.scrollIntoView({ behavior: "smooth" }) },
    { id: "decision", label: "Decision Card (drawer)", icon: "card", action: () => (typeof openTradeDrawer === "function" ? openTradeDrawer({ tab: "decision" }) : document.getElementById("decisionSection")?.scrollIntoView({ behavior: "smooth" })) },
    { id: "recovery", label: "Failure Recovery (drawer)", icon: "first-aid", action: () => (typeof openTradeDrawer === "function" ? openTradeDrawer({ tab: "recovery" }) : document.getElementById("recoverySection")?.scrollIntoView({ behavior: "smooth" })) },
    { id: "profiles", label: "Strategy Presets", icon: "sliders", action: lazyJump("profiles", "presetsSection") },
    { id: "simple-view", label: "Switch to Simple view", icon: "eye", action: setDisplayMode("simple") },
    { id: "standard-view", label: "Switch to Standard view", icon: "eye", action: setDisplayMode("standard") },
    { id: "pro-view", label: "Switch to Pro view", icon: "eye", action: setDisplayMode("pro") },
    { id: "simple-page", label: "Open Simple Scan Page", icon: "external", action: () => { window.location.href = "/simple"; } },
    { id: "login", label: "Open Sign In Page", icon: "key", action: () => { window.location.href = "/login"; } },
    { id: "top", label: "Scroll to Top", icon: "arrow-up", action: () => window.scrollTo({ top: 0, behavior: "smooth" }) },
  ];
}

export function openCommandPalette() {
  const dialog = document.getElementById("cmdPaletteDialog");
  if (!dialog) return;
  dialog.classList.add("open");
  const input = document.getElementById("cmdPaletteInput");
  if (input) { input.value = ""; input.focus(); }
  renderCommandResults("");
}

export function closeCommandPalette() {
  const dialog = document.getElementById("cmdPaletteDialog");
  if (dialog) dialog.classList.remove("open");
}

export function renderCommandResults(query) {
  const list = document.getElementById("cmdPaletteList");
  if (!list) return;
  const q = query.trim().toLowerCase();
  const filtered = q
    ? _actions.filter((a) => a.label.toLowerCase().includes(q) || a.id.includes(q))
    : _actions;
  list.innerHTML = filtered
    .map(
      (a, i) =>
        `<button class="cmd-palette-item${i === 0 ? " selected" : ""}" data-idx="${i}" type="button">
          <span class="cmd-palette-label">${safeText(a.label)}</span>
          ${a.shortcut ? `<kbd class="cmd-palette-kbd">${safeText(a.shortcut)}</kbd>` : ""}
        </button>`
    )
    .join("");
  list.querySelectorAll(".cmd-palette-item").forEach((btn, idx) => {
    btn.addEventListener("click", () => {
      closeCommandPalette();
      filtered[idx]?.action();
    });
    btn.addEventListener("mouseenter", () => {
      list.querySelectorAll(".cmd-palette-item").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
    });
  });
}

export function setupCommandPalette(deps = {}) {
  _actions = buildActions(deps);
  const input = document.getElementById("cmdPaletteInput");
  if (!input) return;
  input.addEventListener("input", () => renderCommandResults(input.value));
  input.addEventListener("keydown", (e) => {
    const list = document.getElementById("cmdPaletteList");
    const items = list ? Array.from(list.querySelectorAll(".cmd-palette-item")) : [];
    const cur = items.findIndex((b) => b.classList.contains("selected"));
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = Math.min(cur + 1, items.length - 1);
      items.forEach((b) => b.classList.remove("selected"));
      items[next]?.classList.add("selected");
      items[next]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const prev = Math.max(cur - 1, 0);
      items.forEach((b) => b.classList.remove("selected"));
      items[prev]?.classList.add("selected");
      items[prev]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      const sel = items[cur >= 0 ? cur : 0];
      if (sel) sel.click();
    } else if (e.key === "Escape") {
      e.preventDefault();
      closeCommandPalette();
    }
  });
  document.getElementById("cmdPaletteDialog")?.addEventListener("click", (e) => {
    if (e.target.id === "cmdPaletteDialog") closeCommandPalette();
  });
}
