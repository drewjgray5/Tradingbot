---
source: Brain/Architecture/WebApp Dashboard.md
created: 2026-04-13
updated: 2026-04-13
tags: [architecture, webapp, dashboard]
---

# WebApp Dashboard

> FastAPI local dashboard for scanning, trade approval, portfolio, and system health.

## Two UIs

- `app.js` + `index.html` — full-featured dashboard
- `simple.js` + `simple.html` — lightweight scan + diagnostics

## Key Route Groups

| Group | Example | Auth |
|-------|---------|------|
| Health | `GET /api/health/deep` | None |
| Scanning | `POST /api/scan`, `GET /api/scan/status` | Optional API key |
| Research | `GET /api/check/{ticker}`, `GET /api/report/{ticker}` | None |
| SEC | `GET /api/sec/analyze/{ticker}`, `GET /api/sec/compare` | None |
| Portfolio | `GET /api/portfolio`, `GET /api/sectors` | None |
| Trades | `POST /api/trades/{id}/approve` | Required API key |
| Settings | `POST /api/settings/profile` | Optional API key |

## Response Pattern

All API routes return `ApiResponse(ok, data, error)` via `_ok()` / `_err()` helpers.

## Middleware

- CORS with `build_allowed_origins()`
- Request metrics and timing (`X-Response-Time` header)
- `Cache-Control: no-store` on `/api/` routes

## Related Pages

- [[local-dashboard-endpoints]] — full endpoint reference
- [[tenant-dashboard-endpoints]] — SaaS per-tenant routes
- [[system-overview]] — architecture context

---

*Last compiled: 2026-04-13*
