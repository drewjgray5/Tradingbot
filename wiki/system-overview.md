---
source: Brain/Architecture/System Overview.md
created: 2026-04-13
updated: 2026-04-13
tags: [architecture, overview]
---

# System Overview

> End-to-end pipeline from market data to trade execution, with two deployment modes.

## Pipeline Flow

```
Market Data → Signal Scanner → Advisory Model → Guardrails → Execution → Discord Alerts
     ↑              ↑                                              ↓
  Schwab API    Stage A + B                                   Trade Outcomes
  yfinance      (parallel)                                         ↓
  Polygon                                                    Self-Study
                                                             Hypothesis Ledger
```

## Core Components

| Component | Responsibility |
|-----------|---------------|
| [[schwab-auth]] | Dual OAuth2 sessions (market + account) |
| [[signal-scanner]] | Two-stage scan pipeline (Stage A fast filter + Stage B enrichment) |
| [[execution-engine]] | Order placement with guardrail wrapper and plugin hooks |
| [[guardrails]] | Risk limits, circuit breaker, sector caps, position sizing |
| [[advisory-model]] | Calibrated P(up in 10 days) probability scoring |
| [[webapp-dashboard]] | FastAPI local dashboard (scan, approve, portfolio) |
| [[saas-api]] | Multi-tenant production API (Supabase, Stripe, Celery) |
| [[discord-integration]] | Webhooks, slash commands, notification types |

## Deployment Modes

- **Local**: single-user, SQLite, `schedule`-based bot loop, API key auth
- **SaaS**: multi-tenant, Postgres, Celery workers, Redis, Supabase JWT, Stripe billing

## Learning Loop

- [[self-study]] analyzes trade outcomes for calibration
- [[hypothesis-ledger]] tracks decision quality separately from P&L
- [[feature-store]] persists per-scan features → [[evolve-logic]] proposes env tweaks → challenger A/B testing

## Related Pages

- [[project-overview]] — high-level project summary
- [[database-schema]] — all tables and relationships
- [[deployment]] — how to deploy

---

*Last compiled: 2026-04-13*
