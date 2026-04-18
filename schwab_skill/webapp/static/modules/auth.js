/**
 * Authentication helpers for the dashboard.
 *
 * Encapsulates:
 *  - JWT storage (manual paste, persisted via localStorage)
 *  - Cookie-session bridge (POST/GET/DELETE /api/auth/session)
 *  - Supabase client lifecycle (lazily ESM-imported on demand)
 *  - The `authSessionReady` promise that the bootstrap awaits before
 *    deciding whether to call protected routes.
 *
 * `app.js` owns the higher-level orchestration (`initSupabaseAuth` wires
 * the sign-in/out buttons and triggers `refreshAccountMe`); this module
 * only owns the primitives that are pure-ish and side-effect-isolated.
 */

import { state, AUTH_TOKEN_KEY, LEGACY_AUTH_TOKEN_KEYS } from "./state.js";
import { safeText } from "./format.js";

/** Reference to the lazily-loaded Supabase JS client. Read-only outside this
 * module; use `setSupabaseClient` to mutate. */
let supabaseClient = null;

export function setSupabaseClient(client) {
  supabaseClient = client;
}

export function getSupabaseClient() {
  return supabaseClient;
}

/** Set by /static/auth-jwt-utils.js (loaded before this file). */
const AuthJwt = (typeof window !== "undefined" && window.TradingBotAuthJwt) || {
  normalizeUserJwt(raw) {
    let t = String(raw ?? "").trim();
    if (/^bearer\s+/i.test(t)) t = t.replace(/^bearer\s+/i, "").trim();
    return t;
  },
  isProbablyAccessJwt() {
    return true;
  },
  JWT_BAD_SHAPE_HINT: "",
};

let _resolveAuthReady;
export const authSessionReady = new Promise((r) => {
  _resolveAuthReady = r;
});

export function markAuthReady() {
  if (_resolveAuthReady) {
    _resolveAuthReady();
    _resolveAuthReady = null;
  }
}

/** Trim and strip a leading "Bearer " if the user pasted a full Authorization value. */
export function normalizeUserJwt(raw) {
  return AuthJwt.normalizeUserJwt(raw);
}

/** Re-exposed helper (delegates to TradingBotAuthJwt) for callers that need
 * to validate a token shape outside of the storage helpers. */
export function isProbablyAccessJwt(token) {
  return AuthJwt.isProbablyAccessJwt(token);
}

/** Human-readable hint for an invalid JWT shape. Empty string when no hint
 * is configured. */
export const JWT_BAD_SHAPE_HINT = AuthJwt.JWT_BAD_SHAPE_HINT || "";

export async function getApiAccessToken() {
  if (state.allowManualJwt) {
    const manual = normalizeUserJwt(document.getElementById("jwtInput")?.value ?? "");
    if (manual) {
      if (!AuthJwt.isProbablyAccessJwt(manual)) {
        console.warn(AuthJwt.JWT_BAD_SHAPE_HINT);
        return "";
      }
      return manual;
    }
  }
  if (state.allowManualJwt) {
    const stored = readStoredApiJwt();
    if (stored) return stored;
  }
  if (state.config?.auth_mode === "supabase" && supabaseClient) {
    const { data, error } = await supabaseClient.auth.getSession();
    if (error) console.warn("auth.getSession", error);
    const sessionToken = normalizeUserJwt(data?.session?.access_token ?? "");
    if (sessionToken && AuthJwt.isProbablyAccessJwt(sessionToken)) return sessionToken;
  }
  if (await ensureCookieAuthSession()) return "";
  return "";
}

export function clearLegacyApiJwtKeys() {
  if (typeof AuthJwt.clearLegacyApiJwtKeys === "function") {
    AuthJwt.clearLegacyApiJwtKeys(localStorage, LEGACY_AUTH_TOKEN_KEYS);
    return;
  }
  LEGACY_AUTH_TOKEN_KEYS.forEach((key) => localStorage.removeItem(key));
}

export function readStoredApiJwt() {
  if (typeof AuthJwt.readStoredApiJwt === "function") {
    return AuthJwt.readStoredApiJwt({
      storage: localStorage,
      authTokenKey: AUTH_TOKEN_KEY,
      legacyAuthTokenKeys: LEGACY_AUTH_TOKEN_KEYS,
      normalizeUserJwt,
      isProbablyAccessJwt: AuthJwt.isProbablyAccessJwt,
      jwtBadShapeHint: AuthJwt.JWT_BAD_SHAPE_HINT,
    });
  }
  const n = normalizeUserJwt(localStorage.getItem(AUTH_TOKEN_KEY) || "");
  return n && AuthJwt.isProbablyAccessJwt(n) ? n : "";
}

export function clearStoredApiJwt() {
  if (typeof AuthJwt.clearStoredApiJwt === "function") {
    AuthJwt.clearStoredApiJwt(localStorage, AUTH_TOKEN_KEY, LEGACY_AUTH_TOKEN_KEYS);
    return;
  }
  localStorage.removeItem(AUTH_TOKEN_KEY);
  clearLegacyApiJwtKeys();
}

export async function ensureCookieAuthSession() {
  try {
    const out = await fetch("/api/auth/session", {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!out.ok) return false;
    const body = await out.json();
    const data = body?.data && typeof body.data === "object" ? body.data : {};
    return Boolean(data.authenticated);
  } catch {
    return false;
  }
}

export async function createCookieAuthSession(accessToken) {
  const token = normalizeUserJwt(safeText(accessToken));
  if (!token || !AuthJwt.isProbablyAccessJwt(token)) return false;
  try {
    const out = await fetch("/api/auth/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ access_token: token }),
    });
    return out.ok;
  } catch {
    return false;
  }
}

export async function clearCookieAuthSession() {
  try {
    await fetch("/api/auth/session", {
      method: "DELETE",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
  } catch {
    /* ignore */
  }
}

export function persistApiJwtFromSession(session) {
  const at = normalizeUserJwt(session?.access_token ?? "");
  if (at && AuthJwt.isProbablyAccessJwt(at)) {
    if (state.allowManualJwt) {
      localStorage.setItem(AUTH_TOKEN_KEY, at);
      clearLegacyApiJwtKeys();
    }
    void createCookieAuthSession(at);
    const inp = document.getElementById("jwtInput");
    if (inp) inp.value = "";
  }
}

export function updateSupabaseAuthUI(session) {
  const out = document.getElementById("supabaseSignedOut");
  const inn = document.getElementById("supabaseSignedIn");
  const label = document.getElementById("supabaseUserLabel");
  if (!out || !inn) return;
  if (session?.user) {
    out.classList.add("hidden");
    inn.classList.remove("hidden");
    if (label) label.textContent = session.user.email || session.user.id || "Signed in";
  } else {
    inn.classList.add("hidden");
    out.classList.remove("hidden");
    if (label) label.textContent = "";
  }
}

/** Default ESM URL for the Supabase JS SDK. Importable so callers can override
 * it for tests or alternate CDNs. */
export const SUPABASE_ESM = "https://esm.sh/@supabase/supabase-js@2.49.1";
