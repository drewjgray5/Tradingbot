---
tags: [runbook, slo, sli, alerting]
---
# SLO and Alerting

SLO contract for production operation and launch readiness.

## Service Level Indicators

- **API availability**: successful `/api/health/ready` responses
- **Scan success ratio**: successful scan tasks / queued scan tasks
- **Order execution success ratio**: executed orders / approved trade attempts
- **Queue latency**: p95 time from task queued to completion
- **Error rate**: 5xx responses / total API requests

## Initial SLO Targets

- API availability: **99.5%** monthly
- Scan success ratio: **>= 97%** daily
- Order execution success ratio: **>= 99%** daily
- Queue latency p95: **<= 120s** for scans, **<= 30s** for orders
- API 5xx rate: **< 1%** per 15-minute window

## Alert Thresholds

- Readiness failing > 5 minutes -> page
- API 5xx > 2% for 10 minutes -> page
- Scan queue backlog > 100 tasks for 10 minutes -> page
- Order queue backlog > 20 tasks for 5 minutes -> page
- Stripe webhook failures > 5 consecutive -> page

## Alert Routing

1. Primary: Pager/on-call system
2. Secondary: Discord notifications
3. Escalation after 15 minutes with no ack

## Error Budget Policy

- Monthly error budget burn > 50% in first half of month: freeze non-essential launches.
- Burn > 80% anytime: launch freeze until mitigation reviewed.

## Dashboard Requirements

- API request volume + latency + error rate
- Celery queue depth by queue
- Task success/failure counters
- Dependency health (DB/Redis/JWKS)

## Release Gate Linkage

Before production promotion:

1. Last 24h SLOs within targets
2. No unresolved SEV-1/SEV-2 incidents
3. Validation profile passes (`validate_all --profile ci --strict`)
