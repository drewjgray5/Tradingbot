---
tags: [config, saas, infrastructure]
---
# SaaS Infrastructure

Environment variables for the multi-tenant production deployment.

## Database

| Env Var | Required | Description |
|---------|----------|-------------|
| `DATABASE_URL` | Yes | Postgres connection string (not Supabase HTTPS URL) |

## Redis & Celery

| Env Var | Required | Description |
|---------|----------|-------------|
| `REDIS_URL` | Yes | Redis connection string |
| `CELERY_BROKER_URL` | Auto | Usually same as REDIS_URL |
| `CELERY_RESULT_BACKEND` | Auto | Usually same as REDIS_URL |

## Authentication (Supabase)

| Env Var | Required | Description |
|---------|----------|-------------|
| `SUPABASE_JWT_SECRET` | Yes | JWT secret for token validation |
| `SUPABASE_URL` | For browser sign-in | Supabase project URL |
| `SUPABASE_ANON_KEY` | For browser sign-in | Supabase anonymous key |

## Billing (Stripe)

| Env Var | Required | Description |
|---------|----------|-------------|
| `STRIPE_SECRET_KEY` | For billing | Stripe API secret key |
| `STRIPE_WEBHOOK_SECRET` | For billing | Stripe webhook signing secret |

## Security

| Env Var | Required | Description |
|---------|----------|-------------|
| `CREDENTIAL_ENCRYPTION_KEY` | Yes | Encryption key for user Schwab tokens |
| `SCHWAB_TOKEN_ENCRYPTION_KEY` | Optional | Encryption for platform token files |

## Platform Controls

| Env Var | Default | Description |
|---------|---------|-------------|
| `LIVE_TRADING_KILL_SWITCH` | false | Platform-wide halt on all live trading |
| `LIVE_TRADING_KILL_SWITCH_BLOCKS_EXITS` | false | Also block SELL orders when kill switch is on |

## Health & Bootstrap

| Env Var | Default | Description |
|---------|---------|-------------|
| `SAAS_BOOTSTRAP_SCHEMA` | 0 | Auto-create schema on first boot |
| `SAAS_RUN_ALEMBIC` | 0 | Auto-run alembic upgrade head on startup |
| `SAAS_HEALTH_REQUIRE_REDIS` | 1 | Require Redis for health readiness (set 0 for dev) |

## Web Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `WEB_API_KEY` | none | API key for trade approval endpoints |
| `WEB_ALLOWED_ORIGINS` | none | CORS allowed origins |
| `WEB_LOCAL_USER_ID` | local | User ID for local dashboard mode |
| `WEB_IMPLEMENTATION_GUIDE_URL` | none | Custom Schwab setup guide URL |

## Related
- [[SaaS API]] — architecture overview
- [[Deployment]] — deployment runbook
- [[Schwab API Keys]] — Schwab credentials
