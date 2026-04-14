---
tags: [runbook, troubleshooting]
---
# Troubleshooting

Common issues and recovery paths.

## Source Documentation
Full troubleshooting guide: `schwab_skill/TROUBLESHOOTING.md`

## Quick Diagnostics

| Check | How |
|-------|-----|
| Token health | `GET /api/health/deep` or `python healthcheck.py` |
| Scan status | `GET /api/scan/status` |
| Validation | `GET /api/validation/status` |
| System status | `GET /api/status` |

## Common Issues

### Schwab Auth Failures
- **Symptom**: `market_token_ok: false` or `account_token_ok: false`
- **Fix**: Re-run `python run_dual_auth.py` and complete browser auth
- See [[Schwab OAuth Setup]]

### Scan Blocked by Bear Regime
- **Symptom**: `scan_blocked_reason: bear_regime_spy_below_200sma`
- **Fix**: Wait for SPY to recover above 200 SMA, or set `SCAN_ALLOW_BEAR_REGIME=true` to override
- See [[Signal Scanner]] > Regime Gate

### Circuit Breaker Open
- **Symptom**: All Schwab API calls failing, `/api/health/deep` shows circuit breaker hint
- **Fix**: Wait 1-2 minutes, check network/DNS, then retry
- See [[Guardrails]] > Circuit Breaker

### No Signals Found
- **Symptom**: Scan completes but `signals_found: 0`
- **Check**: Review diagnostics — `stage2_fail`, `vcp_fail` counts show where signals are filtered
- **Fix**: Consider loosening thresholds or expanding watchlist
- See [[Scanner Tunables]]

### Data Quality Degraded
- **Symptom**: `data_quality: degraded` or `stale` in scan results
- **Fix**: Check Schwab API status, verify token freshness, review `DATA_QUOTE_MAX_AGE_SEC`
- See [[Feature Flags]] > Data Quality

## Recovery Map
The webapp includes a built-in recovery map that translates errors to actionable fixes:
- `GET /api/recovery/map?error=...&source=...`
- Used internally by the dashboard to show user-friendly error messages

## Related
- [[Operations MOC]] — operations overview
- [[Schwab OAuth Setup]] — auth-specific troubleshooting
- [[WebApp Dashboard]] — health endpoints
