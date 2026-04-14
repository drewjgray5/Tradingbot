---
tags: [runbook, backup, restore, dr]
---
# Backup Restore

Backup and restore checklist for local and SaaS deployments.

## Recovery Targets

- **SaaS RPO**: 15 minutes
- **SaaS RTO**: 60 minutes
- **Local RPO**: 24 hours
- **Local RTO**: 30 minutes

## What Must Be Backed Up

- Postgres database (`DATABASE_URL`)
- Redis-backed idempotency/rate-limit behavior assumptions (ephemeral, not source of truth)
- Encrypted credential data in `user_credentials`
- Local mode SQLite file: `schwab_skill/webapp/webapp.db`
- Critical secrets inventory (`CREDENTIAL_ENCRYPTION_KEY`, Supabase JWT settings, Schwab keys, Stripe secrets)

## SaaS Backup Procedure

1. Verify managed Postgres backups are enabled in the provider.
2. Run logical dump daily (or rely on platform snapshots if guaranteed):
   - `pg_dump --format=custom --no-owner --no-privileges "$DATABASE_URL" > tradingbot_$(date +%F).dump`
3. Store dumps in encrypted storage with retention policy (>=14 days).
4. Weekly restore drill into staging:
   - Create scratch DB
   - `pg_restore --clean --if-exists --no-owner --dbname "$STAGING_DATABASE_URL" tradingbot_<date>.dump`
   - Run `python scripts/validate_all.py --profile ci --skip-backtest --strict`

## Local Backup Procedure

1. Stop writes to local dashboard process.
2. Copy `schwab_skill/webapp/webapp.db` to a timestamped backup path.
3. Optionally gzip/encrypt backup before cloud sync.
4. Keep at least 7 daily versions.

## Restore Procedure (SaaS)

1. Declare incident and freeze deploys.
2. Restore latest healthy snapshot to replacement Postgres instance.
3. Point `DATABASE_URL` to restored instance.
4. Run `alembic upgrade head`.
5. Validate API:
   - `/api/health`
   - `/api/health/ready`
   - authenticated `/api/me`
6. Resume worker traffic and monitor `/metrics` plus queue depth.

## Restore Procedure (Local)

1. Stop local process.
2. Replace `webapp.db` with chosen backup file.
3. Start app and run `/api/health`.
4. Validate pending trades + scan history in UI.

## Validation Checklist After Any Restore

- Auth works (`/api/auth/session` or bearer JWT path)
- Latest scan/job records present
- Pending trades and order history readable
- No migration drift (`alembic current` equals `head`)

## Escalation

- If restore exceeds RTO, switch to maintenance mode and notify users.
- If encryption key mismatch prevents decrypting tokens, rotate/re-link Schwab tokens immediately.
