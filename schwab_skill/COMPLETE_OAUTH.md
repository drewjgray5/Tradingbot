# Complete OAuth (best-fit flow)

Open a terminal in `schwab_skill/` and run:

```
python scripts/fix_schwab_auth.py
```

What this does for you:

1. Validates required Schwab app keys/secrets in `.env`
2. Ensures both callback vars are present (`SCHWAB_CALLBACK_URL`, `SCHWAB_MARKET_CALLBACK_URL`)
3. Deletes old token files
4. Runs browser OAuth for **Market** then **Account**
5. Runs `healthcheck.py` to confirm both endpoints are authorized

If you only want to preview what it will fix:

```
python scripts/fix_schwab_auth.py --dry-run
```
