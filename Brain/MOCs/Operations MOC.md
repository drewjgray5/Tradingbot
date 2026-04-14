---
tags: [moc, operations]
---
# Operations MOC

How to deploy, validate, monitor, and troubleshoot the system.

## Deployment
- [[Deployment]] — SaaS deployment on Render, Docker, env setup

## Runbooks
- [[Canary Rollout]] — controlled live testing and rollback rules
- [[Validation]] — unified validation pipeline and matrix
- [[Signal Quality Rollout]] — quality gate promotion plan

## Authentication
- [[Schwab OAuth Setup]] — dual OAuth flow, browser auth, token files

## Troubleshooting
- [[Troubleshooting]] — common issues and recovery paths

## Monitoring
- [[WebApp Dashboard]] > `GET /api/health/deep` — system health check
- [[Local Dashboard Endpoints]] > `GET /api/validation/status` — validation pipeline status
- [[Local Dashboard Endpoints]] > `GET /api/performance` — backtest, shadow, and live metrics

## Engineering
- Lint: `python -m ruff check .`
- Format: `python -m ruff format .`
- Test: `python -m pytest -q`
- Typecheck: `python -m mypy .`
- See `schwab_skill/AGENTS.md` for agent execution rules
- See `schwab_skill/CONTRIBUTING.md` for contribution guidelines
