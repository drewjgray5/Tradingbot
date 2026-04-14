---
source: Brain/Runbooks/Deployment.md
created: 2026-04-13
updated: 2026-04-13
tags: [runbook, deployment]
---

# Deployment

> How to deploy the TradingBot to production.

## Options

### Render (Recommended)
`render.yaml` Blueprint: Postgres + Redis + Web (Docker) + Worker (Celery).

### Docker Compose
```
docker-compose -f schwab_skill/docker-compose.saas.yml up
```

### Manual
```
uvicorn webapp.main_saas:app --host 0.0.0.0 --port 8000
celery -A webapp.tasks worker -Q scan,orders,celery --loglevel=info
alembic upgrade head
```

## Required Env Vars

Critical: `DATABASE_URL`, `REDIS_URL`, `SUPABASE_JWT_SECRET`, `SCHWAB_MARKET_APP_KEY`, `SCHWAB_ACCOUNT_APP_KEY`, `STRIPE_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`.

See [[saas-infrastructure]] for the full list.

## Database Bootstrap

- `python scripts/saas_bootstrap.py` or `SAAS_BOOTSTRAP_SCHEMA=1`
- Migrations: `SAAS_RUN_ALEMBIC=1` or `alembic upgrade head`

## Related Pages

- [[saas-api]] — what gets deployed
- [[saas-infrastructure]] — env vars
- [[schwab-oauth-setup]] — token setup

---

*Last compiled: 2026-04-13*
