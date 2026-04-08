# One manual step: Complete OAuth

Open a terminal in this folder and run:

```
python run_dual_auth.py
```

For **each** session (Market, then Account):

1. Copy the URL printed
2. Open it in your browser
3. Log in to Schwab and approve
4. Copy the full redirect URL (e.g. `https://127.0.0.1/?code=...`)
5. Paste it when prompted

After both sessions complete, `tokens_market.enc` and `tokens_account.enc` will be created. You're done.
