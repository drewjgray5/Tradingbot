---
source: Brain/Runbooks/Schwab OAuth Setup.md
created: 2026-04-13
updated: 2026-04-13
tags: [runbook, auth]
---

# Schwab OAuth Setup

> Step-by-step guide for setting up Schwab API authentication.

## Local Setup

1. Register two apps at Schwab Developer Portal (market + account)
2. Set env vars in `schwab_skill/.env` (`SCHWAB_MARKET_APP_KEY`, etc.)
3. Run `python run_dual_auth.py` — complete browser auth for both sessions
4. Verify token files: `tokens_market.enc`, `tokens_account.enc`
5. Test: `python healthcheck.py`

## SaaS Setup

1. Set `SCHWAB_MARKET_*` and `SCHWAB_ACCOUNT_*` on API and workers
2. Set callback URLs
3. Users connect via dashboard OAuth flow
4. Tokens stored encrypted in `user_credentials` table

## Token Refresh

`DualSchwabAuth` handles auto-refresh. Full expiry: re-run auth (local) or reconnect (SaaS).

## Related Pages

- [[schwab-auth]] — architecture overview
- [[schwab-api-keys]] — env var reference
- [[troubleshooting]] — common auth issues

---

*Last compiled: 2026-04-13*
