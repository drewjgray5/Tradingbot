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
