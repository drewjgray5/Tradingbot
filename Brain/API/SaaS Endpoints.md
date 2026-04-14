---
tags: [api, saas]
---
# SaaS Endpoints

Routes from `schwab_skill/webapp/main_saas.py`. Multi-tenant production API with Supabase JWT auth.

## Authentication

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/auth/session` | JWT | Current session / user info |
| GET | `/api/me` | JWT | User profile |

## Credentials

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/credentials/schwab` | JWT | Upload encrypted Schwab OAuth tokens |
| GET | `/api/credentials/status` | JWT | Check credential materialization |

## Settings

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/settings/enable-live-trading` | JWT | Enable live execution (risk checkbox + type "ENABLE") |
| POST | `/api/settings/trading-halt` | JWT | Toggle user trading halt |

## Scanning (Async via Celery)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/scan` | JWT | Queue async scan task |
| GET | `/api/scan/{task_id}` | JWT | Poll scan task status |
| GET | `/api/scan-results` | JWT | Latest scan results from DB |

## Orders

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/orders/execute` | JWT | **Returns 410** -- use pending trade flow instead |
| GET | `/api/positions` | JWT | Current positions |

## Pending Trades
Same pattern as local dashboard but scoped to authenticated user via JWT.

## Backtest

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/backtest-runs` | JWT | Queue backtest job |
| GET | `/api/backtest-runs` | JWT | List backtest runs |

## Strategy Chat

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/strategy-chat` | JWT | LLM-powered strategy discussion |

## Billing

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| Various | `/api/billing/*` | JWT/Stripe | Stripe subscription management and webhooks |

## Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/health/live` | None | Liveness probe |
| GET | `/api/health/ready` | None | Readiness probe (DB + Redis) |

## Metrics

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/metrics` | None | Prometheus text format metrics |

## Tenant Dashboard Router
The SaaS API includes the [[Tenant Dashboard Endpoints]] router for per-tenant functionality.

## Key File
`schwab_skill/webapp/main_saas.py`

## Related
- [[SaaS API]] — architecture overview
- [[Tenant Dashboard Endpoints]] — included router
- [[API Reference MOC]]
