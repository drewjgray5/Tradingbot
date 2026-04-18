---
source: schwab_skill/webapp/static/app.js
created: 2026-04-17
updated: 2026-04-17
tags: [frontend, refactor, modules, dashboard]
---

# Static Module Layout

> Map of the dashboard front-end after the `app.js` modularization. The
> entry point is loaded as `<script src="/static/app.js" type="module">`
> from `webapp/static/index.html`; everything else is reachable via
> ES `import`.

## Why this exists

`app.js` had grown to ~5,200 lines of mixed concerns: state, formatters,
HTTP client, JWT/cookie auth, Supabase glue, toasts, the notification
center, command palette, keyboard shortcuts, scroll-to-top, plus the
~3,500 lines of panel-render code. That made it brittle to change any one
piece without re-scanning the whole file.

Two passes have run so far:

1. **Foundations pass (~820 lines extracted)** moved the truly
   self-contained utilities into `webapp/static/modules/`.
2. **Panel-by-panel pass (~2,400 lines extracted)** moved every
   independent panel renderer into `webapp/static/panels/`.

`app.js` itself is still the orchestrator — it owns `wireEvents`, the
SSE wiring, the bootstrap IIFE, and the panels that are still tangled
with the wiring (scan, pending trades, diagnostics, account, status,
config, recent activity, the validation rail) — but it is now ~2,300
lines instead of ~5,200, and the heavy panels are independently
testable.

## File map

```
webapp/static/
├─ app.js                         orchestrator + remaining panel renderers (~2,300 lines)
├─ auth-jwt-utils.js              window.TradingBotAuthJwt helpers (loaded first)
├─ login.js / simple.js           independent page entry points
├─ modules/
│  ├─ state.js                    central `state` singleton + storage-key constants
│  ├─ format.js                   pure formatters (safeText, pct, timeAgo, …)
│  ├─ auth.js                     JWT storage, cookie session, supabase client lifecycle
│  ├─ api.js                      authenticated `api.get/post/patch` wrapper around fetch
│  ├─ notifications.js            toasts + persistent notification center
│  ├─ scrollToTop.js              floating "back to top" button
│  ├─ commandPalette.js           Cmd-K palette (action callbacks injected by app.js)
│  ├─ shortcuts.js                global keyboard shortcuts (callbacks injected by app.js)
│  └─ logger.js                   logEvent, action center, activity badge, status pills
└─ panels/
   ├─ twoFa.js                    2FA enable-live-trading panel
   ├─ onboarding.js               Schwab onboarding wizard (5-step stepper + auto-derived next CTA)
   ├─ calibration.js              self-study + hypothesis-ledger card; trading-halt toggle
   ├─ tradeDrawer.js              unified slide-in drawer (Decision + Recovery tabs)
   ├─ sectors.js                  sector-strength grid
   ├─ quickCheck.js               /api/check ticker lookup + LightweightCharts price chart
   ├─ portfolio.js                positions table + portfolio-risk analytics
   ├─ sec.js                      SEC compare suite (verdict, narrative, fallback)
   ├─ report.js                   /api/report/<ticker> tabs + visual + raw view
   ├─ profile.js                  preset/profile panel + apply-preview diff
   ├─ performance.js              backtest/shadow/live + challenger + evolve cards
   ├─ strategyChat.js             chat bubble renderer + queue callout + send loop
   └─ backtest.js                 form persistence, queue, polling, results, hub tabs
```

## Module contracts

### `state.js`

Exports the singleton `state` object and the localStorage namespace
constants (`UI_VIEW_MODE_KEY`, `AUTH_TOKEN_KEY`, `LEGACY_AUTH_TOKEN_KEYS`,
`BACKTEST_PREFS_KEY`, `NOTIF_STORAGE_KEY`). Keep `state` shallow and
JSON-ish — a future migration to a real store should stay tractable.

### `format.js`

Pure, side-effect-free formatters used by every render function:
`safeText`, `escapeHtml`, `safeNum`, `prettyJson`, `formatMoney`, `pct`,
`formatPercentPoints`, `clampPct`, `verdictFromScore`, `timeAgo`,
`durationSec`. The contract is "no DOM, no fetch, no module state". If
you need to touch the DOM, put it in a panel module instead.

### `auth.js`

Encapsulates token storage and the Supabase client lifecycle:

* `getApiAccessToken()` — resolves to a bearer token or `""` for
  cookie-only sessions.
* `readStoredApiJwt`, `clearStoredApiJwt`, `clearLegacyApiJwtKeys`
* `ensureCookieAuthSession`, `createCookieAuthSession`, `clearCookieAuthSession`
* `setSupabaseClient`, `getSupabaseClient`, `SUPABASE_ESM`
* `authSessionReady` (Promise) and `markAuthReady`
* `persistApiJwtFromSession`, `updateSupabaseAuthUI`
* `isProbablyAccessJwt`, `JWT_BAD_SHAPE_HINT` (re-exposed from
  `window.TradingBotAuthJwt`)

`initSupabaseAuth` itself stays in `app.js` because it depends on
`logEvent` and `refreshAccountMe`, both of which still live there.

### `api.js`

The authenticated HTTP client (`api.request/get/post/patch`). Wraps
`fetch` with a 90s timeout, request-ID header, bearer token from
`auth.getApiAccessToken`, optional `X-API-Key`, and a normalized
`{ ok, data, error, status? }` return shape. It always resolves — never
throws — so callers can do `if (!out.ok) showError(out.error)`
unconditionally.

### `logger.js`

User-facing logging surfaces:

* `logEvent({ message, kind, severity })` writes a row to the activity
  rail, bumps the activity badge, and routes destructive errors to a
  toast.
* `updateActionCenter({ title, message, severity })` paints the system
  messages card.
* `updateActivityBadge()` recomputes the unread count.
* `statusClass`, `sentimentTagClass`, `healthBadgeClass`,
  `setStatusPill`, `DIAG_LABELS` — UI helpers shared by panel modules.

### `notifications.js`

Two surfaces sharing one storage key:

* `showToast(msg, type, duration)` — transient, click-to-dismiss.
* `addNotification(msg, severity)` — persisted to `localStorage`,
  rendered in the bell-icon panel; bumps the unread badge.

`setupNotifications()` must be called once after DOMContentLoaded so the
bell click opens the panel and outside-click closes it.

### `commandPalette.js` and `shortcuts.js`

Both take their action callbacks as injected dependencies so they don't
need a hard import on the rest of `app.js`:

```js
setupCommandPalette({ runLazyApi, applyDisplayMode });
setupKeyboardShortcuts({
  openCommandPalette,
  closeCommandPalette,
  showToast,
  applyDisplayMode,
});
```

This means a future test or alternate entry point can wire them with
mocked side effects.

### `scrollToTop.js`

Tiny isolated module. `setupScrollToTop()` toggles a floating button at
`window.scrollY > 400` and smooth-scrolls to the top on click.

## Panel modules

Every file under `panels/` follows the same contract:

* It owns one logically-distinct piece of dashboard UI.
* It imports `state`, `api`, `format`, and `logger` directly when it
  needs them.
* When it has to call back into orchestrator-owned helpers
  (`refreshAccountMe`, `runLazyApi`, `runScan`, `getDisplayMode`,
  `setJobProgress`, `switchBacktestHubTab`, `refreshBacktestRuns`,
  `refreshPending`), those dependencies are **injected** as the last
  argument or as part of an options bag — never imported, never reached
  via globals.
* `app.js` exposes them under their original public name via thin
  arrow-function wrappers so the existing `wireEvents` / `connectSSE`
  / `runLazyApi` call sites don't need to know about the new dependency
  contract.

Concrete examples of injected dependencies:

```js
const submitEnableLiveTrading = () =>
  _submitEnableLiveTradingPanel({ refreshAccountMe, refreshPending });

const refreshPortfolio = () => _refreshPortfolioPanel({ runScan });

const renderSecCompareVisual = (data) =>
  _renderSecCompareVisualPanel(data, { getDisplayMode });

const queueUserBacktest = () =>
  _queueUserBacktestPanel({ setJobProgress, getDisplayMode });

const sendStrategyChat = () =>
  _sendStrategyChatPanel({ refreshBacktestRuns, switchBacktestHubTab });
```

Inter-panel dependencies follow the same rule — `panels/strategyChat.js`
gets `switchBacktestHubTab` and `refreshBacktestRuns` injected so it
doesn't have to import `panels/backtest.js`. `panels/backtest.js`
imports `scrollStrategyChatToEnd` from `panels/strategyChat.js` because
that direction has no cycle risk.

`panels/backtest.js` also keeps a single piece of module-level state —
`_backtestPersistTimer` — for the localStorage-debounce timer. This is
intentional; it should not leak through any export.

## Bootstrap order (`app.js` IIFE)

```js
wireEvents();
setupScrollToTop();
setupCommandPalette({ runLazyApi, applyDisplayMode });
setupKeyboardShortcuts({ openCommandPalette, closeCommandPalette, showToast, applyDisplayMode });
setupNotifications();
applyDisplayMode(getDisplayMode());
applyReportViewMode();
applySecCompareMode();
await loadConfig();
if (state.sseEnabled) connectSSE();
await authSessionReady;
const token = await getApiAccessToken();
// …route based on token / auth_mode / refreshAll…
```

`auth-jwt-utils.js` is still loaded as a classic script *before*
`app.js` (it sets `window.TradingBotAuthJwt` which `auth.js` reads at
import time). Keep this order in `index.html`.

## CSP

The page-level Content-Security-Policy in `webapp/security_headers.py`
already allows `script-src 'self'`, which covers same-origin module
loads from `/static/modules/*.js` and `/static/panels/*.js`. The dynamic
`await import(SUPABASE_ESM)` target (`https://esm.sh`) is *not*
whitelisted — same as before the refactor — so production deployments
that want to enforce CSP must either add `https://esm.sh` to
`script-src` or self-host the SDK.

## What's still in `app.js` and why

The orchestrator still owns the renderers that are tangled with the
wiring layer or that are read by every other panel:

* Top-level routing / lazy section loading (`runLazyApi`,
  `applySectionFromQuery`, `applyDisplayMode`).
* `wireEvents()` — the ~290-line single source of truth for all DOM
  event listeners. Each panel module exports its handlers; `wireEvents`
  binds them.
* `connectSSE()` — pushes events into `addNotification`, `showToast`,
  `refreshStatus`, `refreshPending`, `updateActionCenter`.
* Scan pipeline (`runScan`, scan-results renderer, queue-scan dialog,
  `scanBodyFromBacktestSpec`, `fillScanOptionsFromLatestBacktest`).
* Pending-trade table + manual queue form + approve/cancel.
* Account / status / diagnostics / config (`refreshAccountMe`,
  `refreshStatus`, `refreshPending`, validation rail, last-run
  metrics).
* `setJobProgress` — generic progress-bar helper shared by scan and
  backtest pollers.
* `loadConfig()` and `initSupabaseAuth()` — orchestrate state + DOM +
  SSE based on `/api/public-config`.

The next decomposition slice would split `app.js` into a `panels/scan.js`
(scan + queue dialog + diagnostics) and a `panels/account.js` (status,
account, pending, validation rail), then reduce `app.js` to a true
bootstrap-only file. That requires extracting `wireEvents` into a
declarative manifest and splitting `connectSSE` event handlers, which
is a separate effort.

## Related Pages

- [[webapp-dashboard]] — Backend that serves these static files.
- [[saas-api]] — Multi-tenant variant that shares the same static bundle.
- [[plugin-modes]] — Many of the toggles surfaced in the dashboard.

---

*Last compiled: 2026-04-17*
