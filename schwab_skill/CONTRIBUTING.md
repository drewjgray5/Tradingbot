# Contributing

## Scope

- Work inside `schwab_skill/` only.
- Keep changes focused and atomic.
- Avoid broad refactors unless explicitly requested.

## Code Conventions

- Prefer explicit, descriptive names over compact abstractions.
- Keep side effects isolated and logged in trading/execution paths.
- Add concise comments only where logic is non-obvious.

## Validation Before Merge

Run locally before opening a PR:

```
python -m ruff check .
python -m pytest -q
python -m mypy .
```

## Security and Secrets

- Never commit `.env`, token files, certs, or encrypted credential blobs.
- Use `.env.example` as the only committed configuration template.
