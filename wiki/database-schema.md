---
source: Brain/Architecture/Database Schema.md
created: 2026-04-13
updated: 2026-04-13
tags: [architecture, database, schema]
---

# Database Schema

> SQLAlchemy ORM models and table relationships.

## Key Tables

| Table | Purpose | Mode |
|-------|---------|------|
| `users` | User accounts | SaaS |
| `orders` | Trade orders and execution records | Both |
| `pending_trades` | Trades awaiting approval | Both |
| `scan_results` | Stored scan outputs and diagnostics | Both |
| `user_credentials` | Encrypted Schwab OAuth tokens | SaaS |
| `backtest_runs` | Async backtest job results | SaaS |

## Engine Configuration

- Local: SQLite at `webapp/webapp.db`
- SaaS: Postgres via `DATABASE_URL` with connection pool (`pool_size=5`, `max_overflow=10`, `pool_pre_ping=True`)
- Render: auto-appended `?sslmode=require`

## Migrations

- Alembic for schema changes
- SQLite: auto-upgrade on startup
- Postgres: `SAAS_RUN_ALEMBIC=1` or manual `alembic upgrade head`
- Bootstrap: `SAAS_BOOTSTRAP_SCHEMA=1` or `python scripts/saas_bootstrap.py`

## Related Pages

- [[saas-infrastructure]] — DB connection config
- [[system-overview]] — where DB fits in architecture
- [[webapp-dashboard]] — routes that query these tables

---

*Last compiled: 2026-04-13*
