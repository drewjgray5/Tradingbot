# Schwab Trading Bot - OpenClaw Skill

Dual-API OAuth2, guardrails, Stage 2 logic, and Discord notifications.

## Architecture

| Module | Purpose |
|--------|---------|
| `logger_setup.py` | Console + rotating 5MB `trading_bot.log` |
| `notifier.py` | Discord alerts via webhook — varied kinds (heartbeat, signal, order, guardrail, hold, etc.) |
| `schwab_auth.py` | **Dual sessions**: Market (OHLCV) + Account (orders/balances) |
| `market_data.py` | `get_daily_history()`, `get_current_quote()` — Market Session only |
| `stage_analysis.py` | `is_stage_2()`, `check_vcp_volume()` — TA-Lib or pandas SMAs |
| `signal_scanner.py` | Two-stage scan pipeline: Stage A fast filter + Stage B heavy enrichment |
| `execution.py` | Guardrail Wrapper, `place_order()` — Account Session only |
| `main.py` | Daily heartbeat 9:25 AM, crash alert |
| `TradingSkill.py` | OpenClaw @tool: analyze_ticker_trend, get_account_status, execute_trade |

## Setup

1. Copy `.env.example` to `.env` and fill in:
   - `DISCORD_WEBHOOK_URL`, `DISCORD_USER_ID`
   - `SCHWAB_MARKET_APP_KEY`, `SCHWAB_MARKET_APP_SECRET`
   - `SCHWAB_ACCOUNT_APP_KEY`, `SCHWAB_ACCOUNT_APP_SECRET`
   - `SCHWAB_CALLBACK_URL=https://127.0.0.1:8182` (must match app registration; browser auth uses port 8182)

2. Run dual OAuth: `python run_dual_auth.py` (complete both sessions)

3. `pip install -r requirements.txt`  
   TA-Lib optional: `pip install TA-Lib` (fallback to pandas if missing)

## Self-Study

The bot learns from trade outcomes: records filled BUY/SELL prices, computes round-trip returns, and analyzes performance by MiroFish conviction band and sector. With enough data (5+ round trips), it suggests a minimum conviction threshold; when `SELF_STUDY_ENABLED=true`, signals below that threshold are filtered out. Runs automatically at 4:00 PM ET; or manually: `python scripts/run_self_study.py`.

## Hypothesis ledger & outcome scoring (calibration)

Use this to score **decision quality** for scanner signals (and optional full-report conclusions), separate from P&L.

1. Enable recording: set `HYPOTHESIS_LEDGER_ENABLED=true` in `.env`. Signals that actually send an alert append a row to `.hypothesis_ledger.json` with ticker, source, model id, input fingerprint, and structured prediction (direction, reference price, levels).
2. Full reports: `python full_report.py TICKER --record-hypothesis` appends one record when the flag above is on.
3. After enough calendar time, run `python scripts/score_hypothesis_outcomes.py` (scheduled or manual). Horizons default to T+1, T+5, T+20 trading days; override with `HYPOTHESIS_SCORE_HORIZONS=1,5,20` in `.env`.
4. Review aggregates: with `HYPOTHESIS_SELF_STUDY_MERGE=true`, `run_self_study()` adds `hypothesis_calibration` (hit rates by source) into `.self_study.json`.
5. Optional promotion gate: `HYPOTHESIS_PROMOTION_GUARD_ENABLED=true` causes `scripts/decide_and_promote_advisory_model.py` to veto promotion when combined scored hypotheses for **`signal_scanner` + `advisory`** fall below `HYPOTHESIS_PROMOTION_MIN_HIT_RATE` (requires at least `HYPOTHESIS_PROMOTION_MIN_N` scored outcome rows).

## Data quality & degraded execution

Market/SEC freshness is summarized as `data_quality` (`ok` | `degraded` | `stale` | `conflict`) plus `data_quality_reasons`. Scanner diagnostics, Discord scan summaries, and live order responses (`_data_quality` / shadow result) include this when available.

- **Defaults:** `DATA_QUALITY_EXEC_POLICY=off` — no execution change.
- **Strict:** set `DATA_QUALITY_EXEC_POLICY=block_risk_increasing` to block new BUY/opening risk at the **guardrail wrapper** when quality is not `ok` (exits/reducing orders still allowed). Use `warn` for log-only.
- Tunables: `DATA_QUOTE_MAX_AGE_SEC`, `DATA_BAR_MAX_STALENESS_DAYS`, `DATA_EDGAR_MAX_AGE_HOURS`, optional `DATA_CROSSCHECK_ENABLED` (Schwab quote vs yfinance last).

## Guardrails

- **Configurable via .env**: `MAX_TOTAL_ACCOUNT_VALUE`, `MAX_POSITION_PER_TICKER`, `MAX_TRADES_PER_DAY`
- Defaults: $500k total, $50k per ticker, 20 trades/day
- **Sector filter**: Only trades in sectors outperforming SPY (set `SECTOR_FILTER_ENABLED=false` to disable)

On block: returns error string, sends Discord warning. BUY orders get 7% trailing stop; success alert on fill. Trailing stop failure triggers error alert.

## Simulation Viewer

Each MiroFish run gets a unique Simulation ID. Discord shows a link like `http://localhost:3000/simulation/sim_abc123` to open the interactive view. Run the viewer server:

```
python scripts/simulation_viewer.py
```

Configure `SIMULATION_VIEWER_URL` in `.env` if using a different host/port.

## Discord

- **Webhook:** Alerts use distinct types (heartbeat, signal, order_filled, guardrail, hold_reminder, etc.) with different colors and emojis.
- **Slash command:** Users run `/scan` in Discord to trigger a new signal scan on demand. Requires `applications.commands` scope when inviting the bot.

## Web Dashboard

A FastAPI-powered website is available under `webapp/` with a modern UI for:
- status (market/account token health)
- running scans + reviewing diagnostics
- queueing pending trades from scan results
- approve/reject workflows backed by SQLite persistence
- portfolio and sector snapshots
- quick ticker checks

The main dashboard (`webapp/static/index.html`) is organized around **today’s workflow** (health → blockers → scan & pending approvals → quick ticker check), with a **Tools** grid for jumping to deeper capabilities. **Simple / Standard / Pro** (saved in `localStorage`) trims or expands what appears above the fold; diagnostics, detailed status, SEC compare depth, and several panels use a shared **disclosure** pattern and **lazy-load** their backing API calls when sections scroll into view—**Refresh All** still loads everything. Preset **Expert** mode remains the server-backed `standard` vs `expert` setting on `/api/settings/profile` (separate from the display layout).

Run:

```
uvicorn webapp.main:app --reload --port 8000
```

Then open:

```
http://127.0.0.1:8000
```

**Legal:** The dashboard footer links to **`/static/legal.html`** (not investment advice, third-party trademark notice, risk and “as is” terms). Operators should review **`docs/LEGAL_DISCLOSURES.md`** and have counsel adapt it for your jurisdiction and offering.

## Multi-tenant SaaS API (`webapp/main_saas.py`)

Production-oriented API: Supabase JWT auth, encrypted per-user Schwab tokens, Postgres-friendly pooling, Celery workers with **separate queues** (`scan`, `orders`), Redis-backed scan cooldown and rate limits, audit log table, and per-request `X-Request-ID`.

**Live execution defaults:** New users have `live_execution_enabled=false` until they call `POST /api/settings/enable-live-trading` (risk checkbox + typing `ENABLE`) with Schwab account **and** market materialization ready (same bar as scans). Broker orders are **not** accepted via `POST /api/orders/execute` (returns 410); use `POST /api/pending-trades` then `POST /api/trades/{id}/approve` with JSON `{"typed_ticker":"TICKER"}` so each live order requires an explicit in-app confirmation. The hosted dashboard exposes the same controls under Strategy Presets.

**Run API (from `schwab_skill/`):**

```
uvicorn webapp.main_saas:app --host 0.0.0.0 --port 8000
```

**Run workers** (same working directory, same env as API):

```
celery -A webapp.tasks worker -Q scan,orders,celery --loglevel=info
```

**Database migrations (Postgres or existing SQLite file):**

```
alembic upgrade head
```

Empty Postgres: run once `python scripts/saas_bootstrap.py` or set `SAAS_BOOTSTRAP_SCHEMA=1` for a single API boot (creates schema + stamps `saas002`). Set `SAAS_RUN_ALEMBIC=1` on API startup to auto-run `alembic upgrade head` (optional). Local SQLite still auto-creates tables via SQLAlchemy when `DATABASE_URL` is sqlite.

**Schwab + SaaS:**

- Platform registers **market** and **account** developer apps; set `SCHWAB_MARKET_*` and `SCHWAB_ACCOUNT_*` on API and workers.
- **Browser OAuth:** Set `SCHWAB_CALLBACK_URL` (account app) and `SCHWAB_MARKET_CALLBACK_URL` (market app, e.g. `…/api/oauth/schwab/market/callback`). The hosted dashboard shows **Connect Schwab (account)** and **Connect Schwab (market)** in **Connect Schwab & setup**, with plain-language copy and a default **Schwab setup guide** at `/static/connect-schwab-guide.html` unless `WEB_IMPLEMENTATION_GUIDE_URL` is set (`docs/CONNECT_SCHWAB_END_USERS.md`).
- **API upload:** Each user can POST `/api/credentials/schwab` with `account_oauth_json` and `market_oauth_json` (encrypted at rest), or legacy `access_token` / `refresh_token` (account only) plus **`SAAS_PLATFORM_MARKET_SKILL_DIR`** for a shared platform `tokens_market.enc` if you skip per-user market OAuth.

**Health:** `GET /api/health/live`, `GET /api/health/ready` (DB + Redis; set `SAAS_HEALTH_REQUIRE_REDIS=0` to skip Redis in dev).

**Deploy:** see `docs/SAAS_DEPLOYMENT.md` and `docker-compose.saas.yml`.

## Run

- Heartbeat/scheduler: `python main.py`
- OpenClaw: copy to `~/.openclaw/skills/schwab-api/` or `workspace/skills/`

## Validation

Unified validation pipeline:

```
python scripts/validate_all.py --profile local --strict
```

Hardening pipeline (includes plugin-mode checks, execution quality, exit manager, event risk, regime v2):

```
python scripts/validate_all.py --profile local --strict --skip-backtest
```

Additional references:
- `VALIDATION_MATRIX.md` (environment gates and pass/fail criteria)
- `VALIDATION_RUNBOOK.md` (how to run validations in local/server/container/CI)
- `CANARY_RUNBOOK.md` (controlled live canary and rollback rules)

## Continuous Strategy Improvement (Hybrid)

Fast CI checks (PR/push):

```
python scripts/validate_signal_quality.py
python scripts/validate_scanner_parallelization.py
python scripts/validate_shadow_mode.py
```

Scheduled CI validation (artifact-producing):

```
python scripts/validate_all.py --profile ci --skip-backtest --strict
```

Server heavy cycle (nightly/weekly):

```
python scripts/run_continuous_strategy_cycle.py --strict
```

This heavy cycle runs:
- `validate_all.py --profile server --strict`
- `run_strategy_tune_cycle.py` (dry-run promotion decision)
- `validate_backtest.py --promotion --warn-on-regression`

A compact status artifact is written to:
- `validation_artifacts/continuous_validation_status.json`

Dashboard/API exposure:
- `GET /api/status` -> `validation_status`
- `GET /api/validation/status`

### Manual Promotion Guard

Promotion apply is blocked by default in unattended environments.  
To explicitly allow apply for a one-off operator run:

```
MANUAL_PROMOTION_APPROVED=1 python scripts/decide_strategy_promotion.py --challenger-artifact <artifact> --apply
MANUAL_PROMOTION_APPROVED=1 python scripts/decide_and_promote_advisory_model.py --apply --strict --promotion
```

Guarded scripts:
- `scripts/run_strategy_tune_cycle.py`
- `scripts/decide_strategy_promotion.py`
- `scripts/run_advisory_retrain_cycle.py`
- `scripts/decide_and_promote_advisory_model.py`

## Advisory Model (Phase 1)

Phase 1 is advisory-only and adds calibrated `P(up in 10d)` to scan results.
Execution behavior is unchanged.

Champion/challenger workflow:

```
python scripts/train_and_evaluate_challenger.py --profile promotion --allow-model-upgrades --strict --promotion
python scripts/decide_and_promote_advisory_model.py --strict --promotion
```

Apply a promotion (atomic backup + activate):

```
python scripts/decide_and_promote_advisory_model.py --strict --promotion --apply --notify
```

Scheduled wrappers:

```
python scripts/scheduled_advisory_weekly.py         # dry-run by default
python scripts/scheduled_advisory_weekly.py --apply # apply if challenger qualifies
python scripts/scheduled_advisory_daily_report.py
```

Train model artifact:

```
python scripts/train_advisory_model.py --max-tickers 250
```

Promotion-grade training (denser folds + model-upgrade candidate path):

```
python scripts/train_advisory_model.py --profile promotion --allow-model-upgrades --max-tickers 250
```

Validate model gates:

```
python scripts/validate_advisory_model.py --strict
```

Promotion gate validation:

```
python scripts/validate_advisory_model.py --strict --promotion
```

Promotion flow and scanner concurrency checks:

```
python scripts/validate_scanner_parallelization.py
python scripts/validate_promotion_flow.py
```

Spec:
- `ADVISORY_MODEL_SPEC.md` (feature/label schema, walk-forward setup, acceptance gates)

## Plugin Modes (OFF | SHADOW | LIVE)

All new plugins follow the same rollout pattern:
- `off`: legacy behavior preserved.
- `shadow`: compute + diagnostics only, no behavior changes.
- `live`: enforce gates/resize/actions.

Current plugin modes:
- `EXEC_QUALITY_MODE`
- `EXIT_MANAGER_MODE`
- `EVENT_RISK_MODE`
- `CORRELATION_GUARD_MODE` (config-ready)
- `REGIME_V2_MODE`

Defaults are `off`, so production behavior remains legacy until explicitly enabled.

## Recommended Rollout Sequence

1. `EXEC_QUALITY_MODE=shadow` -> `live`
2. `EVENT_RISK_MODE=shadow` -> `live`
3. `REGIME_V2_MODE=shadow` -> `live`
4. `EXIT_MANAGER_MODE=shadow` -> `live`
5. Enable `CORRELATION_GUARD_MODE` only after implementation is live-tested.

Promote one plugin at a time, hold for at least one full market week, then proceed.

## Env Var Reference (New Plugins)

Execution Quality:
- `EXEC_QUALITY_MODE=off|shadow|live` (default `off`)
- `EXEC_SPREAD_MAX_BPS` (default `35`)
- `EXEC_SLIPPAGE_MAX_BPS` (default `20`)
- `EXEC_REPRICE_ATTEMPTS` (default `2`)
- `EXEC_REPRICE_INTERVAL_SEC` (default `3`)
- `EXEC_USE_LIMIT_FOR_LIQUID` (default `true`)

Exit Manager:
- `EXIT_MANAGER_MODE=off|shadow|live` (default `off`)
- `EXIT_PARTIAL_TP_R_MULT` (default `1.5`)
- `EXIT_PARTIAL_TP_FRACTION` (default `0.5`)
- `EXIT_BREAKEVEN_AFTER_PARTIAL` (default `true`)
- `EXIT_MAX_HOLD_DAYS` (default `12`)

Event Risk:
- `EVENT_RISK_MODE=off|shadow|live` (default `off`)
- `EVENT_BLOCK_EARNINGS_DAYS` (default `2`)
- `EVENT_MACRO_BLACKOUT_ENABLED` (default `false`)
- `EVENT_ACTION=block|downsize` (default `block`)
- `EVENT_DOWNSIZE_FACTOR` (default `0.5`)
- Optional macro date sources:
  - `EVENT_MACRO_BLACKOUT_DATES=YYYY-MM-DD,YYYY-MM-DD`
  - `.macro_event_blackouts.json` (`{"dates": [...]}`)

Regime v2:
- `REGIME_V2_MODE=off|shadow|live` (default `off`)
- `REGIME_V2_ENTRY_MIN_SCORE` (default `55`)
- `REGIME_V2_SIZE_MULT_HIGH` (default `1.0`)
- `REGIME_V2_SIZE_MULT_MED` (default `0.7`)
- `REGIME_V2_SIZE_MULT_LOW` (default `0.4`)

Strategy Tune/Promotion:
- `python scripts/run_strategy_tune_cycle.py`
- `python scripts/run_optimization_batch.py --runs 24 --timeout-seconds 3600`
- `python scripts/rank_optimization_candidates.py --min-oos-pf 1.15 --min-trades 35`
- `python scripts/validate_pf_robustness.py --fast-smoke`

## Canary and Rollback

Canary:
1. Set plugin mode to `shadow`, run for 3-5 sessions.
2. Confirm diagnostics trend is stable (no sharp jump in block/error rates).
3. Promote to `live` for a reduced-size canary account/session.
4. Run `python scripts/validate_all.py --profile local --strict --skip-backtest` daily during canary week.

Rollback:
1. Switch affected plugin mode back to `off`.
2. Re-run validation pipeline.
3. Verify execution events return to baseline (`validate_observability_gates` details).
4. If strategy promotion was applied, restore prior champion params/artifact.

## Weekly Operational Checklist

Review these every week before increasing rollout scope:
- PF (`profit_factor_net`) trend and OOS PF stability.
- Expectancy (`avg_return_net_pct`) non-negative and not degrading.
- Worst drawdown (`max_drawdown_net_pct`) within cap.
- Slippage/spread pressure (`exec_quality_*` events and diagnostics).
- Block/resize rates:
  - event risk (`event_risk_blocked`, `event_risk_downsized`)
  - regime v2 (`regime_v2_blocked`, `regime_v2_sized`)
  - guardrails (`guardrail_blocked_order`)

## Engineering Baseline

This repository now includes a standard local quality toolchain:

- Lint: `python -m ruff check .`
- Format: `python -m ruff format .`
- Test: `python -m pytest -q`
- Typecheck: `python -m mypy .`

Install dependencies:

```
pip install -r requirements-dev.txt
```

## Agent Execution Loop

Use this repeatable loop for safe autonomous changes:

1. **Inspect**: Identify the minimal set of files/symbols needed.
2. **Implement**: Make targeted, reversible changes.
3. **Verify**: Run lint, tests, and type checks.
4. **Summarize**: Report changed files, command outcomes, risks, and next actions.

See `AGENTS.md` for repository-specific agent rules.
