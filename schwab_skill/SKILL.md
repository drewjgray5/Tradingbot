---
name: schwab-api
description: Interact with Schwab Developer API for trading. Handles OAuth2 auth, token refresh, and enforces guardrails (defaults: max $500k account, $50k per ticker, 20 trades/day—configurable in .env). Use when placing trades via Schwab, checking accounts, or managing Schwab API auth. OpenClaw operator prompt: openclaw_operator_prompt.txt
metadata:
  {"openclaw":{"requires":{"bins":["python3"],"env":["SCHWAB_MARKET_APP_KEY","SCHWAB_ACCOUNT_APP_KEY"]},"emoji":"📈"}}
---

# Schwab API Skill

## Critical: Guardrail Wrapper

**All trade requests MUST go through the GuardrailWrapper.** Never call Schwab's place-order API directly. The wrapper enforces:

- Maximum total account value: $500,000 (configurable via MAX_TOTAL_ACCOUNT_VALUE)
- Maximum position per ticker: $50,000 (configurable via MAX_POSITION_PER_TICKER)
- Maximum trades per day: 20 (configurable via MAX_TRADES_PER_DAY)

Exceeding any limit blocks the API call and returns an error string to the agent.

## Setup

1. Copy `.env.example` to `.env` and add your Schwab app key and secret.
2. Run initial OAuth (one-time): `python run_auth.py` from the skill directory.
   Or programmatically:
   ```python
   import sys; sys.path.insert(0, "{baseDir}")
   from client import get_client
   c = get_client()
   url = c.auth.get_authorization_url()
   # User opens url in browser, logs in, copies redirect URL
   c.auth.complete_initial_auth("https://127.0.0.1/?code=...")
   ```

## Placing Trades

```python
import sys; sys.path.insert(0, "{baseDir}")
from client import get_client

client = get_client()
result = client.place_order(
    ticker="AAPL",
    qty=10,
    side="BUY",
    order_type="MARKET",
    limit_price=None  # required for LIMIT orders only
)
# result is either success dict or error string
if isinstance(result, str):
    # Blocked by guardrail, sector filter, or API error
    print(result)
```

Or call execution directly:
```python
from execution import place_order
result = place_order("AAPL", 10, "BUY", "MARKET")
```

## Getting Accounts

```python
import sys; sys.path.insert(0, "{baseDir}")
from client import get_client

client = get_client()
accounts = client.get_accounts()
```

## TradingSkill Tools (OpenClaw)

Load `TradingSkill.py` for three agent-callable tools:

- **analyze_ticker_trend(ticker, days=300)** — Fetches OHLCV, runs Stage 2 and VCP volume checks.
- **get_account_status()** — Returns Schwab account details and IDs.
- **execute_trade(ticker, qty, side, order_type, limit_price=None)** — Places orders via GuardrailWrapper. On guardrail block, returns the exact plain-text error; the agent MUST pass this to the user and log the restriction.

Invoke via Python:
```python
import sys; sys.path.insert(0, "{baseDir}")
from TradingSkill import analyze_ticker_trend, get_account_status, execute_trade
result = analyze_ticker_trend("AAPL", 300)
```

Or use `TradingSkill.get_tools()` for framework tool discovery.

## File Layout

- `TradingSkill.py` - OpenClaw tools (analyze_ticker_trend, get_account_status, execute_trade)
- `auth.py` - OAuth2, token refresh (25 min), encrypted storage
- `execution.py` - GuardrailWrapper, place_order (single consolidated path; sector filter, trailing stop, fill monitor)
- `guardrail.py` - Re-exports GuardrailWrapper from execution (legacy)
- `client.py` - Entry point: `get_client()` -> ClientFacade
