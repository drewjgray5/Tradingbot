---
source: Brain/Config/Schwab API Keys.md
created: 2026-04-13
updated: 2026-04-13
tags: [config, schwab, credentials]
---

# Schwab API Keys

> OAuth credentials, callback URLs, and token encryption environment variables.

## Credentials

| Env Var | Required | Description |
|---------|----------|-------------|
| `SCHWAB_MARKET_APP_KEY` | Yes | Market data OAuth app key |
| `SCHWAB_MARKET_APP_SECRET` | Yes | Market data OAuth app secret |
| `SCHWAB_ACCOUNT_APP_KEY` | Yes | Account OAuth app key |
| `SCHWAB_ACCOUNT_APP_SECRET` | Yes | Account OAuth app secret |
| `SCHWAB_CALLBACK_URL` | Yes | OAuth callback (default `https://127.0.0.1:8182`) |
| `SCHWAB_MARKET_CALLBACK_URL` | Optional | Market-specific callback (falls back to `SCHWAB_CALLBACK_URL`) |
| `SCHWAB_TOKEN_ENCRYPTION_KEY` | Optional | Encryption for local token files |
| `CREDENTIAL_ENCRYPTION_KEY` | SaaS | Encryption for DB-stored tokens |

## Related Pages

- [[schwab-auth]] — how auth works
- [[schwab-oauth-setup]] — setup runbook
- [[saas-infrastructure]] — other SaaS env vars

---

*Last compiled: 2026-04-13*
