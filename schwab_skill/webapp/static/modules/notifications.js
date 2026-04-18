/**
 * Toasts and the persistent "notification center" panel.
 *
 * Two surfaces with one storage:
 *  - `showToast(msg, type, duration)` — transient, click-to-dismiss, no persist.
 *  - `addNotification(msg, severity)` — persisted to localStorage, displayed
 *    in the bell-icon dropdown panel; bumps the unread badge.
 *
 * `setupNotifications()` must be called once after DOMContentLoaded so the
 * bell click opens/closes the panel and outside-click closes it.
 */

import { safeText } from "./format.js";
import { NOTIF_STORAGE_KEY } from "./state.js";

export function showToast(message, type = "info", duration = 4000) {
  const container = document.getElementById("toastContainer");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span class="toast-dot"></span><span>${message}</span>`;
  container.appendChild(toast);
  const dismiss = () => {
    toast.classList.add("exiting");
    toast.addEventListener("animationend", () => toast.remove(), { once: true });
  };
  toast.addEventListener("click", dismiss);
  if (duration > 0) setTimeout(dismiss, duration);
}

const _notifications = [];

function loadStoredNotifications() {
  try {
    const raw = localStorage.getItem(NOTIF_STORAGE_KEY);
    if (raw) {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) _notifications.push(...arr.slice(-50));
    }
  } catch { /* ignore */ }
}

function saveNotifications() {
  try {
    localStorage.setItem(NOTIF_STORAGE_KEY, JSON.stringify(_notifications.slice(-50)));
  } catch { /* ignore */ }
}

export function addNotification(message, severity = "info") {
  _notifications.push({
    message,
    severity,
    time: new Date().toISOString(),
    read: false,
  });
  if (_notifications.length > 100) _notifications.splice(0, _notifications.length - 100);
  saveNotifications();
  renderNotifications();
}

export function renderNotifications() {
  const badge = document.getElementById("notifBadge");
  const list = document.getElementById("notifList");
  if (!badge || !list) return;

  const unread = _notifications.filter((n) => !n.read).length;
  if (unread > 0) {
    badge.textContent = unread > 99 ? "99+" : String(unread);
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }

  if (!_notifications.length) {
    list.innerHTML = `<li class="muted">No notifications yet.</li>`;
    return;
  }

  list.innerHTML = _notifications
    .slice()
    .reverse()
    .slice(0, 30)
    .map((n) => {
      const dotClass = n.severity === "error" ? "notif-dot--bad" : n.severity === "success" ? "notif-dot--good" : "notif-dot--info";
      const timeStr = new Date(n.time).toLocaleTimeString();
      return `<li class="notif-item${n.read ? "" : " notif-unread"}">
        <span class="notif-dot ${dotClass}"></span>
        <span class="notif-msg">${safeText(n.message)}</span>
        <span class="notif-time muted">${safeText(timeStr)}</span>
      </li>`;
    })
    .join("");
}

export function clearNotifications() {
  _notifications.length = 0;
  saveNotifications();
  renderNotifications();
  const panel = document.getElementById("notifPanel");
  if (panel) panel.classList.add("hidden");
}

export function setupNotifications() {
  loadStoredNotifications();
  renderNotifications();

  document.getElementById("notifBellBtn")?.addEventListener("click", () => {
    const panel = document.getElementById("notifPanel");
    if (!panel) return;
    panel.classList.toggle("hidden");
    if (!panel.classList.contains("hidden")) {
      _notifications.forEach((n) => { n.read = true; });
      saveNotifications();
      renderNotifications();
    }
  });

  document.getElementById("notifClearBtn")?.addEventListener("click", clearNotifications);

  document.addEventListener("click", (e) => {
    const panel = document.getElementById("notifPanel");
    const bell = document.getElementById("notifBellBtn");
    if (panel && !panel.classList.contains("hidden") && !panel.contains(e.target) && !bell?.contains(e.target)) {
      panel.classList.add("hidden");
    }
  });
}
