---
source: Brain/Runbooks/Incident Response SaaS.md
created: 2026-04-14
updated: 2026-04-14
tags: [runbook, incident, saas, ops]
---

# Incident Response SaaS

> Operational playbook for SaaS incidents affecting availability, safety, or tenant isolation.

## Severity

- SEV-1: full outage, cross-tenant risk, or live-trade safety risk
- SEV-2: major degradation
- SEV-3: partial degradation with workaround

## Response Flow

1. Acknowledge and assign incident commander
2. Stabilize (enable kill switch if required)
3. Triage with health, queue, and dependency signals
4. Mitigate safely
5. Recover and validate
6. Publish postmortem

## Scenario Coverage

- Redis down / degraded
- Postgres down / slow
- Celery backlog / stuck tasks
- JWT verification failures
- Stripe webhook failures

## Communications

- SEV-1 updates every 15 minutes
- SEV-2 updates every 30 minutes
- Include impact, mitigation, ETA, next update time

## Related Pages

- [[backup-restore]] — restore steps and DR targets
- [[slo-alerting]] — paging thresholds and error budget policy
- [[troubleshooting]] — diagnostics matrix

---

*Last compiled: 2026-04-14*
