---
source: Brain/API/Local Dashboard Endpoints.md
created: 2026-04-13
updated: 2026-04-13
tags: [api, local, endpoints]
---

# Local Dashboard Endpoints

> All routes from `webapp/main.py` — single-user FastAPI app.

## Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health/deep` | Full health: DB, tokens, quotes |
| POST | `/api/scan` | Trigger scan |
| GET | `/api/scan/status` | Current/last scan status + signals |
| GET | `/api/check/{ticker}` | Quick technical check |
| GET | `/api/report/{ticker}` | Full multi-section report |
| GET | `/api/portfolio` | Account positions with P&L |
| GET | `/api/sectors` | Sector heatmap |
| POST | `/api/trades/{id}/approve` | Approve + execute (requires API key) |
| POST | `/api/trades/{id}/reject` | Reject trade (requires API key) |
| GET | `/api/trades/{id}/preflight` | Pre-trade checklist |
| GET | `/api/sec/analyze/{ticker}` | SEC filing analysis |
| GET | `/api/performance` | Backtest/shadow/live metrics |

## Related Pages

- [[webapp-dashboard]] — architecture overview
- [[saas-endpoints]] — SaaS equivalent
- [[tenant-dashboard-endpoints]] — per-tenant routes

---

*Last compiled: 2026-04-13*
