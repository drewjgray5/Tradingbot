---
tags: [api, tenant]
---
# Tenant Dashboard Endpoints

Routes from `schwab_skill/webapp/tenant_dashboard.py`. Per-tenant API router included in the SaaS API.

All endpoints require Supabase JWT authentication.

## Status & Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Tenant status (tokens, last scan, validation) |
| GET | `/api/health/deep` | Deep health check (DB, tokens, quotes) |

## Schwab OAuth

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/oauth/schwab/account/authorize-url` | Get account OAuth authorize URL |
| GET | `/api/oauth/schwab/account/callback` | Account OAuth callback handler |
| GET | `/api/oauth/schwab/market/authorize-url` | Get market OAuth authorize URL |
| GET | `/api/oauth/schwab/market/callback` | Market OAuth callback handler |

## Portfolio & Positions

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Account positions with P&L |
| GET | `/api/sectors` | Sector heatmap |

## Scanning & Research

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/decision-card/{ticker}` | Signal decision card |
| GET | `/api/check/{ticker}` | Quick technical check |
| GET | `/api/report/{ticker}` | Full multi-section report |
| GET | `/api/sec/compare` | SEC filing comparison |

## Pending Trades

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/pending-trades` | List pending trades |
| POST | `/api/trades/{trade_id}/approve` | Approve + execute trade |
| POST | `/api/trades/{trade_id}/reject` | Reject trade |
| GET | `/api/trades/{trade_id}/preflight` | Pre-trade checklist |

## Settings & Onboarding

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/settings/profiles` | Profile catalog |
| POST | `/api/settings/profile` | Switch settings profile |
| POST | `/api/onboarding/start` | Start onboarding |
| POST | `/api/onboarding/step/{step}` | Run onboarding step |
| GET | `/api/onboarding/status` | Onboarding progress |

## Performance

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/performance` | Performance metrics |
| GET | `/api/recovery/map` | Error recovery map |

## Key File
`schwab_skill/webapp/tenant_dashboard.py`

## Related
- [[SaaS Endpoints]] — parent API
- [[SaaS API]] — architecture
- [[API Reference MOC]]
