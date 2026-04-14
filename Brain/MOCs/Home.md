---
tags: [moc, home]
---
# TradingBot Brain

Central knowledge base for the Schwab Trading Bot project.

## Navigation

### System Knowledge
- [[Architecture MOC]] — how the system is built
- [[Trading Strategy MOC]] — why we trade the way we do
- [[Config Reference MOC]] — every tunable knob and env var

### Operations
- [[Operations MOC]] — runbooks, deployment, validation
- [[API Reference MOC]] — all HTTP endpoints

### Working Notes
- **Decisions** — `Decisions/` folder for ADRs
- **Journal** — `Journal/` folder for daily trading notes

## Quick Links

| Action | Link |
|--------|------|
| Run a scan | [[WebApp Dashboard]] > `POST /api/scan` |
| Check a ticker | [[Local Dashboard Endpoints]] > `GET /api/check/{ticker}` |
| Deploy to Render | [[Deployment]] |
| Troubleshoot auth | [[Schwab OAuth Setup]] |
| Tune scanner | [[Scanner Tunables]] |
| Rollout a plugin | [[Plugin Modes]] > Recommended Rollout Sequence |
