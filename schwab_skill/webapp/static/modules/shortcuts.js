/**
 * Global keyboard shortcuts. Decoupled from the rest of the dashboard:
 * the caller injects the side effects it wants the keys to trigger.
 *
 *   setupKeyboardShortcuts({
 *     openCommandPalette,
 *     closeCommandPalette,
 *     showToast,
 *     applyDisplayMode,
 *   })
 *
 * Shortcuts are no-op when the focus is in a form element (`INPUT`,
 * `TEXTAREA`, `SELECT`) or when a modifier other than Ctrl/Meta+K is held.
 */

export function setupKeyboardShortcuts({
  openCommandPalette,
  closeCommandPalette,
  showToast,
  applyDisplayMode,
} = {}) {
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "k") {
      e.preventDefault();
      const dialog = document.getElementById("cmdPaletteDialog");
      if (dialog?.classList.contains("open")) closeCommandPalette?.();
      else openCommandPalette?.();
      return;
    }

    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    switch (e.key) {
      case "r":
      case "R":
        e.preventDefault();
        document.getElementById("refreshBtn")?.click();
        showToast?.("Refreshing all data...", "info", 2000);
        break;
      case "s":
      case "S":
        e.preventDefault();
        document.getElementById("scanBtn")?.click();
        break;
      case "t":
      case "T":
        e.preventDefault();
        document.getElementById("tickerInput")?.focus();
        document.getElementById("quickCheckSection")?.scrollIntoView({ behavior: "smooth" });
        break;
      case "?":
        e.preventDefault();
        showToast?.("Shortcuts: Ctrl+K = Command palette, R = Refresh, S = Scan, T = Ticker, 1-3 = View", "info", 5000);
        break;
      case "1":
        e.preventDefault();
        applyDisplayMode?.("simple");
        document.getElementById("displayModeSelect").value = "simple";
        showToast?.("Switched to Simple view", "info", 2000);
        break;
      case "2":
        e.preventDefault();
        applyDisplayMode?.("standard");
        document.getElementById("displayModeSelect").value = "standard";
        showToast?.("Switched to Standard view", "info", 2000);
        break;
      case "3":
        e.preventDefault();
        applyDisplayMode?.("pro");
        document.getElementById("displayModeSelect").value = "pro";
        showToast?.("Switched to Pro view", "info", 2000);
        break;
    }
  });
}
