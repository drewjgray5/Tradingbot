---
tags: [runbook, deployment]
---
# Deployment

How to deploy the TradingBot to production.

## Source Documentation
Full deployment guide: `schwab_skill/docs/SAAS_DEPLOYMENT.md`

## Deployment Options

### Render (Recommended)
The repo includes a Render Blueprint at `render.yaml`:
- **Postgres** database
- **Redis** instance
- **Web service** (Docker, from `schwab_skill/Dockerfile.saas`)
- **Worker service** (Celery, same Docker image)

### Docker Compose
For local/self-hosted production:
```
docker-compose -f schwab_skill/docker-compose.saas.yml up
```

### Manual
```
# API
uvicorn webapp.main_saas:app --host 0.0.0.0 --port 8000

# Workers
celery -A webapp.tasks worker -Q scan,orders,celery --loglevel=info

# Migrations
alembic upgrade head
```

## Required Environment Variables
See [[SaaS Infrastructure]] for the full list. Critical ones:
- `DATABASE_URL` — Postgres connection string
- `REDIS_URL` — Redis connection string
- `SUPABASE_JWT_SECRET` — for auth
- `SCHWAB_MARKET_APP_KEY`, `SCHWAB_ACCOUNT_APP_KEY` — Schwab API
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` — billing
- `CREDENTIAL_ENCRYPTION_KEY` — token encryption

## Database Bootstrap
- Empty Postgres: `python scripts/saas_bootstrap.py`
- Or set `SAAS_BOOTSTRAP_SCHEMA=1` for one-time auto-create on API boot
- Auto-migrations: `SAAS_RUN_ALEMBIC=1`

## Related
- [[SaaS API]] — what gets deployed
- [[SaaS Infrastructure]] — env vars
- [[Config Reference MOC]] — all configuration
- [[Schwab OAuth Setup]] — token setup
