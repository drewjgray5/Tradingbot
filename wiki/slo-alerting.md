---
source: Brain/Runbooks/SLO and Alerting.md
created: 2026-04-14
updated: 2026-04-14
tags: [runbook, slo, sli, alerting]
---

# SLO Alerting

> Service level objectives, indicators, and paging thresholds for production.

## Core SLIs

- API availability via readiness success
- Scan success ratio
- Order execution success ratio
- Queue latency (scan/order)
- API 5xx error rate

## Initial Targets

- Availability: 99.5% monthly
- Scan success: >=97% daily
- Order success: >=99% daily
- Queue latency p95: <=120s scans, <=30s orders
- API 5xx rate: <1% per 15m window

## Paging Thresholds

- Readiness failing >5m
- API 5xx >2% for 10m
- Scan backlog >100 tasks for 10m
- Order backlog >20 tasks for 5m
- Stripe webhook failure burst

## Error Budget Policy

- >50% burn early month: freeze non-essential launches
- >80% burn: release freeze until mitigations complete

## Related Pages

- [[incident-response-saas]] — incident workflow
- [[backup-restore]] — recovery operations
- [[validation]] — pre-release validation gates

---

*Last compiled: 2026-04-14*
