---
tags: [moc, api]
---
# API Reference MOC

All HTTP endpoints exposed by the TradingBot web applications.

## Local Dashboard
- [[Local Dashboard Endpoints]] — single-user FastAPI app (`webapp/main.py`)
  - Health, status, config
  - Scan trigger and status
  - Ticker check and full report
  - SEC filing analysis and compare
  - Portfolio and sectors
  - Pending trade CRUD, approve/reject, preflight
  - Settings profiles, onboarding, calibration, performance

## SaaS API
- [[SaaS Endpoints]] — multi-tenant production API (`webapp/main_saas.py`)
  - Supabase JWT auth, session management
  - Celery-backed async scans and orders
  - Stripe billing webhooks
  - Credential upload (encrypted Schwab tokens)
  - Health probes (liveness + readiness)
  - Prometheus metrics

## Tenant Dashboard
- [[Tenant Dashboard Endpoints]] — per-tenant router (`webapp/tenant_dashboard.py`)
  - Schwab OAuth authorize/callback (account + market)
  - Portfolio, sectors, pending trades
  - Decision cards, reports, SEC compare
  - Settings profiles, onboarding
