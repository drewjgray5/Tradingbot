/**
 * Profile / Preset panel.
 *
 * Exposes:
 *   - PRESET_SETTING_LABELS / presetSettingLabel: friendly labels for
 *     the env-key-style preset settings shown in the panel.
 *   - renderProfilePanel: paints the profile chips + the active-preset
 *     parameters table + the runtime-env table (expert mode).
 *   - renderPresetApplyPreview: paints the "if you apply now" diff
 *     between the saved preset and the currently-selected form values.
 *   - loadProfiles / applyProfile: GET/POST `/api/settings/profiles` and
 *     `/api/settings/profile`.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { safeText, prettyJson } from "../modules/format.js";
import { logEvent, updateActionCenter } from "../modules/logger.js";

export const PRESET_SETTING_LABELS = {
  POSITION_SIZE_USD: "Position size (USD)",
  MAX_TRADES_PER_DAY: "Max trades per day",
  QUALITY_GATES_MODE: "Quality gates",
  EVENT_RISK_MODE: "Event risk mode",
  EVENT_ACTION: "Event action",
  EXEC_QUALITY_MODE: "Execution quality mode",
};

export function presetSettingLabel(key) {
  return PRESET_SETTING_LABELS[key] || key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function renderProfilePanel(rootEl, data, { error } = {}) {
  const rawDetails = document.getElementById("profileRawDetails");
  const rawPre = document.getElementById("profileRaw");
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
    rootEl.innerHTML = `<div class="report-empty">No preset loaded.</div>`;
    return;
  }

  const profile = safeText(data.profile || "—");
  const mode = safeText(data.mode || "standard");
  const expertUi = mode === "expert";
  const autoOn = Boolean(data.automation_opt_in);
  const active = data.active_profile_settings && typeof data.active_profile_settings === "object" ? data.active_profile_settings : {};
  const keys = Object.keys(active).sort();
  const catalog = state.presetCatalog && typeof state.presetCatalog === "object" ? state.presetCatalog : {};
  const profileKey = String(data.profile || "").toLowerCase();
  const dispMap =
    catalog[profileKey] && catalog[profileKey].settings_display && typeof catalog[profileKey].settings_display === "object"
      ? catalog[profileKey].settings_display
      : {};

  const settingsRows = keys
    .map((k) => {
      const d = dispMap[k] && typeof dispMap[k] === "object" ? dispMap[k] : {};
      const label = safeText(d.label || presetSettingLabel(k));
      const plain = safeText(d.plain || active[k]);
      const raw = safeText(d.raw != null ? d.raw : active[k]);
      const valueCell = expertUi ? `${plain}<br/><code class="preset-value">${raw}</code>` : plain;
      return `<tr><th scope="row">${label}</th><td>${valueCell}</td></tr>`;
    })
    .join("");

  const expert = data.expert_runtime_overrides && typeof data.expert_runtime_overrides === "object" ? data.expert_runtime_overrides : null;
  let expertBlock = "";
  if (expert) {
    const ek = Object.keys(expert).sort();
    const expertRows = ek
      .map((k) => `<tr><th scope="row"><code>${safeText(k)}</code></th><td>${safeText(expert[k])}</td></tr>`)
      .join("");
    expertBlock = `
      <div class="preset-subsection preset-expert">
        <h3>Runtime env (read-only)</h3>
        <table class="preset-kv-table">
          <tbody>${expertRows || `<tr><td colspan="2" class="muted">No values</td></tr>`}</tbody>
        </table>
      </div>`;
  }

  rootEl.innerHTML = `
    <div class="preset-chips">
      <span class="preset-chip">Profile: ${profile}</span>
      <span class="preset-chip muted-chip">Mode: ${mode}</span>
      <span class="preset-chip ${autoOn ? "" : "muted-chip"}">${autoOn ? "Automation: on" : "Automation: off"}</span>
    </div>
    <div class="preset-subsection">
      <h3>Active preset parameters</h3>
      <table class="preset-kv-table">
        <tbody>${
          settingsRows || `<tr><td colspan="2" class="muted">No parameters in response.</td></tr>`
        }</tbody>
      </table>
    </div>
    ${expertBlock}
  `;
}

export function renderPresetApplyPreview() {
  const root = document.getElementById("presetApplyPreview");
  if (!root) return;
  const saved = state.savedUiSettings;
  const catalog = state.presetCatalog;
  if (!saved || !catalog || typeof catalog !== "object") {
    root.innerHTML = `<p class="muted small">Load presets to see a change summary.</p>`;
    return;
  }
  const selProfile = document.getElementById("profileSelect")?.value || saved.profile;
  const selMode = document.getElementById("settingsModeSelect")?.value || saved.mode;
  const selAuto = Boolean(document.getElementById("automationOptIn")?.checked);

  const cur = String(saved.profile || "balanced").toLowerCase();
  const next = String(selProfile || "balanced").toLowerCase();
  const curSet = catalog[cur]?.settings || {};
  const nextSet = catalog[next]?.settings || {};
  const keys = [...new Set([...Object.keys(curSet), ...Object.keys(nextSet)])].sort();

  const parts = [];
  if (next !== cur) {
    const blurb = safeText(catalog[next]?.blurb || "");
    parts.push(
      `<li><strong>Profile:</strong> ${safeText(cur)} → ${safeText(next)}.${blurb ? ` ${blurb}` : ""}</li>`
    );
  }
  keys.forEach((k) => {
    if (curSet[k] !== nextSet[k]) {
      const d0 = catalog[cur]?.settings_display?.[k] || {};
      const d1 = catalog[next]?.settings_display?.[k] || {};
      const label = safeText(d1.label || d0.label || presetSettingLabel(k));
      const fromPlain = safeText(d0.plain || curSet[k]);
      const toPlain = safeText(d1.plain || nextSet[k]);
      parts.push(`<li><strong>${label}:</strong> ${fromPlain} → ${toPlain}</li>`);
    }
  });
  if (String(selMode) !== String(saved.mode)) {
    const hint =
      selMode === "expert" ? "You will see raw env values under presets." : "Raw env values stay hidden.";
    parts.push(`<li><strong>Dashboard mode:</strong> ${safeText(saved.mode)} → ${safeText(selMode)}. ${hint}</li>`);
  }
  if (selAuto !== Boolean(saved.automation_opt_in)) {
    parts.push(
      `<li><strong>Automation opt-in (saved setting):</strong> ${saved.automation_opt_in ? "on" : "off"} → ${selAuto ? "on" : "off"}. When off, API clients must pass an explicit live-confirmation flag; this dashboard still makes you type the ticker to approve.</li>`
    );
  }

  if (!parts.length) {
    root.innerHTML = `<p class="muted preset-preview-none">No changes to apply.</p>`;
    return;
  }
  root.innerHTML = `<h3 class="preset-preview-title">If you apply now</h3><ul class="preset-preview-list">${parts.join("")}</ul>`;
}

export async function loadProfiles() {
  const mode = document.getElementById("settingsModeSelect")?.value || "standard";
  const expert = mode === "expert";
  const out = await api.get(`/api/settings/profiles?expert=${expert}`);
  const panel = document.getElementById("profilePanel");
  if (!panel) return;
  if (!out.ok) {
    renderProfilePanel(panel, null, { error: `Profile load failed: ${out.error}` });
    return;
  }
  state.profile = out.data;
  state.presetCatalog =
    out.data.preset_catalog && typeof out.data.preset_catalog === "object" ? out.data.preset_catalog : {};
  state.savedUiSettings = {
    profile: out.data.profile || "balanced",
    mode: out.data.mode || "standard",
    automation_opt_in: Boolean(out.data.automation_opt_in),
  };
  document.getElementById("profileSelect").value = out.data.profile || "balanced";
  document.getElementById("settingsModeSelect").value = out.data.mode || "standard";
  document.getElementById("automationOptIn").checked = Boolean(out.data.automation_opt_in);
  renderProfilePanel(panel, out.data);
  renderPresetApplyPreview();
}

export async function applyProfile() {
  const profile = document.getElementById("profileSelect").value;
  const mode = document.getElementById("settingsModeSelect").value;
  const automationOptIn = document.getElementById("automationOptIn").checked;
  const out = await api.post(`/api/settings/profile?profile=${encodeURIComponent(profile)}&mode=${encodeURIComponent(mode)}&automation_opt_in=${automationOptIn}`, {});
  const panel = document.getElementById("profilePanel");
  if (!out.ok) {
    if (panel) renderProfilePanel(panel, null, { error: `Apply preset failed: ${out.error}` });
    logEvent({ kind: "system", severity: "error", message: `Preset apply failed: ${out.error}` });
    return;
  }
  logEvent({
    kind: "system",
    severity: "info",
    message: `Preset: ${profile}, automation ${automationOptIn ? "on" : "off"}, ${mode} mode.`,
  });
  updateActionCenter({
    title: "Preset applied",
    message: `${profile} · ${mode} mode · automation ${automationOptIn ? "on" : "off"}`,
    severity: "success",
  });
  await loadProfiles();
}
