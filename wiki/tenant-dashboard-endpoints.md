---
source: Brain/API/Tenant Dashboard Endpoints.md
created: 2026-04-13
updated: 2026-04-13
tags: [api, tenant, endpoints]
---

# Tenant Dashboard Endpoints

> Per-tenant API router from `webapp/tenant_dashboard.py`, included in SaaS API. All require JWT.

## Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/oauth/schwab/{account\|market}/authorize-url` | OAuth authorize URL |
| GET | `/api/oauth/schwab/{account\|market}/callback` | OAuth callback |
| GET | `/api/portfolio` | Tenant positions |
| GET | `/api/decision-card/{ticker}` | Signal decision card |
| POST | `/api/trades/{id}/approve` | Approve + execute |
| GET | `/api/settings/profiles` | Profile catalog |

## Related Pages

- [[saas-endpoints]] — parent API
- [[saas-api]] — architecture
- [[schwab-auth]] — OAuth flow

---

*Last compiled: 2026-04-13*
