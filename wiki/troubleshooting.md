---
source: Brain/Runbooks/Troubleshooting.md
created: 2026-04-13
updated: 2026-04-13
tags: [runbook, troubleshooting]
---

# Troubleshooting

> Common issues and recovery paths.

## Quick Diagnostics

| Check | How |
|-------|-----|
| Token health | `GET /api/health/deep` or `python healthcheck.py` |
| Scan status | `GET /api/scan/status` |
| Validation | `GET /api/validation/status` |

## Common Issues

### Schwab Auth Failures
Re-run `python run_dual_auth.py`. See [[schwab-oauth-setup]].

### Scan Blocked by Bear Regime
Wait for SPY > 200 SMA, or set `SCAN_ALLOW_BEAR_REGIME=true`. See [[signal-scanner]].

### Circuit Breaker Open
Wait 1-2 minutes, check network. See [[guardrails]].

### No Signals Found
Review diagnostics (`stage2_fail`, `vcp_fail`). Consider loosening thresholds. See [[scanner-tunables]].

### Data Quality Degraded
Check Schwab API status, verify token freshness. See [[feature-flags]].

## Recovery Map

`GET /api/recovery/map?error=...&source=...` translates errors to actionable fixes.

## Related Pages

- [[schwab-oauth-setup]] — auth troubleshooting
- [[webapp-dashboard]] — health endpoints

---

*Last compiled: 2026-04-13*
