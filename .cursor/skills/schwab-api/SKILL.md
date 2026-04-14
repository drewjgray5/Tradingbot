---
name: schwab-api
description: >-
  Schwab API integration patterns including dual OAuth2 authentication, token
  management, market data retrieval, and order execution. Use when working on
  Schwab auth, tokens, API calls, market data, order placement, or any code
  touching schwab_auth, DualSchwabAuth, run_dual_auth, or token refresh.
---

# Schwab API Integration

## Dual OAuth Architecture

The system uses **two separate Schwab developer apps** with distinct permissions:

| Session | Purpose | Env Prefix | Token File |
|---------|---------|------------|------------|
| **Market** | Quotes, chains, movers, price history | `SCHWAB_MARKET_*` | `tokens_market.enc` |
| **Account** | Positions, orders, account info | `SCHWAB_ACCOUNT_*` | `tokens_account.enc` |

Why two apps: Schwab's API requires different OAuth scopes. Splitting them isolates market data access from account-mutating operations.

## Credential Environment Variables

```
SCHWAB_MARKET_APP_KEY=...
SCHWAB_MARKET_APP_SECRET=...
SCHWAB_ACCOUNT_APP_KEY=...
SCHWAB_ACCOUNT_APP_SECRET=...
SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
SCHWAB_MARKET_CALLBACK_URL=https://127.0.0.1:8182  # optional, falls back to SCHWAB_CALLBACK_URL
SCHWAB_TOKEN_ENCRYPTION_KEY=...  # optional, for encrypted token files
```

SaaS adds: `CREDENTIAL_ENCRYPTION_KEY` for per-user tokens stored in `user_credentials` table.

## Auth Flow

### Local (single user)
1. `python run_dual_auth.py` opens browser for both apps
2. User completes OAuth in browser, tokens saved as encrypted files
3. `DualSchwabAuth` auto-refreshes tokens on expiry

### SaaS (multi-tenant)
1. Dashboard generates authorize URLs: `GET /api/oauth/schwab/{account|market}/authorize-url`
2. User completes OAuth, callback stores encrypted tokens in DB
3. Per-tenant `DualSchwabAuth` created from DB credentials

## Token Refresh

`DualSchwabAuth` handles automatic refresh. If tokens expire completely:
- Local: re-run `run_dual_auth.py`
- SaaS: user reconnects via dashboard OAuth flow

## Market Data Resilience

Priority chain in `market_data.py`:
1. **Schwab API** — primary, with retry/backoff and circuit breaker
2. **yfinance** — fallback for price history
3. **Polygon** — secondary fallback where implemented

Circuit breaker trips after consecutive failures, auto-recovers after cooldown.

## Order Execution Flow

1. Signal passes all quality gates and guardrails
2. Position size computed (adaptive stops, regime multiplier, sector caps)
3. Order submitted via account session
4. Stop-loss order attached on fill confirmation
5. All execution logged to DB and Discord

## Health Checks

- `GET /api/health/deep` — checks token validity, quote freshness, DB connectivity
- `python healthcheck.py` — local CLI diagnostic
- Both report: `market_token_ok`, `account_token_ok`, `last_quote_age_sec`

## Key Files

- `schwab_skill/schwab_auth.py` — `DualSchwabAuth` class
- `schwab_skill/run_dual_auth.py` — browser OAuth flow
- `schwab_skill/market_data.py` — data retrieval with fallbacks
- `schwab_skill/execution.py` — order placement
- `schwab_skill/webapp/tenant_dashboard.py` — SaaS OAuth callbacks
