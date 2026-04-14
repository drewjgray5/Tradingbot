---
source: Brain/Architecture/SaaS API.md
created: 2026-04-13
updated: 2026-04-13
tags: [architecture, saas, api]
---

# SaaS API

> Multi-tenant production API with Supabase JWT auth, Stripe billing, and Celery workers.

## Stack

- **FastAPI** (`webapp/main_saas.py`) — API server
- **Supabase** — JWT auth, user management
- **Stripe** — subscription billing
- **Celery + Redis** — async scan and backtest workers
- **Postgres** — persistent storage via `DATABASE_URL`

## Key Endpoints

| Category | Examples |
|----------|---------|
| Auth | `GET /api/auth/session`, `GET /api/me` |
| Credentials | `POST /api/credentials/schwab`, `GET /api/credentials/status` |
| Scanning | `POST /api/scan` (async), `GET /api/scan/{task_id}`, `GET /api/scan-results` |
| Orders | `POST /api/orders/execute` (returns 410 — use pending trade flow) |
| Billing | `/api/billing/*` (Stripe) |
| Health | `GET /api/health/live`, `GET /api/health/ready` |
| Metrics | `GET /metrics` (Prometheus) |

## Tenant Dashboard Router

Includes [[tenant-dashboard-endpoints]] for per-user functionality: OAuth callbacks, portfolio, pending trades, reports.

## Related Pages

- [[saas-infrastructure]] — environment variables
- [[schwab-auth]] — OAuth in SaaS context
- [[deployment]] — how to deploy
- [[system-overview]] — architecture context

---

*Last compiled: 2026-04-13*
