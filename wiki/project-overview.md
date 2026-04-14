---
source: n/a (manually created)
created: 2026-04-13
updated: 2026-04-13
tags: [overview, architecture]
---

# Project Overview

> Schwab-integrated trading automation system with signal scanning, risk management, and multi-tenant SaaS deployment.

## What is TradingBot?

A momentum/breakout trading system that scans for Stage 2 + VCP setups, enriches signals with fundamental/earnings analysis, applies multi-layered risk guardrails, and executes trades via the Charles Schwab API. Supports both single-user local and multi-tenant SaaS deployment.

## Key Components

- **Signal Scanner** — two-stage pipeline: Stage A (fast structural filters) → Stage B (enrichment, ranking, quality gates). See [[signal-scanner]].
- **Execution Engine** — order placement with guardrails, adaptive stops, and plugin hooks. See [[execution-engine]].
- **Advisory Model** — calibrated P(up in 10 days) probability scoring. See [[advisory-model]].
- **WebApp Dashboard** — FastAPI dashboard for scanning, approval, portfolio. See [[webapp-dashboard]].
- **SaaS API** — multi-tenant production API (Supabase, Stripe, Celery). See [[saas-api]].
- **Learning Loop** — self-study, hypothesis ledger, feature store, evolve logic.

## Deployment Modes

| Mode | DB | Auth | Workers | Entry Point |
|------|-----|------|---------|-------------|
| Local | SQLite | API key | `schedule` loop | `main.py` |
| SaaS | Postgres | Supabase JWT | Celery + Redis | `main_saas.py` |

See [[deployment]] for setup instructions.

## Related Pages

- [[system-overview]] — architecture deep-dive
- [[index]] — central catalog

---

*Last compiled: 2026-04-13*
