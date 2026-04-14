---
source: Brain/Architecture/Schwab Auth.md
created: 2026-04-13
updated: 2026-04-13
tags: [architecture, auth, schwab]
---

# Schwab Auth

> Dual OAuth2 authentication with separate market and account sessions.

## Dual App Architecture

| Session | Purpose | Env Prefix | Token File |
|---------|---------|------------|------------|
| Market | Quotes, chains, movers, price history | `SCHWAB_MARKET_*` | `tokens_market.enc` |
| Account | Positions, orders, account info | `SCHWAB_ACCOUNT_*` | `tokens_account.enc` |

## Auth Flow

### Local
1. `python run_dual_auth.py` → browser OAuth for both apps
2. Tokens saved as encrypted files
3. `DualSchwabAuth` auto-refreshes on expiry

### SaaS
1. `GET /api/oauth/schwab/{account|market}/authorize-url` generates auth URLs
2. User completes browser OAuth
3. Callback stores encrypted tokens in `user_credentials` table
4. Per-tenant `DualSchwabAuth` created from DB credentials

## Token Refresh

Automatic via `DualSchwabAuth`. Full expiry requires re-auth (local: `run_dual_auth.py`, SaaS: dashboard reconnect).

## Health Monitoring

- `GET /api/health/deep` reports `market_token_ok`, `account_token_ok`, `last_quote_age_sec`
- `python healthcheck.py` for local CLI diagnostics

## Related Pages

- [[schwab-api-keys]] — credential environment variables
- [[schwab-oauth-setup]] — step-by-step runbook
- [[system-overview]] — where auth fits in the pipeline
- [[saas-api]] — SaaS OAuth callback routes

---

*Last compiled: 2026-04-13*
