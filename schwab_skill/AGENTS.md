# AGENTS.md

This file defines how autonomous coding agents should operate in this repository.

## Repository Scope

- Treat `schwab_skill/` as the repo root.
- Do not modify parent directories.
- Never commit secrets, token files, certs, or local runtime caches.

## Safe Working Rules

- Prefer small, reversible changes grouped by intent.
- Preserve existing behavior unless the task explicitly asks for behavior changes.
- If unexpected unrelated changes appear, stop and ask for direction.
- Never use destructive git commands without explicit approval.

## Standard Delivery Loop

1. **Inspect**: Read relevant files, identify constraints, and confirm assumptions.
2. **Implement**: Make the minimum coherent code/doc/config change.
3. **Verify**: Run `lint`, `test`, and `typecheck` commands when available.
4. **Summarize**: Report changed files, verification results, risks, and next steps.

## Quality Commands

**Fast loop (typical pre-push / IDE):**

- Lint: `python -m ruff check .`
- Format: `python -m ruff format .`
- Test: `python -m pytest -q`
- Typecheck: `python -m mypy .`
- Fixture chain smoke: `python scripts/validate_hypothesis_chain.py`

**Full validation (release / server profile):**

- `python scripts/validate_all.py --profile ci` (runs plugin validators, optional backtest, observability gates, etc.; see script `--help`).

If a tool is not installed, install from `requirements-dev.txt` before rerunning checks.

Frozen Schwab-shaped samples and scanner diagnostics live under `tests/fixtures/` for regression checks; extend `scripts/validate_hypothesis_chain.py` when adding new shapes the pipeline must accept.

## Cursor Cloud specific instructions

All commands below run from `schwab_skill/` (the effective repo root).

### Services

| Service | How to run | Notes |
|---------|-----------|-------|
| **FastAPI dashboard** | `python3 -m uvicorn webapp.main:app --reload --port 8000` | Local single-user dashboard; uses SQLite by default. |
| **SaaS API** | `python3 -m uvicorn webapp.main_saas:app --host 0.0.0.0 --port 8000` | Multi-tenant mode; requires Postgres + Redis + Celery (see `docker-compose.saas.yml`). |

### Quality checks

See the **Quality Commands** section above. Use `python3` instead of `python` in this environment. Ruff lint has one pre-existing import-sort warning in `webapp/main_saas.py` (auto-fixable with `--fix`). Mypy reports ~90 pre-existing type errors across 24 files; these are in the existing codebase and not regressions.

### SQLite migration gotcha

When `webapp.main` is first imported, it runs `Base.metadata.create_all()` followed by `alembic upgrade head`. On a **fresh** SQLite database this causes a `CircularDependencyError` because the ORM already created all columns and the batch-mode migration (saas005/saas006) tries to re-add them. To work around this before running pytest (which imports `webapp.main`):

```bash
rm -f webapp/webapp.db
python3 -c "from webapp.db import Base, engine; Base.metadata.create_all(bind=engine)"
python3 -m alembic stamp head
```

After this, `python3 -m pytest -q` passes all tests (41 tests). Alternatively, `python3 -m pytest -q --ignore=tests/test_smoke.py` runs the 39 non-smoke tests without any database setup.

### External credentials

Schwab API keys, Discord tokens, and Stripe keys are **not** required for local dev, lint, tests, or running the dashboard. The dashboard will show "Disconnected" for broker auth but is fully functional for UI exploration and scan workflow review.
