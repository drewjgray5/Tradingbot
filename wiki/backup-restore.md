---
source: Brain/Runbooks/Backup Restore.md
created: 2026-04-14
updated: 2026-04-14
tags: [runbook, backup, restore, dr]
---

# Backup Restore

> Backup and restore contract for local and SaaS deployments.

## Recovery Objectives

- SaaS: RPO 15m / RTO 60m
- Local: RPO 24h / RTO 30m

## Backup Scope

- Postgres primary data (`DATABASE_URL`)
- Local SQLite file (`schwab_skill/webapp/webapp.db`)
- Encrypted credential rows (`user_credentials`)
- Critical secret inventory (`CREDENTIAL_ENCRYPTION_KEY`, Supabase JWT vars, Schwab + Stripe keys)

## SaaS Procedure

1. Ensure managed backups are enabled.
2. Take daily logical backup (`pg_dump` custom format).
3. Store encrypted artifacts with 14+ day retention.
4. Run weekly restore drill in staging (`pg_restore` + `validate_all --profile ci --strict`).

## Restore Validation

- `/api/health` and `/api/health/ready` succeed
- Auth works (`/api/me`)
- Scan/order data visible
- Alembic revision matches `head`

## Related Pages

- [[deployment]] — deployment flow and environment setup
- [[troubleshooting]] — incident diagnostics
- [[saas-infrastructure]] — runtime secret/config inventory

---

*Last compiled: 2026-04-14*
