---
source: Brain/Architecture/Discord Integration.md
created: 2026-04-13
updated: 2026-04-13
tags: [architecture, discord, alerts]
---

# Discord Integration

> Webhook-based alerts for signals, crashes, and system events.

## Alert Types

- Signal alerts with conviction and score
- Crash alerts with error details
- System status notifications

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `DISCORD_WEBHOOK_URL` | none | Discord webhook URL |
| `DISCORD_USER_ID` | none | User ID for @mention pings |
| `ALERT_MIN_CONVICTION` | 20 | Min conviction for any alert |
| `ALERT_PING_CONVICTION` | 50 | Conviction threshold for @ping |
| `ALERT_PING_SCORE` | 60 | Score threshold for @ping |

## Ping Logic

Signals with conviction >= `ALERT_PING_CONVICTION` AND score >= `ALERT_PING_SCORE` trigger an @mention. Below `ALERT_MIN_CONVICTION`, no alert is sent.

## Related Pages

- [[signal-scanner]] — source of signal alerts
- [[feature-flags]] — alert threshold config
- [[system-overview]] — where Discord fits

---

*Last compiled: 2026-04-13*
