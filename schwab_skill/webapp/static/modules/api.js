/**
 * Authenticated JSON-over-HTTP client used by every UI panel.
 *
 * Wraps `fetch` with:
 *  - 90s default timeout (overridable via options.timeoutMs)
 *  - per-request `X-Request-ID` header for log correlation
 *  - bearer JWT (from `auth.getApiAccessToken`) when present
 *  - `X-API-Key` from localStorage when the public-config requires it
 *  - same-origin credentials so cookie sessions work
 *  - normalized `{ ok, data, error, status? }` return shape
 *
 * Always returns a resolved object (no throws) so callers can do
 * `if (!out.ok) showError(out.error)` without try/catch boilerplate.
 */

import { state } from "./state.js";
import { getApiAccessToken } from "./auth.js";

export const api = {
  async request(path, options = {}) {
    const timeoutMs = Number(options.timeoutMs || 90000);
    const fetchOptions = { ...options };
    delete fetchOptions.timeoutMs;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    const headers = {
      "Content-Type": "application/json",
      ...(fetchOptions.headers || {}),
    };
    if (!headers["X-Request-ID"]) {
      headers["X-Request-ID"] = `ui-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
    }

    const token = await getApiAccessToken();
    if (token) headers.Authorization = `Bearer ${token}`;

    const apiKey = state.publicConfig?.api_key_required ? (localStorage.getItem("tradingbot.api_key") || "") : "";
    if (apiKey) headers["X-API-Key"] = apiKey;

    try {
      const res = await fetch(path, {
        ...fetchOptions,
        credentials: fetchOptions.credentials ?? "same-origin",
        headers,
        signal: controller.signal,
      });
      const text = await res.text();
      let data;
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        data = { ok: false, error: `Invalid JSON response (${res.status})` };
      }
      if (!res.ok) {
        return {
          ok: false,
          error: data?.error || data?.detail || `HTTP ${res.status}`,
          status: res.status,
          data: data?.data ?? null,
        };
      }
      return data;
    } catch (err) {
      if (err?.name === "AbortError") return { ok: false, error: "Request timed out. Please retry." };
      return { ok: false, error: err?.message || "Request failed." };
    } finally {
      clearTimeout(timeout);
    }
  },

  get(path, options = {}) {
    return this.request(path, { method: "GET", ...options });
  },

  post(path, body = {}, options = {}) {
    return this.request(path, { method: "POST", body: JSON.stringify(body), ...options });
  },

  patch(path, body = {}, options = {}) {
    return this.request(path, { method: "PATCH", body: JSON.stringify(body), ...options });
  },
};
