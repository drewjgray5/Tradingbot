/**
 * Tiny client-side router for the dashboard.
 *
 * The dashboard is a single HTML page with anchored sections rather than a
 * full SPA, so the "router" surface is small:
 *
 *   1. `handleRouteHash`              — react to ``window.location.hash`` by
 *                                       opening any ancestor <details> and
 *                                       smooth-scrolling the target into view.
 *   2. `applyQuerySectionDeepLink`    — translate ``?section=foo`` (the
 *                                       human-friendly query param links we
 *                                       hand out in emails / docs) into the
 *                                       canonical ``#fooSection`` hash via
 *                                       ``history.replaceState`` so refresh /
 *                                       back-button keep working.
 *   3. `clearOAuthQueryParams`        — strip the Schwab OAuth callback
 *                                       params off the URL once we've consumed
 *                                       them, again via ``replaceState`` so
 *                                       no history entry is added.
 *   4. `installRouter`                — wire the ``hashchange`` listener and
 *                                       run the initial pass. Returns an
 *                                       uninstall fn for tests / hot-reload.
 *
 * Pulled out of ``app.js`` so the entry point is shorter and the alias map
 * is easy to unit-test in isolation. See [[static-module-layout]] in the
 * wiki for the broader breakup.
 */

/**
 * Default short-form ``?section=…`` aliases. Keep keys lowercase; values are
 * the actual element ``id`` to anchor to. New aliases land here so docs /
 * emails can keep using friendly names without callers having to know the
 * DOM id.
 */
export const SECTION_ALIASES = Object.freeze({
  backtest: "backtestSection",
  backtests: "backtestSection",
  pending: "pendingSection",
  trades: "pendingSection",
  scan: "workflowPrimary",
  workflow: "workflowPrimary",
  connect: "onboardingSection",
  onboarding: "onboardingSection",
  setup: "onboardingSection",
});

/**
 * Walk up from ``el`` and force-open any ancestor ``<details>``. Without
 * this, deep-linking into a collapsed section would scroll to a hidden
 * element and the user would just see whitespace.
 */
function openAncestorDetails(el) {
  let node = el?.parentElement || null;
  while (node) {
    if (node.tagName === "DETAILS") {
      node.open = true;
    }
    node = node.parentElement;
  }
}

/**
 * Resolve the current ``location.hash`` to an element and scroll to it.
 * No-op when:
 *   • there is no hash,
 *   • the target element does not exist (stale hash from a removed section),
 *   • the document doesn't have ``getElementById`` (jsdom-less test env).
 *
 * Smooth-scrolls in a ``requestAnimationFrame`` to ensure layout settles
 * after we open ancestor details, and to avoid jank on first paint.
 */
export function handleRouteHash() {
  if (typeof window === "undefined" || !window.document) return;
  const id = (window.location?.hash || "").slice(1);
  if (!id) return;
  const el = window.document.getElementById?.(id);
  if (!el) return;
  openAncestorDetails(el);
  const raf = window.requestAnimationFrame || ((cb) => setTimeout(cb, 0));
  raf(() => {
    if (typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
}

/**
 * Resolve a raw ``?section=value`` to a DOM ``id``, applying ``aliases``.
 * Exported separately from ``applyQuerySectionDeepLink`` so unit tests can
 * exercise the alias / passthrough rules without a window.
 */
export function resolveSectionAlias(value, aliases = SECTION_ALIASES) {
  const key = String(value || "").trim().toLowerCase();
  if (!key) return "";
  return aliases[key] || String(value).trim();
}

/**
 * If the current URL has ``?section=foo``, translate it to the canonical
 * ``#fooSection`` hash and rewrite the URL via ``history.replaceState`` so
 * the original query param doesn't linger in the address bar (and back-stack
 * stays clean — we replace, not push).
 *
 * Returns the resolved id (or ``""`` if nothing was rewritten) so callers /
 * tests can chain assertions.
 */
export function applyQuerySectionDeepLink(aliases = SECTION_ALIASES) {
  if (typeof window === "undefined" || !window.location) return "";
  try {
    const u = new URL(window.location.href);
    const raw = (u.searchParams.get("section") || "").trim();
    if (!raw) return "";
    const id = resolveSectionAlias(raw, aliases);
    if (!id) return "";
    if (!window.document?.getElementById?.(id)) return "";
    u.searchParams.delete("section");
    const q = u.searchParams.toString();
    window.history.replaceState({}, "", `${u.pathname}${q ? `?${q}` : ""}#${id}`);
    return id;
  } catch (_err) {
    // URL parsing or replaceState failed — be silent so a stale URL never
    // breaks page load. Leaving the param in place is acceptable degraded
    // behaviour.
    return "";
  }
}

/**
 * Drop the named query params from the current URL via
 * ``history.replaceState``. Used by the OAuth-callback cleanup path so we
 * remove ``?schwab_oauth=ok&message=…`` once we've shown the toast.
 *
 * Returns ``true`` when at least one param was removed.
 */
export function clearOAuthQueryParams(keys) {
  if (typeof window === "undefined" || !window.location) return false;
  const list = Array.isArray(keys) ? keys : [keys];
  try {
    const u = new URL(window.location.href);
    let removed = false;
    for (const k of list) {
      if (u.searchParams.has(k)) {
        u.searchParams.delete(k);
        removed = true;
      }
    }
    if (!removed) return false;
    const search = u.searchParams.toString();
    window.history.replaceState(
      {},
      "",
      `${u.pathname}${search ? `?${search}` : ""}${u.hash || ""}`,
    );
    return true;
  } catch (_err) {
    return false;
  }
}

/**
 * Wire the ``hashchange`` listener and run the initial deep-link pass.
 *
 * Called once from ``app.js`` during boot. The returned ``uninstall``
 * function detaches the listener — used by tests and any future hot-reload
 * setup; production code never calls it.
 */
export function installRouter({ aliases = SECTION_ALIASES } = {}) {
  if (typeof window === "undefined") {
    return () => {};
  }
  const onHashChange = () => handleRouteHash();
  window.addEventListener("hashchange", onHashChange);
  applyQuerySectionDeepLink(aliases);
  handleRouteHash();
  return () => window.removeEventListener("hashchange", onHashChange);
}
