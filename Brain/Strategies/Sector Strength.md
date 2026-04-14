---
tags: [strategy, sector]
---
# Sector Strength

Relative sector performance filter that prefers stocks in sectors outperforming the broad market (SPY).

## Rationale
Stocks in strong sectors have institutional tailwinds. Trading with the sector trend improves the probability that a technical setup follows through.

## How It Works
1. `get_sector_heatmap()` in `sector_strength.py` computes relative performance of each sector ETF vs SPY
2. Scanner checks whether the stock's sector is "winning" (outperforming SPY)
3. If `SECTOR_FILTER_ENABLED=true`, stocks in underperforming sectors are filtered out

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `SECTOR_FILTER_ENABLED` | `true` | Enable/disable sector filter in scanner |
| `MAX_SECTOR_ACCOUNT_FRACTION` | `0` (disabled) | Max portfolio fraction in one sector |

## Sector Exposure Guard
`MAX_SECTOR_ACCOUNT_FRACTION` in the [[Guardrails]] layer caps how much of the account can be allocated to a single sector, preventing concentration risk. Set to 0 to disable. Uses yfinance-backed sector mapping (cached).

## Dashboard
- `GET /api/sectors` returns the sector heatmap data
- Dashboard renders a visual sector breakdown with relative strength indicators

## Related
- [[Signal Scanner]] — sector filter applied in Stage B enrichment
- [[Guardrails]] — sector fraction guardrail
- [[Scanner Tunables]] — `SECTOR_FILTER_ENABLED`
