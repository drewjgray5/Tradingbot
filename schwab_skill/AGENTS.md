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

All work runs from `schwab_skill/` (in Cursor Cloud that is often `/workspace/schwab_skill`). Use `python3` — there is typically no `python` symlink.

### Services

| Service | How to run | Notes |
|---------|-----------|-------|
| **FastAPI dashboard** | `python3 -m uvicorn webapp.main:app --reload --port 8000` | Local single-user dashboard; SQLite by default. |
| **SaaS API** | `python3 -m uvicorn webapp.main_saas:app --host 0.0.0.0 --port 8000` | Multi-tenant mode; Postgres + Redis + Celery (see `docker-compose.saas.yml`). |

### Quality checks

See **Quality Commands** above. Ruff may report a pre-existing import-sort warning in `webapp/main_saas.py` (`--fix`). Mypy reports ~90 pre-existing type errors (e.g. `None`-safety in `forensic_accounting.py` and `signal_scanner.py`); known and not treated as blocking for routine work.

### SQLite + Alembic circular-dependency gotcha

When `webapp.main` is first imported, it runs `Base.metadata.create_all()` then `alembic upgrade head`. On a **fresh** SQLite file, batch migrations can hit a `CircularDependencyError`. **Before first dev server or full pytest run:**

```bash
cd /workspace/schwab_skill   # or your local path to schwab_skill
rm -f webapp/webapp.db
python3 -c "from webapp.db import Base, engine; Base.metadata.create_all(bind=engine)"
python3 -m alembic stamp head
```

This builds schema from the ORM and stamps Alembic to `head` so the problematic migration is skipped. After this, `python3 -m pytest -q` is the usual full suite; `python3 -m pytest -q --ignore=tests/test_smoke.py` skips smoke tests if you want to avoid DB setup.

### External credentials

Schwab, Discord, Stripe, and similar keys are **not** required for lint, tests, or dashboard UI exploration; broker integrations show as disconnected without them.
