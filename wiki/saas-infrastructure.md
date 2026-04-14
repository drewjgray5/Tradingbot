---
source: Brain/Config/SaaS Infrastructure.md
created: 2026-04-13
updated: 2026-04-13
tags: [config, saas, infrastructure]
---

# SaaS Infrastructure

> Environment variables for the multi-tenant production deployment.

## Database

| Env Var | Required | Description |
|---------|----------|-------------|
| `DATABASE_URL` | Yes | Postgres connection string |

## Redis & Celery

| Env Var | Required | Description |
|---------|----------|-------------|
| `REDIS_URL` | Yes | Redis connection string |

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

| Env Var | Description |
|---------|-------------|
| `CREDENTIAL_ENCRYPTION_KEY` | Encryption key for user Schwab tokens |

## Health & Bootstrap

| Env Var | Default | Description |
|---------|---------|-------------|
| `SAAS_BOOTSTRAP_SCHEMA` | 0 | Auto-create schema on first boot |
| `SAAS_RUN_ALEMBIC` | 0 | Auto-run alembic upgrade head |
| `SAAS_HEALTH_REQUIRE_REDIS` | 1 | Require Redis for readiness |

## Related Pages

- [[saas-api]] — architecture overview
- [[deployment]] — deployment runbook
- [[schwab-api-keys]] — Schwab credentials
- [[feature-flags]] — other config domains

---

*Last compiled: 2026-04-13*
