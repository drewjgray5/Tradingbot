---
tags: [moc, config]
---
# Config Reference MOC

Every environment variable and tunable parameter, organized by domain.

All config is loaded via `schwab_skill/config.py` with process env overrides taking precedence over `.env` file values.

## Config Domains
- [[Schwab API Keys]] — OAuth credentials, callback URLs, token encryption
- [[Scanner Tunables]] — Stage 2, VCP, shortlist sizing, worker parallelism, regime gate
- [[Plugin Modes Config]] — execution quality, exit manager, event risk, regime v2, correlation guard, strategy ensemble
- [[SaaS Infrastructure]] — DATABASE_URL, Redis, Celery, Supabase JWT, Stripe
- [[Feature Flags]] — kill switches, shadow mode, hypothesis ledger, SEC enrichment, advisory model

## How Config Works

1. `config.py` reads `schwab_skill/.env` on each call
2. Process environment variables (`os.environ`) override `.env` values
3. Each getter has a hardcoded default used when the key is absent
4. Plugin modes validate against allowed values (`off`, `shadow`, `live`)
5. Booleans accept `0/false/no/off` and `1/true/yes/on`
