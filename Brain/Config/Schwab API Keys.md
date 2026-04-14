---
tags: [config, auth, schwab]
---
# Schwab API Keys

Credentials and callback URLs for the Schwab Developer API.

## Market App (OHLCV, Quotes)

| Env Var | Required | Description |
|---------|----------|-------------|
| `SCHWAB_MARKET_APP_KEY` | Yes | Market app key from Schwab Developer Portal |
| `SCHWAB_MARKET_APP_SECRET` | Yes | Market app secret |
| `SCHWAB_MARKET_CALLBACK_URL` | SaaS only | OAuth callback for market app (e.g. `.../api/oauth/schwab/market/callback`) |

## Account App (Orders, Balances)

| Env Var | Required | Description |
|---------|----------|-------------|
| `SCHWAB_ACCOUNT_APP_KEY` | Yes | Account app key from Schwab Developer Portal |
| `SCHWAB_ACCOUNT_APP_SECRET` | Yes | Account app secret |
| `SCHWAB_CALLBACK_URL` | Yes | OAuth callback URL (default `https://127.0.0.1:8182` for local) |

## Token Security

| Env Var | Default | Description |
|---------|---------|-------------|
| `SCHWAB_TOKEN_ENCRYPTION_KEY` | none | Optional encryption key for token files |
| `CREDENTIAL_ENCRYPTION_KEY` | none | SaaS: encryption key for per-user credentials in DB |

## Token Files (Local Mode)
- `tokens_market.enc` — encrypted market session tokens
- `tokens_account.enc` — encrypted account session tokens
- Created by `python run_dual_auth.py`

## Related
- [[Schwab Auth]] — architecture overview
- [[Schwab OAuth Setup]] — setup runbook
- [[SaaS Infrastructure]] — SaaS-specific credential config
