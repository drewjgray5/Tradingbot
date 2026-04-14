---
source: Brain/API/SaaS Endpoints.md
created: 2026-04-13
updated: 2026-04-13
tags: [api, saas, endpoints]
---

# SaaS Endpoints

> Routes from `webapp/main_saas.py` — multi-tenant API with Supabase JWT auth.

## Key Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/auth/session` | JWT | Current session |
| POST | `/api/credentials/schwab` | JWT | Upload Schwab tokens |
| POST | `/api/scan` | JWT | Queue async scan (Celery) |
| GET | `/api/scan/{task_id}` | JWT | Poll scan status |
| GET | `/api/positions` | JWT | Current positions |
| POST | `/api/settings/enable-live-trading` | JWT | Enable live execution |
| GET | `/api/health/live` | None | Liveness probe |
| GET | `/api/health/ready` | None | Readiness (DB + Redis) |
| GET | `/metrics` | None | Prometheus metrics |

## Related Pages

- [[saas-api]] — architecture overview
- [[tenant-dashboard-endpoints]] — per-tenant router
- [[local-dashboard-endpoints]] — local equivalent

---

*Last compiled: 2026-04-13*
