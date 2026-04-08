# Schwab OAuth Login Troubleshooting

## "Unable to login" – what it might mean

### 1. Use the browser-based flow (no copy/paste)
Run:
```
python run_dual_auth_browser.py
```
This opens a local callback server so the browser redirect is captured automatically.

**First:** Add `https://127.0.0.1:8182` to both apps in [Schwab Developer Portal](https://developer.schwab.com) → My Apps → App Details → Callback URL.

When the browser shows a security warning (self-signed cert), choose **Advanced** → **Proceed to 127.0.0.1**.

---

### 2. Callback URL must match exactly
- App callback in portal: `https://127.0.0.1:8182`  
- Code must use the same URL (run_dual_auth_browser.py does this)
- No trailing slash
- Use `127.0.0.1`, not `localhost`

---

### 3. App status
- Each app must be **"Ready for use"** in the portal
- If still pending, wait for approval
- Callback changes can require re-approval

---

### 4. Schwab login (username/password)
- Use your normal Schwab brokerage login
- Complete 2FA or security codes if prompted
- Use a normal browser (Chrome, Edge, Firefox)

---

### 5. VPN / network
- Turn off VPN during OAuth
- Try a different network if possible

---

### 6. Manual flow (copy/paste)
If the browser flow fails:
```
python run_dual_auth.py
```
1. Open the printed URL
2. Log in and approve
3. Copy the **entire** redirect URL (e.g. `https://127.0.0.1/?code=...`)
4. Paste as soon as you can (the code expires quickly)

---

### 7. "Bad authorization code"
- Code is single-use and short-lived
- Use `run_dual_auth_browser.py` for automatic capture
- Or paste immediately when using the manual flow

---

### 8. Trader API 400 / "Internal Server Error" (Account fetch fails)

**Symptom:** Market Session OK, Account Session OK, but "Account Fetch" fails with 400.

**Cause:** The Account app may not have Trader API access or your brokerage account may not be linked.

**Fix:**
1. Go to [Schwab Developer Portal](https://developer.schwab.com) → My Apps
2. Open your **Account** app (the one with `SCHWAB_ACCOUNT_APP_KEY`)
3. Under **API Products**, ensure **"Accounts and Trading Production"** (or **Trader API - Individual**) is included
4. Under **Linked Accounts**, ensure your brokerage account is linked
5. If the app is "Approved Pending", wait for full approval
6. If still failing, email traderapi@schwab.com with your app key and error details

---

### 8a. 401 Unauthorized (token expired)

**Symptom:** `401 Client Error: Unauthorized for url: .../accounts?fields=positions` or on order placement.

**Cause:** Account session token expired (tokens last ~30 min). The bot now auto-refreshes on 401; if it persists, re-auth.

**Fix:** Delete `tokens_account.enc` and run `python run_dual_auth_browser.py` to re-authenticate.

---

### 8b. "Invalid account number" (400) when placing orders

**Symptom:** Order placement fails with `API Error: 400 - {"message":"Invalid account number"}`.

**Cause:** The Trader API requires the account **hashValue** from `/accounts/accountNumbers`, not the plain account number from `GET /accounts`. The bot now fetches the hash automatically; if it still fails, set it manually.

**Fix:**
1. From `schwab_skill/`, run: `python scripts/get_account_hash.py`
2. Add the printed line to your `.env`
3. Restart the bot

---

### 9. Trade confirmation bot (Approve/Reject in Discord)

**Setup:**
1. Go to [Discord Developer Portal](https://discord.com/developers/applications) → New Application (or use existing)
2. Go to **Bot** → Add Bot → Copy the token → add to `.env` as `DISCORD_BOT_TOKEN`
3. Go to **OAuth2** → URL Generator → Scopes: **`bot`** and **`applications.commands`**; Bot Permissions: `Send Messages`, `Embed Links`
4. Open the generated URL and invite the bot to your server
5. In Discord, right-click the channel for trade confirmations → Copy Channel ID (enable Developer Mode in Settings)
6. Add `DISCORD_CONFIRM_CHANNEL_ID=123456789...` to `.env`
7. (Optional) For instant `/scan` command: right-click your server → Copy Server ID → add `DISCORD_GUILD_ID=...` to `.env`

**Slash command `/scan`:** Users can run `/scan` in any channel to trigger a new signal scan. Commands sync on bot startup; with `DISCORD_GUILD_ID` set they appear instantly; without it, global sync can take up to an hour.

**"The application did not respond":** Usually the bot’s event loop was blocked (e.g. Schwab API during position sizing while building a confirm embed). The bot now runs that work in a background thread. If it still happens, try `/ping` — if that works but `/scan` fails, the scan may be timing out; restart the bot and ensure only one instance is running with your token.

**Confirm channel ≠ Webhook channel:** The webhook posts to one channel; the bot posts Approve/Reject to `DISCORD_CONFIRM_CHANNEL_ID`. Use the same channel if you want both in one place.

**Bot not sending?** Check: (1) Bot is in the server, (2) Bot has Send Messages + Embed Links in that channel, (3) Channel ID is correct (right-click channel → Copy ID), (4) `applications.commands` scope was used when inviting.

**Fallback:** If the bot isn't configured, signals are sent via webhook with a manual execute command.

---

### 10. Text commands (`!report`, `!scan`, `!ping`, `!help`)

The bot supports text-based commands in the confirm channel (e.g. `!report AAPL`). These require the **Message Content Intent** to be enabled:

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your bot application
3. Go to **Bot** tab
4. Scroll to **Privileged Gateway Intents**
5. Enable **Message Content Intent**
6. Save and restart the bot

Without this, the bot will see messages but their `.content` will be empty, so no `!` commands will work. Slash commands (`/report`, `/scan`) do NOT need this intent and will continue to work either way.
