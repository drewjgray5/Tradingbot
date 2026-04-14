---
tags: [runbook, incident, saas, operations]
---
# Incident Response SaaS

Primary incident handling workflow for multi-tenant production.

## Severity Levels

- **SEV-1**: Full outage, cross-tenant data risk, order safety risk.
- **SEV-2**: Major degradation (queue backlog, degraded scans/orders, billing webhooks failing).
- **SEV-3**: Partial feature degradation with workaround.

## Golden Workflow

1. **Acknowledge** alert and assign incident commander.
2. **Stabilize** by enabling kill switch if execution risk exists.
3. **Triage** using health + metrics + queue state.
4. **Mitigate** with smallest safe change.
5. **Recover** normal operation.
6. **Postmortem** within 24 hours.

## Core Commands / Checks

- API liveness/readiness: `GET /api/health/live`, `GET /api/health/ready`
- Worker pressure: Celery queue depth and active tasks
- Redis connectivity: `redis_ping` signal via readiness payload
- DB connectivity: `SELECT 1` and migration head check

## Scenario Playbooks

### Redis Down / Degraded

- Expected behavior: rate limits fail closed, scans/orders may be rejected.
- Actions:
  1. Confirm Redis endpoint health.
  2. Scale/restart Redis service.
  3. Keep fail-closed mode enabled (`SAAS_RATE_LIMIT_FAIL_OPEN` unset/false).
  4. Recheck `/api/health/ready`.

### Postgres Down / Slow

- Actions:
  1. Confirm DB connectivity from API and worker.
  2. Enable maintenance mode for write-heavy endpoints if required.
  3. Fail over or restore per backup runbook.
  4. Verify `alembic` head and API read paths.

### Celery Backlog / Stuck Tasks

- Actions:
  1. Inspect queue depth and worker concurrency settings.
  2. Scale worker count or reduce scan concurrency env vars.
  3. Check for repeated task failure patterns.
  4. Replay failed user-visible jobs where safe.

### JWT Validation Failures

- Actions:
  1. Confirm `SUPABASE_JWT_SECRET`, `SUPABASE_JWT_AUDIENCE`, `SUPABASE_JWT_ISSUER`.
  2. Validate Supabase project URL and JWKS accessibility.
  3. Roll forward config and verify `/api/me`.

### Stripe Webhook Failures

- Actions:
  1. Validate `STRIPE_WEBHOOK_SECRET`.
  2. Confirm webhook signature header and retries.
  3. Replay failed events after fix.

## Communications

- SEV-1 updates every 15 minutes.
- SEV-2 updates every 30 minutes.
- Include: impact, mitigation in progress, ETA, next update time.
