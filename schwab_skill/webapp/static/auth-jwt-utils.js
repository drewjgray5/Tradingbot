/**
 * Shared helpers for Supabase access JWTs (browser + classic script bundles).
 * Loaded before app.js / login.js / simple.js.
 */
(function (w) {
  "use strict";

  const AUTH_TOKEN_KEY = "tradingbot.jwt";
  const LEGACY_AUTH_TOKEN_KEYS = ["supabasetoken", "supabaseToken", "supabase_token"];

  function normalizeUserJwt(raw) {
    let t = String(raw ?? "").trim();
    if (/^bearer\s+/i.test(t)) t = t.replace(/^bearer\s+/i, "").trim();
    return t;
  }

  function isProbablyAccessJwt(token) {
    if (!token || typeof token !== "string") return false;
    const parts = token.split(".");
    return parts.length === 3 && parts.every((p) => p.length > 0);
  }

  const JWT_BAD_SHAPE_HINT =
    "That value is not a Supabase access token. It must be one long string with two dots (three parts). Sign in with email/password, or paste the access token—not the refresh token or anon key.";

  function clearLegacyApiJwtKeys(storage, legacyKeys) {
    const s = storage || w.localStorage;
    const keys = Array.isArray(legacyKeys) && legacyKeys.length ? legacyKeys : LEGACY_AUTH_TOKEN_KEYS;
    keys.forEach((key) => s?.removeItem?.(key));
  }

  function clearStoredApiJwt(storage, authTokenKey, legacyKeys) {
    const s = storage || w.localStorage;
    const currentKey = authTokenKey || AUTH_TOKEN_KEY;
    s?.removeItem?.(currentKey);
    clearLegacyApiJwtKeys(s, legacyKeys);
  }

  function readStoredApiJwt(options = {}) {
    const storage = options.storage || w.localStorage;
    const authTokenKey = options.authTokenKey || AUTH_TOKEN_KEY;
    const legacyKeys =
      Array.isArray(options.legacyAuthTokenKeys) && options.legacyAuthTokenKeys.length
        ? options.legacyAuthTokenKeys
        : LEGACY_AUTH_TOKEN_KEYS;
    const normalize = options.normalizeUserJwt || normalizeUserJwt;
    const isAccessJwt = options.isProbablyAccessJwt || isProbablyAccessJwt;
    const badShapeHint = options.jwtBadShapeHint || JWT_BAD_SHAPE_HINT;
    const onInvalidToken = typeof options.onInvalidToken === "function" ? options.onInvalidToken : null;

    const accept = (raw) => {
      const token = normalize(raw);
      if (!token) return "";
      if (!isAccessJwt(token)) {
        if (badShapeHint) console.warn(badShapeHint);
        clearStoredApiJwt(storage, authTokenKey, legacyKeys);
        if (onInvalidToken) onInvalidToken();
        return "";
      }
      return token;
    };

    const current = accept(storage?.getItem?.(authTokenKey) || "");
    if (current) return current;

    for (const key of legacyKeys) {
      const legacy = (storage?.getItem?.(key) || "").trim();
      if (!legacy) continue;
      const migrated = accept(legacy);
      if (!migrated) continue;
      storage?.setItem?.(authTokenKey, migrated);
      clearLegacyApiJwtKeys(storage, legacyKeys);
      return migrated;
    }
    return "";
  }

  w.TradingBotAuthJwt = {
    AUTH_TOKEN_KEY,
    LEGACY_AUTH_TOKEN_KEYS,
    normalizeUserJwt,
    isProbablyAccessJwt,
    JWT_BAD_SHAPE_HINT,
    clearLegacyApiJwtKeys,
    clearStoredApiJwt,
    readStoredApiJwt,
  };
})(typeof window !== "undefined" ? window : globalThis);
