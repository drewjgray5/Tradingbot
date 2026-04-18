/**
 * Two-factor auth panel + "enable live trading" toggle for SaaS accounts.
 *
 * `renderTwoFaPanel` rebuilds the panel HTML from `state.twoFaStatus`
 * and re-wires its inner buttons each render. `refreshTwoFaStatus` is a
 * no-op outside SaaS mode (keeps state.twoFaStatus null and renders the
 * disabled view).
 *
 * `submitEnableLiveTrading` is wired from the global "enable live"
 * dialog in `wireEvents`; it nudges `refreshAccountMe` and
 * `refreshPending` once the server confirms the toggle so the rest of
 * the dashboard reflects the new permission.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { safeText, formatMoney } from "../modules/format.js";
import { logEvent, updateActionCenter } from "../modules/logger.js";

export function renderTwoFaPanel() {
  const wrap = document.getElementById("twoFaPanel");
  if (!wrap) return;
  const st = state.twoFaStatus || {};
  const enabled = Boolean(st.enabled);
  const threshold = Number(st.high_value_threshold_usd || 0);
  const statusText = enabled
    ? "2FA is enabled for high-value approvals."
    : "2FA is currently disabled. High-value approvals will be blocked until enabled.";
  const thresholdText = threshold > 0 ? `Threshold: ${formatMoney(threshold)} notional.` : "Threshold: not configured.";
  wrap.innerHTML = `
    <div class="muted small">${safeText(statusText)} ${safeText(thresholdText)}</div>
    <div class="inline-form compact" style="margin-top: 0.5rem">
      <button id="twoFaSetupBtn" type="button" class="btn small secondary">Generate 2FA secret</button>
      <label class="field-label" for="twoFaCodeInput">TOTP code</label>
      <input id="twoFaCodeInput" type="text" inputmode="numeric" maxlength="8" placeholder="123456" />
      <button id="twoFaEnableBtn" type="button" class="btn small secondary">Enable 2FA</button>
    </div>
    <pre id="twoFaSetupOutput" class="code-block code-block--tight hidden" style="margin-top: 0.5rem"></pre>
  `;
  document.getElementById("twoFaSetupBtn")?.addEventListener("click", async () => {
    const out = await api.post("/api/security/2fa/setup", {});
    if (!out.ok) {
      updateActionCenter({ title: "2FA setup failed", message: safeText(out.error), severity: "error" });
      return;
    }
    const pre = document.getElementById("twoFaSetupOutput");
    if (pre) {
      pre.classList.remove("hidden");
      pre.textContent = `Secret: ${safeText(out.data?.secret)}\nAdd this to your authenticator app.\nURI:\n${safeText(out.data?.otpauth_uri)}`;
    }
    updateActionCenter({
      title: "2FA secret generated",
      message: "Add the secret in your authenticator app, then enter a code and click Enable 2FA.",
      severity: "info",
    });
  });
  document.getElementById("twoFaEnableBtn")?.addEventListener("click", async () => {
    const code = document.getElementById("twoFaCodeInput")?.value?.trim() || "";
    if (!code) {
      updateActionCenter({ title: "2FA code required", message: "Enter a current authenticator code.", severity: "warn" });
      return;
    }
    const out = await api.post("/api/security/2fa/enable", { otp_code: code });
    if (!out.ok) {
      updateActionCenter({ title: "2FA enable failed", message: safeText(out.error), severity: "error" });
      return;
    }
    updateActionCenter({ title: "2FA enabled", message: "High-value approvals now require OTP verification.", severity: "success" });
    await refreshTwoFaStatus();
  });
}

export async function refreshTwoFaStatus() {
  if (!state.publicConfig?.saas_mode) {
    state.twoFaStatus = null;
    renderTwoFaPanel();
    return;
  }
  const out = await api.get("/api/security/2fa/status");
  state.twoFaStatus = out.ok ? out.data || null : null;
  renderTwoFaPanel();
}

/**
 * Submit the "enable live trading" form for the current account. After a
 * successful flip we delegate back to the caller-provided refresh hooks
 * so the rest of the dashboard re-reads its state.
 *
 *   submitEnableLiveTrading({ refreshAccountMe, refreshPending })
 *
 * Both deps default to no-ops; the orchestrator (`app.js`) wires the
 * real implementations.
 */
export async function submitEnableLiveTrading({
  refreshAccountMe = async () => {},
  refreshPending = async () => {},
} = {}) {
  const ack = Boolean(document.getElementById("enableLiveRiskAck")?.checked);
  const phrase = document.getElementById("enableLiveTypedPhrase")?.value?.trim() || "";
  const out = await api.post("/api/settings/enable-live-trading", {
    risk_acknowledged: ack,
    typed_phrase: phrase,
  });
  if (!out.ok) {
    const msg = typeof out.error === "string" ? out.error : JSON.stringify(out.error || "Request failed");
    logEvent({ kind: "system", severity: "error", message: `Enable live trading failed: ${msg}` });
    updateActionCenter({ title: "Enable live trading", message: msg, severity: "error" });
    return;
  }
  logEvent({ kind: "system", severity: "info", message: "Live trading enabled for this account." });
  updateActionCenter({
    title: "Live trading enabled",
    message: "You can approve pending trades; type the ticker in the dialog to confirm each order.",
    severity: "success",
  });
  const phraseInput = document.getElementById("enableLiveTypedPhrase");
  if (phraseInput) phraseInput.value = "";
  await refreshAccountMe();
  await refreshPending();
}
