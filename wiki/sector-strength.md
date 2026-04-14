---
source: Brain/Strategies/Sector Strength.md
created: 2026-04-13
updated: 2026-04-13
tags: [strategy, sector]
---

# Sector Strength

> Relative sector performance filter preferring stocks in sectors outperforming SPY.

## How It Works

1. `get_sector_heatmap()` computes relative performance of sector ETFs vs SPY
2. Scanner checks if stock's sector is "winning"
3. If `SECTOR_FILTER_ENABLED=true`, underperforming sectors are filtered out

## Sector Exposure Guard

`MAX_SECTOR_ACCOUNT_FRACTION` in [[guardrails]] caps portfolio allocation to a single sector.

## Dashboard

`GET /api/sectors` returns the sector heatmap data.

## Related Pages

- [[signal-scanner]] — sector filter in Stage B
- [[guardrails]] — sector fraction cap
- [[scanner-tunables]] — `SECTOR_FILTER_ENABLED`

---

*Last compiled: 2026-04-13*
