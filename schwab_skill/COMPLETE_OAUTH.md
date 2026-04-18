# Complete OAuth (safe default flow)

Use this page when you want the fastest reliable setup with minimal guesswork.

## Fastest safe path (checklist)

1. In `schwab_skill/.env`, set the required vars:
   - `SCHWAB_MARKET_APP_KEY`
   - `SCHWAB_MARKET_APP_SECRET`
   - `SCHWAB_ACCOUNT_APP_KEY`
   - `SCHWAB_ACCOUNT_APP_SECRET`
   - `SCHWAB_CALLBACK_URL=https://127.0.0.1:8182`
   - `SCHWAB_MARKET_CALLBACK_URL=https://127.0.0.1:8182`
2. Run:

```bash
python scripts/fix_schwab_auth.py
```

3. Complete both browser prompts (market, then account).
4. Confirm success:
   - `tokens_market.enc` and `tokens_account.enc` exist.
   - `python healthcheck.py` reports both sessions healthy.

## Canonical OAuth flow (single source of truth)

1. Preflight validates required app credentials.
2. Old token files are removed (to avoid mixed-session state).
3. Browser OAuth runs for the market app and writes `tokens_market.enc`.
4. Browser OAuth runs for the account app and writes `tokens_account.enc`.
5. Healthcheck verifies both token paths are usable by runtime code.

This is the preferred path for local runtime and troubleshooting.

## Common misconfigurations

| Symptom | Likely cause | Fix |
|---|---|---|
| Callback mismatch error in browser | Registered redirect URI does not match env | Register and set `SCHWAB_CALLBACK_URL` / `SCHWAB_MARKET_CALLBACK_URL` exactly |
| Market works but account fails | Account app key/secret missing or swapped | Recheck `SCHWAB_ACCOUNT_APP_KEY` + `SCHWAB_ACCOUNT_APP_SECRET` |
| Account works but market fails | Market app key/secret missing or swapped | Recheck `SCHWAB_MARKET_APP_KEY` + `SCHWAB_MARKET_APP_SECRET` |
| Healthcheck fails after OAuth | Old/bad token files still present | Re-run `python scripts/fix_schwab_auth.py` (it cleans stale files first) |

## Required vs legacy/optional vars

Required for this flow:

- `SCHWAB_MARKET_APP_KEY`
- `SCHWAB_MARKET_APP_SECRET`
- `SCHWAB_ACCOUNT_APP_KEY`
- `SCHWAB_ACCOUNT_APP_SECRET`
- `SCHWAB_CALLBACK_URL`
- `SCHWAB_MARKET_CALLBACK_URL`

Legacy/optional:

- `SCHWAB_TOKEN_ENCRYPTION_KEY` (used for encrypted token file workflows)
- `SAAS_PLATFORM_MARKET_SKILL_DIR` (advanced SaaS fallback path, not needed for standard local OAuth)

## Legacy path (advanced only)

Use only if you intentionally manage tokens manually:

- `python run_dual_auth.py` (manual dual OAuth flow)
- direct token payload upload via SaaS credentials endpoints

For normal operation, keep using `scripts/fix_schwab_auth.py`.
