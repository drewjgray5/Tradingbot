---
tags: [api, local]
---
# Local Dashboard Endpoints

All routes from `schwab_skill/webapp/main.py`. Single-user FastAPI app with SQLite persistence.

## Pages

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Main dashboard (index.html) |
| GET | `/simple` | Simple scan + diagnostics UI |
| GET | `/login` | Sign-in page |

## Health & Config

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/health` | None | Basic health check (status + time) |
| GET | `/api/health/deep` | None | Full health: DB, tokens, quote probe, metrics |
| GET | `/api/public-config` | None | Non-secret client config (Supabase URL, kill switch) |
| GET | `/api/config` | None | API key requirement and CORS origins |
| GET | `/api/status` | None | Token status, last scan, validation status |
| GET | `/api/validation/status` | None | Continuous validation pipeline status |

## Scanning

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/scan` | None | Trigger scan (async by default) |
| GET | `/api/scan/status` | None | Current/last scan status + signals |

## Ticker Research

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/check/{ticker}` | None | Quick technical check |
| GET | `/api/report/{ticker}` | None | Full multi-section report |
| GET | `/api/decision-card/{ticker}` | None | Signal decision card (from current scan) |

Query params for `/api/report/{ticker}`: `section` (tech/dcf/comps/health/edgar/mirofish), `skip_mirofish`, `skip_edgar`

## SEC Filing Analysis

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/sec/analyze/{ticker}` | None | Analyze latest SEC filing |
| GET | `/sec/analyze/{ticker}` | None | Alias |
| GET | `/api/sec/compare` | None | Compare filings (ticker_vs_ticker or ticker_over_time) |
| GET | `/sec/compare` | None | Alias |

Query params for compare: `mode`, `ticker`, `ticker_b`, `form_type`, `highlight_changes_only`

## Portfolio

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/portfolio` | None | Account positions with P&L |
| GET | `/api/sectors` | None | Sector heatmap |

## Pending Trades

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/pending-trades` | None | List trades (filter by `status`, sort by `newest`/`oldest`) |
| POST | `/api/pending-trades` | None | Create pending trade (body: ticker, qty, price, signal, note) |
| POST | `/api/pending-trades/clear-pending` | X-API-Key | Reject all pending trades |
| POST | `/api/pending-trades/delete-all` | None | Delete all trades |
| POST | `/api/trades/{trade_id}/delete` | None | Delete single trade |
| POST | `/api/trades/{trade_id}/approve` | X-API-Key | Approve + execute (body: typed_ticker, confirm_live) |
| POST | `/api/trades/{trade_id}/reject` | X-API-Key | Reject trade |
| GET | `/api/trades/{trade_id}/preflight` | None | Pre-trade checklist |

## Settings & Onboarding

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/settings/profiles` | None | Current profile + catalog |
| POST | `/api/settings/profile` | None | Set profile, mode, automation opt-in |
| POST | `/api/onboarding/start` | None | Initialize onboarding state |
| POST | `/api/onboarding/step/{step}` | None | Run onboarding step (connect/verify/scan/paper_order) |
| GET | `/api/onboarding/status` | None | Onboarding progress |

## Performance & Calibration

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/performance` | None | Backtest, shadow, live metrics (separated) |
| GET | `/api/calibration/summary` | None | Calibration snapshot |

## Recovery

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/recovery/map` | None | Map error string to recovery steps |

## Key File
`schwab_skill/webapp/main.py`

## Related
- [[WebApp Dashboard]] — architecture overview
- [[API Reference MOC]]
