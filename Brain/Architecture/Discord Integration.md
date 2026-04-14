---
tags: [architecture, discord]
---
# Discord Integration

Discord serves as the primary notification channel for the bot, delivering alerts about scans, trades, guardrail events, and system health.

## Notification Types

| Type | Color | When |
|------|-------|------|
| Heartbeat | Blue | Daily 9:25 AM ET startup |
| Signal | Green | New trading signal found |
| Order Filled | Green | Successful order execution |
| Guardrail Warning | Orange | Order blocked by risk limits |
| Hold Reminder | Yellow | Reminder to review open positions |
| Error / Crash | Red | Unhandled exception or system failure |
| Self-Study | Purple | Trade outcome analysis results |
| Weekly Digest | Blue | Weekly performance summary |

## Setup
1. Create a Discord webhook in your target channel
2. Set `DISCORD_WEBHOOK_URL` in `.env`
3. Set `DISCORD_USER_ID` for @mention pings on high-conviction signals

## Slash Commands
- `/scan` — trigger a signal scan on demand from Discord
- Requires `applications.commands` scope when inviting the bot

## Alert Thresholds
- `ALERT_MIN_CONVICTION` (default 20) — minimum conviction to send any alert
- `ALERT_PING_CONVICTION` (default 50) — threshold for @ping
- `ALERT_PING_SCORE` (default 60) — setup score threshold for @ping

## Simulation Links
When MiroFish integration is active, alerts include a link to the simulation viewer:
- Default: `http://localhost:3000/simulation/{sim_id}`
- Override: `SIMULATION_VIEWER_URL` in `.env`

## Key File
`schwab_skill/notifier.py`

## Related
- [[Signal Scanner]] — triggers signal alerts
- [[Execution Engine]] — triggers order and guardrail alerts
- [[Feature Flags]] — alert thresholds
