# Prediction-Market Overlay Rollout

Prediction-market metadata is integrated as a Stage B context overlay only. It never creates a standalone trade trigger.

## Environment Variables

- `PRED_MARKET_ENABLED` (default `false`): master enable flag.
- `PRED_MARKET_MODE` (default `off`): `off`, `shadow`, or `live`.
- `PRED_MARKET_PROVIDER` (default `stub`): `stub` or `polymarket`.
- `PRED_MARKET_TIMEOUT_MS` (default `1200`): provider request timeout.
- `PRED_MARKET_CACHE_TTL_SEC` (default `300`): in-process cache TTL.
- `PRED_MARKET_MAX_EVENT_AGE_HOURS` (default `24.0`): stale-event cutoff.
- `PRED_MARKET_MIN_LIQUIDITY` (default `1000.0`): minimum market depth/liquidity.
- `PRED_MARKET_MAX_SPREAD` (default `0.08`): max spread threshold.

## Rollout Pattern

1. `off`: no behavior impact.
2. `shadow`: computes metadata and diagnostics only; score/confidence/sizing unchanged.
3. `live`: applies bounded overlay to score/advisory confidence/size multiplier.

## Safety Controls

- Timestamp-safe usage only (`as_of` at scan start).
- Excludes stale/resolved/illiquid/wide-spread events.
- Clamped impact:
  - score delta capped to +/-3 points
  - position-size multiplier capped to `[0.85, 1.15]`
  - advisory probability delta capped to +/-0.03
- Low-confidence metadata naturally decays toward zero effect.
- Provider failures/timeouts fail safe and produce diagnostics without breaking scans.

## Diagnostics Fields

Per signal:

- `signal["prediction_market"]` with provider status, matched event metadata, features, overlay, and exclusion reasons.
- `signal["prediction_market_size_multiplier"]` for optional downstream position sizing.

Scan diagnostics:

- `diagnostics["prediction_market"]` summary namespace (`enabled`, `mode`, `provider`, `processed`, `applied`, `skipped`, `errors`).
- counters:
  - `prediction_market_processed`
  - `prediction_market_applied`
  - `prediction_market_skipped`
  - `prediction_market_errors`
