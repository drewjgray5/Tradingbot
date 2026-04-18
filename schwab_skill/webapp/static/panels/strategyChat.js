/**
 * Strategy chat panel — the conversational frontend that lets users
 * describe a backtest in natural language. Owns the message bubble
 * renderer, the "queued" callout that appears once a chat tool result
 * contains a `queue_backtest` task id, and the `sendStrategyChat`
 * round-trip against `/api/strategy-chat`.
 *
 * `switchBacktestHubTab` and `refreshBacktestRuns` live on the
 * backtest panel module; they're injected to avoid pulling that
 * module into strategyChat (and to keep them swappable in tests).
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { safeText, prettyJson } from "../modules/format.js";
import { logEvent } from "../modules/logger.js";

export function strategyChatPayloadMessages() {
  return (Array.isArray(state.strategyChatMessages) ? state.strategyChatMessages : [])
    .filter((m) => m && (m.role === "user" || m.role === "assistant"))
    .map((m) => ({ role: m.role, content: String(m.content ?? "") }));
}

export function scrollStrategyChatToEnd() {
  const el = document.getElementById("scMessages");
  if (el) el.scrollTop = el.scrollHeight;
}

export function renderStrategyChatMessages() {
  const el = document.getElementById("scMessages");
  const chips = document.getElementById("scEmptyChips");
  if (!el) return;
  const msgs = Array.isArray(state.strategyChatMessages) ? state.strategyChatMessages : [];
  el.innerHTML = "";
  if (!msgs.length) {
    const hint = document.createElement("div");
    hint.className = "chat-empty-hint";
    hint.textContent = "Describe the universe, date range, and any rule tweaks. Examples below.";
    el.appendChild(hint);
    if (chips) chips.classList.remove("hidden");
    return;
  }
  if (chips) chips.classList.add("hidden");
  msgs.forEach((m) => {
    const wrap = document.createElement("div");
    const role = m.role === "user" ? "user" : "assistant";
    wrap.className = `chat-bubble chat-bubble-${role}`;
    const roleEl = document.createElement("div");
    roleEl.className = "chat-bubble-role";
    roleEl.textContent = role === "user" ? "You" : "Assistant";
    wrap.appendChild(roleEl);
    const body = document.createElement("div");
    body.textContent = m.content != null ? String(m.content) : "";
    wrap.appendChild(body);
    if (role === "assistant" && Array.isArray(m.toolResults) && m.toolResults.length) {
      const det = document.createElement("details");
      det.className = "chat-tool-details";
      const sum = document.createElement("summary");
      sum.textContent = "Tool calls & raw results";
      det.appendChild(sum);
      const pre = document.createElement("pre");
      pre.className = "code-block";
      pre.textContent = prettyJson(m.toolResults);
      det.appendChild(pre);
      wrap.appendChild(det);
    }
    el.appendChild(wrap);
  });
  scrollStrategyChatToEnd();
}

export function hideScQueueCallout() {
  const c = document.getElementById("scQueueCallout");
  if (c) {
    c.classList.add("hidden");
    c.innerHTML = "";
  }
}

export function showScQueueCallout(taskId, runId, { switchBacktestHubTab = () => {} } = {}) {
  const c = document.getElementById("scQueueCallout");
  if (!c || !taskId) return;
  const tid = safeText(taskId);
  const rid = runId ? safeText(runId) : "";
  c.classList.remove("hidden");
  c.innerHTML = `
    <strong>Backtest queued.</strong> It runs in the background (often a few minutes). Results appear in <strong>Recent runs</strong> below when finished.
    <div class="callout-actions">
      <code id="scTaskIdCopy">${tid}</code>
      <button type="button" class="btn small secondary" id="scCopyTaskBtn">Copy task id</button>
      <button type="button" class="btn small secondary" id="scSwitchFormTabBtn">Open form tab</button>
    </div>
    ${rid ? `<div class="muted" style="margin-top:8px;font-size:0.82rem">Run id: ${rid.slice(0, 12)}…</div>` : ""}
  `;
  document.getElementById("scCopyTaskBtn")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(tid);
      logEvent({ kind: "system", severity: "info", message: "Task id copied." });
    } catch {
      logEvent({ kind: "system", severity: "warn", message: "Could not copy task id." });
    }
  });
  document.getElementById("scSwitchFormTabBtn")?.addEventListener("click", () => switchBacktestHubTab("form"));
}

export async function sendStrategyChat({ refreshBacktestRuns = async () => {}, switchBacktestHubTab = () => {} } = {}) {
  if (state.strategyChatBusy) return;
  const input = document.getElementById("scInput");
  const text = input?.value?.trim() || "";
  if (!text) return;
  if (!Array.isArray(state.strategyChatMessages)) state.strategyChatMessages = [];
  hideScQueueCallout();
  state.strategyChatMessages.push({ role: "user", content: text });
  input.value = "";
  renderStrategyChatMessages();
  state.strategyChatBusy = true;
  const sendBtn = document.getElementById("scSendBtn");
  if (sendBtn) sendBtn.disabled = true;
  try {
    const out = await api.post("/api/strategy-chat", { messages: strategyChatPayloadMessages() }, { timeoutMs: 180000 });
    if (!out.ok) {
      logEvent({ kind: "system", severity: "error", message: `Strategy chat: ${out.error}` });
      state.strategyChatMessages.push({ role: "assistant", content: `Error: ${out.error}` });
      renderStrategyChatMessages();
      return;
    }
    const assistant = out.data?.message || "";
    const tools = out.data?.tool_results;
    state.strategyChatMessages.push({
      role: "assistant",
      content: assistant || "(empty reply)",
      toolResults: Array.isArray(tools) && tools.length ? tools : null,
    });
    if (Array.isArray(tools)) {
      for (const t of tools) {
        if (t && t.tool === "queue_backtest" && t.result && t.result.task_id) {
          showScQueueCallout(t.result.task_id, t.result.run_id, { switchBacktestHubTab });
          break;
        }
      }
    }
    renderStrategyChatMessages();
    await refreshBacktestRuns();
  } finally {
    state.strategyChatBusy = false;
    if (sendBtn) sendBtn.disabled = false;
  }
}
