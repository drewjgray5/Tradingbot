/**
 * Schwab onboarding wizard panel.
 *
 * Surfaces the five-step "connect account → connect market → verify
 * tokens → test scan → paper order" sequence as a horizontal stepper
 * with a single "Next step" CTA, plus a card grid of past attempts and
 * an advanced row of per-step buttons. Status is stored on
 * `state.onboarding`; `refreshOnboarding` pulls fresh data from
 * `/api/onboarding/status` and renders the connection meta line, the
 * stepper, the CTA, and the retrospective cards.
 *
 * `startOnboarding`, `runOnboardingStep`, and the new
 * `triggerSchwabOAuth` helper accept an injected `runLazyApi` so the
 * panel section is gated by the same lazy-load machinery in `app.js`.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { safeText, prettyJson } from "../modules/format.js";
import { logEvent, updateActionCenter } from "../modules/logger.js";

const STEP_NAMES = {
  connect: "Link Schwab",
  verify_token_health: "Verify Tokens",
  test_scan: "Test Scan",
  test_paper_order: "Paper Order",
};
const STEP_DESCS = {
  connect: "Token files exist for market & account sessions.",
  verify_token_health: "Live API check: market token, account token, and quote probe.",
  test_scan: "Run the signal scanner and confirm no fatal errors.",
  test_paper_order: "Shadow-mode order to verify execution path.",
};

/** Ordered list of steps shown in the visual stepper. */
const STEPPER_ORDER = [
  "account",
  "market",
  "verify_token_health",
  "test_scan",
  "test_paper_order",
];

const STEPPER_COPY = {
  account: {
    title: "Connect your Schwab brokerage account",
    desc: "Approve access for trading (balances, positions, orders). Opens Schwab in this tab.",
    cta: "Connect Schwab account",
  },
  market: {
    title: "Connect Schwab market data",
    desc: "Second approval for quotes and historical data. Required for scans.",
    cta: "Connect Schwab market",
  },
  verify_token_health: {
    title: "Verify your tokens are live",
    desc: "Quick API probe to confirm both Schwab tokens accept requests right now.",
    cta: "Verify tokens",
  },
  test_scan: {
    title: "Run a test scan",
    desc: "Scans the universe end-to-end and confirms no fatal errors.",
    cta: "Run test scan",
  },
  test_paper_order: {
    title: "Place a paper order",
    desc: "Shadow-mode order so we know the execution path is wired correctly.",
    cta: "Place paper order",
  },
  done: {
    title: "Setup complete — you're cleared to scan and trade.",
    desc: "All five steps passed. You can re-run any step from the “All steps” drawer below.",
    cta: "Re-verify tokens",
  },
};

/** Maps a derived current step → the action that completes it. */
function actionForStep(step, deps) {
  const { runLazyApi, runStep, oauthAccount, oauthMarket } = deps;
  switch (step) {
    case "account":
      return oauthAccount;
    case "market":
      return oauthMarket;
    case "verify_token_health":
    case "test_scan":
    case "test_paper_order":
      return () => runStep(step, { runLazyApi });
    case "done":
      return () => runStep("verify_token_health", { runLazyApi });
    default:
      return null;
  }
}

/**
 * Decide which step the user should tackle next.
 *
 * Account and market OAuth completion are derived from token presence
 * (`api_health.account_token_ok` / `market_token_ok`) since the
 * single-record `steps.connect` flag is set later by step 1's API call.
 */
function deriveCurrentStep(data) {
  const ah = data?.api_health || {};
  const steps = data?.steps || {};
  if (!ah.account_token_ok) return "account";
  if (!ah.market_token_ok) return "market";
  if (!steps.verify_token_health?.ok) return "verify_token_health";
  if (!steps.test_scan?.ok) return "test_scan";
  if (!steps.test_paper_order?.ok) return "test_paper_order";
  return "done";
}

function stepStatus(step, data) {
  const ah = data?.api_health || {};
  const steps = data?.steps || {};
  if (step === "account") return ah.account_token_ok ? "done" : "pending";
  if (step === "market") return ah.market_token_ok ? "done" : "pending";
  const s = steps[step] || {};
  if (s.ok) return "done";
  if (s.at) return "failed";
  return "pending";
}

function renderStepper(data, currentStep) {
  const stepper = document.getElementById("onboardingStepper");
  if (!stepper) return;
  for (const step of STEPPER_ORDER) {
    const li = stepper.querySelector(`li[data-step="${step}"]`);
    if (!li) continue;
    const status = stepStatus(step, data);
    const isCurrent = step === currentStep;
    li.dataset.status = status;
    li.classList.toggle("current", isCurrent && currentStep !== "done");
    li.classList.toggle("done", status === "done");
    li.classList.toggle("failed", status === "failed");
    const label = li.querySelector(".step-state");
    if (label) {
      label.textContent =
        status === "done" ? "done" : status === "failed" ? "retry" : isCurrent ? "next" : "pending";
    }
  }
  // Mark "done" by adding `complete` to the whole stepper for styling.
  stepper.classList.toggle("complete", currentStep === "done");
}

function renderNextCta(data, currentStep, deps) {
  const titleEl = document.getElementById("onboardingNextTitle");
  const descEl = document.getElementById("onboardingNextDesc");
  const btn = document.getElementById("onboardingNextBtn");
  if (!titleEl || !descEl || !btn) return;
  const copy = STEPPER_COPY[currentStep] || STEPPER_COPY.account;
  titleEl.textContent = copy.title;
  descEl.textContent = copy.desc;
  btn.textContent = copy.cta;

  const handler = actionForStep(currentStep, deps);
  // Replace listener (clone trick) so re-renders don't pile up listeners.
  const fresh = btn.cloneNode(true);
  btn.parentNode.replaceChild(fresh, btn);
  if (handler) {
    fresh.disabled = false;
    fresh.addEventListener("click", async (e) => {
      e.preventDefault();
      fresh.disabled = true;
      try {
        await handler();
      } finally {
        fresh.disabled = false;
      }
    });
  } else {
    fresh.disabled = true;
  }
}

export function renderOnboardingCards(data) {
  const cards = document.getElementById("onboardingCards");
  const det = document.getElementById("onboardingJsonDetails");
  const pre = document.getElementById("onboardingOutput");
  if (!cards) return;
  if (!data) {
    cards.innerHTML = `<p class="muted">Run the wizard or click individual steps above.</p>`;
    if (det) det.classList.add("hidden");
    return;
  }
  const steps = data.steps || {};
  let html = '<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px;">';
  for (const [key, label] of Object.entries(STEP_NAMES)) {
    const step = steps[key] || {};
    const ok = Boolean(step.ok);
    const borderColor = ok ? "rgba(52, 211, 153, 0.45)" : step.at ? "rgba(251, 113, 133, 0.45)" : "rgba(100, 116, 139, 0.35)";
    const bgColor = ok ? "rgba(6, 78, 59, 0.2)" : step.at ? "rgba(127, 29, 29, 0.15)" : "rgba(10, 16, 34, 0.6)";
    const statusPill = ok
      ? '<span class="pill good small">Pass</span>'
      : step.at ? '<span class="pill bad small">Fail</span>' : '<span class="pill neutral small">Not run</span>';
    const fixPath = step.fix_path ? `<p class="muted" style="font-size: 0.78rem; margin: 6px 0 0;">${safeText(step.fix_path)}</p>` : "";
    html += `<div style="border-radius: 12px; border: 1px solid ${borderColor}; background: ${bgColor}; padding: 12px;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
        <strong style="font-size: 0.88rem;">${label}</strong>
        ${statusPill}
      </div>
      <p class="muted" style="font-size: 0.8rem; margin: 0;">${STEP_DESCS[key]}</p>
      ${fixPath}
    </div>`;
  }
  html += "</div>";
  const elapsed = data.elapsed_minutes;
  const done = data.completed_under_target;
  if (elapsed != null) {
    html += `<p class="muted" style="margin-top: 10px;">Elapsed: ${elapsed} min${done ? ' · <span class="pill good small">Under target</span>' : ""}</p>`;
  }
  cards.innerHTML = html;
  if (det) det.classList.remove("hidden");
  if (pre) pre.textContent = prettyJson(data);
}

/**
 * Kick off the Schwab account OAuth flow. Exposed so the stepper CTA
 * and the legacy "Connect Schwab (account)" button share one path.
 */
export async function triggerSchwabAccountOAuth() {
  if (!state.publicConfig?.schwab_oauth) {
    logEvent({ kind: "system", severity: "warn", message: "Schwab OAuth is not configured on this server." });
    return;
  }
  const out = await api.get("/api/oauth/schwab/authorize-url");
  if (!out.ok || !out.data?.url) {
    logEvent({ kind: "system", severity: "error", message: out.error || "Could not start Schwab OAuth." });
    return;
  }
  window.location.href = out.data.url;
}

export async function triggerSchwabMarketOAuth() {
  if (!state.publicConfig?.schwab_market_oauth) {
    logEvent({
      kind: "system",
      severity: "warn",
      message: "Schwab market OAuth is not configured on this server.",
    });
    return;
  }
  const out = await api.get("/api/oauth/schwab/market/authorize-url");
  if (!out.ok || !out.data?.url) {
    logEvent({
      kind: "system",
      severity: "error",
      message: out.error || "Could not start Schwab market OAuth.",
    });
    return;
  }
  window.location.href = out.data.url;
}

export async function refreshOnboarding({ runLazyApi = async () => {} } = {}) {
  const out = await api.get("/api/onboarding/status");
  const meta = document.getElementById("onboardingMeta");
  const section = document.getElementById("onboardingSection");
  if (!meta) return;
  if (!out.ok) {
    renderOnboardingCards(null);
    renderStepper({}, "account");
    renderNextCta({}, "account", {
      runLazyApi,
      runStep: runOnboardingStep,
      oauthAccount: triggerSchwabAccountOAuth,
      oauthMarket: triggerSchwabMarketOAuth,
    });
    meta.textContent = `Onboarding status failed: ${out.error}`;
    return;
  }
  state.onboarding = out.data;
  if (section) section.style.display = "block";
  const conn = out.data?.connection_status || (out.data?.schwab_linked ? "connected" : "disconnected");
  const ah = out.data?.api_health || {};
  const apiLine = ah.schwab_linked
    ? `API: market ${ah.market_token_ok ? "ok" : "—"} · account ${ah.account_token_ok ? "ok" : "—"} · quotes ${ah.quote_ok ? "ok" : "—"}`
    : "API: connect Schwab to probe tokens and quotes.";
  const haltLine = state.publicConfig.platform_live_trading_kill_switch ? " · Global operator halt: ON" : "";
  meta.textContent = `Connection: ${conn} · ${apiLine}${haltLine}`;

  const currentStep = deriveCurrentStep(out.data);
  renderStepper(out.data, currentStep);
  renderNextCta(out.data, currentStep, {
    runLazyApi,
    runStep: runOnboardingStep,
    oauthAccount: triggerSchwabAccountOAuth,
    oauthMarket: triggerSchwabMarketOAuth,
  });
  renderOnboardingCards(out.data);
}

export async function startOnboarding({ runLazyApi = async () => {} } = {}) {
  await runLazyApi("onboarding");
  const out = await api.post("/api/onboarding/start", {});
  if (!out.ok) {
    logEvent({ kind: "system", severity: "error", message: `Onboarding start failed: ${out.error}` });
    renderOnboardingCards(null);
    updateActionCenter({ title: "Schwab setup", message: out.error || "Could not start onboarding.", severity: "error" });
    return;
  }
  logEvent({ kind: "system", severity: "info", message: "Setup wizard started." });
  await refreshOnboarding({ runLazyApi });
}

export async function runOnboardingStep(step, { runLazyApi = async () => {} } = {}) {
  await runLazyApi("onboarding");
  const out = await api.post(`/api/onboarding/step/${step}`, {});
  if (!out.ok) {
    logEvent({ kind: "system", severity: "error", message: `Onboarding step failed: ${out.error}` });
    updateActionCenter({ title: "Schwab setup", message: out.error || `Step ${step} failed.`, severity: "error" });
    return;
  }
  logEvent({ kind: "system", severity: "info", message: `Onboarding step complete: ${step}.` });
  await refreshOnboarding({ runLazyApi });
}
