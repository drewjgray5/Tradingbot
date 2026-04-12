/**
 * Focused sign-in page: shares JWT localStorage key with the main dashboard (`tradingbot.jwt`).
 */
const AUTH_TOKEN_KEY = "tradingbot.jwt";
const LEGACY_AUTH_TOKEN_KEYS = ["supabasetoken", "supabaseToken", "supabase_token"];
const SUPABASE_ESM = "https://esm.sh/@supabase/supabase-js@2.49.1";

let supabaseClient = null;

function setMessage(text) {
  const el = document.getElementById("loginMessage");
  if (el) el.textContent = text || "";
}

function clearLegacyApiJwtKeys() {
  LEGACY_AUTH_TOKEN_KEYS.forEach((key) => localStorage.removeItem(key));
}

function readStoredApiJwt() {
  const current = (localStorage.getItem(AUTH_TOKEN_KEY) || "").trim();
  if (current) return current;
  for (const key of LEGACY_AUTH_TOKEN_KEYS) {
    const legacy = (localStorage.getItem(key) || "").trim();
    if (!legacy) continue;
    localStorage.setItem(AUTH_TOKEN_KEY, legacy);
    clearLegacyApiJwtKeys();
    return legacy;
  }
  return "";
}

function clearStoredApiJwt() {
  localStorage.removeItem(AUTH_TOKEN_KEY);
  clearLegacyApiJwtKeys();
}

async function createCookieSession(token) {
  if (!token) return;
  try {
    await fetch("/api/auth/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ access_token: token }),
    });
  } catch (e) {
    console.warn("auth cookie set failed", e);
  }
}

async function clearCookieSession() {
  try {
    await fetch("/api/auth/session", {
      method: "DELETE",
      credentials: "include",
    });
  } catch (e) {
    console.warn("auth cookie clear failed", e);
  }
}

function persistJwt(session) {
  if (session?.access_token) {
    localStorage.setItem(AUTH_TOKEN_KEY, session.access_token);
    clearLegacyApiJwtKeys();
    void createCookieSession(session.access_token);
    const inp = document.getElementById("loginJwt");
    if (inp) inp.value = "";
  }
}

function updateSbUi(session) {
  const out = document.getElementById("loginSbOut");
  const inn = document.getElementById("loginSbIn");
  const label = document.getElementById("loginSbLabel");
  if (!out || !inn) return;
  if (session?.user) {
    out.classList.add("hidden");
    inn.classList.remove("hidden");
    if (label) label.textContent = session.user.email || session.user.id || "Signed in";
    setMessage("You are signed in. Continue to the dashboard.");
  } else {
    inn.classList.add("hidden");
    out.classList.remove("hidden");
    if (label) label.textContent = "";
  }
}

async function initSupabase(url, anonKey) {
  let createClient;
  try {
    const mod = await import(SUPABASE_ESM);
    createClient = mod.createClient;
  } catch (e) {
    console.warn(e);
    setMessage("Could not load Supabase from CDN; paste a JWT below.");
    return;
  }
  supabaseClient = createClient(url, anonKey, {
    auth: { autoRefreshToken: true, persistSession: true, detectSessionInUrl: true },
  });
  const {
    data: { session },
  } = await supabaseClient.auth.getSession();
  persistJwt(session);
  updateSbUi(session);
  supabaseClient.auth.onAuthStateChange((_e, next) => {
    persistJwt(next);
    updateSbUi(next);
  });

  document.getElementById("loginSbSignIn")?.addEventListener("click", async () => {
    const email = document.getElementById("loginSbEmail")?.value?.trim() || "";
    const password = document.getElementById("loginSbPass")?.value || "";
    if (!email || !password) {
      setMessage("Enter email and password.");
      return;
    }
    const { error } = await supabaseClient.auth.signInWithPassword({ email, password });
    if (error) setMessage(error.message);
    else setMessage("Signed in.");
  });
  document.getElementById("loginSbSignUp")?.addEventListener("click", async () => {
    const email = document.getElementById("loginSbEmail")?.value?.trim() || "";
    const password = document.getElementById("loginSbPass")?.value || "";
    if (!email || !password) {
      setMessage("Enter email and password to sign up.");
      return;
    }
    const { error } = await supabaseClient.auth.signUp({ email, password });
    if (error) setMessage(error.message);
    else setMessage("Check email if confirmation is required, then sign in.");
  });
  document.getElementById("loginSbSignOut")?.addEventListener("click", async () => {
    await supabaseClient.auth.signOut();
    await clearCookieSession();
    clearStoredApiJwt();
    const inp = document.getElementById("loginJwt");
    if (inp) inp.value = "";
    setMessage("Signed out.");
  });
}

async function main() {
  const jwtInput = document.getElementById("loginJwt");
  const wrap = document.getElementById("loginSupabase");
  if (jwtInput) jwtInput.value = readStoredApiJwt();

  document.getElementById("loginJwtSave")?.addEventListener("click", () => {
    const val = jwtInput?.value?.trim() || "";
    if (val) {
      localStorage.setItem(AUTH_TOKEN_KEY, val);
      clearLegacyApiJwtKeys();
      void createCookieSession(val);
      setMessage("Token saved for this browser.");
    } else {
      void clearCookieSession();
      clearStoredApiJwt();
      setMessage("Token cleared.");
    }
  });

  try {
    const res = await fetch("/api/public-config", { headers: { Accept: "application/json" } });
    const body = res.ok ? await res.json() : {};
    const data = body?.data && typeof body.data === "object" ? body.data : {};
    const sb = data.supabase;
    if (sb?.url && sb?.anon_key) {
      wrap?.classList.remove("hidden");
      await initSupabase(sb.url, sb.anon_key);
    } else {
      wrap?.classList.add("hidden");
      setMessage("Paste a JWT and save, or use the dashboard if this host does not expose Supabase sign-in.");
    }
  } catch {
    setMessage("Could not load server config. You can still paste and save a JWT.");
  }
}

void main();
