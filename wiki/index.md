# Wiki Index

> Central catalog of all knowledge compiled into this wiki.
> Every page in the wiki should be reachable from this index.

## Project Overview

- [[project-overview]] — High-level summary of the TradingBot project

## Architecture & Design

- [[system-overview]] — End-to-end pipeline diagram and component map
- [[schwab-auth]] — Dual OAuth2 sessions (market + account)
- [[signal-scanner]] — Two-stage scan pipeline (Stage A + Stage B)
- [[execution-engine]] — Order placement, guardrails, plugin hooks
- [[guardrails]] — Risk limits, circuit breaker, sector caps
- [[advisory-model]] — Calibrated P(up in 10 days) probability scoring
- [[database-schema]] — SQLAlchemy tables and relationships
- [[discord-integration]] — Webhook alerts and notification types
- [[webapp-dashboard]] — FastAPI local dashboard
- [[saas-api]] — Multi-tenant production API

## Trading Strategies

- [[stage-2-analysis]] — Weinstein Stage 2 trend qualification
- [[vcp-detection]] — Volume Contraction Pattern identification
- [[sector-strength]] — Relative sector performance filter
- [[signal-ranking]] — Composite scoring and top-N selection
- [[pead]] — Post-earnings announcement drift scoring
- [[forensic-accounting]] — Sloan, Beneish, Altman financial checks
- [[quality-gates]] — Weak signal filtering modes
- [[adaptive-stops]] — ATR-based trailing stop sizing
- [[plugin-modes]] — Execution, exit, event risk, regime, correlation plugins
- [[hypothesis-ledger]] — Decision quality tracking
- [[self-study]] — Automated trade outcome analysis

## Configuration Reference

- [[schwab-api-keys]] — OAuth credentials and token encryption
- [[scanner-tunables]] — Scanner pipeline env vars
- [[plugin-modes-config]] — Plugin mode env vars
- [[feature-flags]] — Boolean toggles and mode switches
- [[saas-infrastructure]] — Database, Redis, auth, billing env vars

## APIs & Integrations

- [[local-dashboard-endpoints]] — Local FastAPI routes
- [[saas-endpoints]] — SaaS multi-tenant routes
- [[tenant-dashboard-endpoints]] — Per-tenant API router

## Operations & Deployment

- [[deployment]] — How to deploy (Render, Docker, manual)
- [[schwab-oauth-setup]] — OAuth setup runbook
- [[troubleshooting]] — Common issues and recovery
- [[validation]] — Validation pipeline and matrix
- [[canary-rollout]] — Controlled live testing process
- [[signal-quality-rollout]] — Quality gate promotion plan
- [[backup-restore]] — Backup strategy, restore drills, and DR targets
- [[incident-response-saas]] — SaaS incident triage and escalation flow
- [[slo-alerting]] — SLI/SLO targets, alert thresholds, and error budget policy

---

*This index is automatically maintained. Every new wiki page must be linked here.*
