---
tags: [runbook, auth]
---
# Schwab OAuth Setup

How to set up and maintain Schwab API authentication.

## Source Documentation
- Complete OAuth guide: `schwab_skill/COMPLETE_OAUTH.md`
- End-user connection guide: `schwab_skill/docs/CONNECT_SCHWAB_END_USERS.md`

## Local Setup (Single User)

### Prerequisites
1. Register two apps at the Schwab Developer Portal (market + account)
2. Note your app keys and secrets

### Steps
1. Set env vars in `schwab_skill/.env`:
   ```
   SCHWAB_MARKET_APP_KEY=...
   SCHWAB_MARKET_APP_SECRET=...
   SCHWAB_ACCOUNT_APP_KEY=...
   SCHWAB_ACCOUNT_APP_SECRET=...
   SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
   ```

2. Run dual OAuth flow:
   ```
   python run_dual_auth.py
   ```
   Complete browser-based auth for both sessions.

3. Verify token files created:
   - `tokens_market.enc`
   - `tokens_account.enc`

4. Test with healthcheck:
   ```
   python healthcheck.py
   ```

## SaaS Setup (Multi-Tenant)
- Platform registers market and account developer apps
- Set `SCHWAB_MARKET_*` and `SCHWAB_ACCOUNT_*` on API and workers
- Set callback URLs: `SCHWAB_CALLBACK_URL` (account) and `SCHWAB_MARKET_CALLBACK_URL` (market)
- Users connect via browser OAuth flow in the dashboard
- Tokens stored encrypted in `user_credentials` table

## Token Refresh
- `DualSchwabAuth` handles automatic token refresh on expiry
- If tokens expire completely, re-run `run_dual_auth.py` (local) or reconnect via dashboard (SaaS)

## Troubleshooting
- Check `/api/health/deep` for token and quote health status
- Run `python healthcheck.py` for local diagnostics
- See [[Troubleshooting]] for common auth issues

## Related
- [[Schwab Auth]] — architecture overview
- [[Schwab API Keys]] — env var reference
- [[Troubleshooting]]
