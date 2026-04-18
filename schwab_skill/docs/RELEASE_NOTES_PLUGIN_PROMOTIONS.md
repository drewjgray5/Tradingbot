# Release Notes — Plugin Promotions

## 2026-Q2: `EXEC_QUALITY_MODE` and `EVENT_RISK_MODE` promoted to `live` (default)

**Date:** 2026-04-18
**Ledger:** `scripts/promotion_ledger.jsonl` (seq 1 + seq 2, chain verified)
**README rollout step(s):** 1 and 2 (see "Recommended Rollout Sequence")

### Summary

Both `EXEC_QUALITY_MODE` and `EVENT_RISK_MODE` have been promoted from
`shadow` to `live` as the bare-config defaults. Operators who do not set
the corresponding env vars now get the gates in *enforcing* mode by
default. Operators who explicitly set `…=off` continue to bypass the
gates exactly as before.

### Why

- Both plugins have run in `shadow` long enough to surface diagnostics
  trends and confirm no sharp jumps in block / error rates per the
  README canary procedure.
- The `balanced` and `conservative` strategy presets in
  `webapp/preset_catalog.py` already prescribed `live`. Promoting the
  bare default closes the gap for operators who don't pick a preset
  (CI, custom env files, fresh installs).
- Invalid env values now also fall back to the operational default
  (`live` for these two) instead of silently disabling the gate, which
  is the safer behavior for promoted plugins.

### What changed

| File | Change |
| --- | --- |
| `config.py` | `get_exec_quality_mode` and `get_event_risk_mode` defaults flipped from `"off"` to `"live"`. Both functions now document the promotion + ledger reference in their docstrings. |
| `webapp/settings.py` | Pydantic `ExecutionSettings.exec_quality_mode` and `event_risk_mode` defaults flipped from `"off"` to `"live"` so the SaaS settings flow matches. |
| `scripts/validate_plugin_modes.py` | Added a `PROMOTED_DEFAULTS` table; default + invalid-value assertions are parameterised so the validator stays green. Other modes (`EXIT_MANAGER_MODE`, `CORRELATION_GUARD_MODE`, `REGIME_V2_MODE`) still default to `"off"`. |
| `README.md` | Rollout sequence steps 1 and 2 marked ✅ done. Env-var reference annotated with `default live — promoted 2026-Q2`. |
| `scripts/promotion_ledger.jsonl` | Two new signed entries (seq 1, seq 2). Chain verified via `python scripts/promotion_ledger.py verify`. |

### Operator impact

- **Doing nothing:** new installs and any installs without
  `EXEC_QUALITY_MODE=` / `EVENT_RISK_MODE=` set in env now get
  `live` — wide-spread / bad-quote orders may be blocked, and earnings
  / macro blackouts may block or downsize buys (per `EVENT_ACTION`).
- **Pinning back to shadow:** set `EXEC_QUALITY_MODE=shadow` and/or
  `EVENT_RISK_MODE=shadow` in your env / `.env` file.
- **Disabling completely:** set `EXEC_QUALITY_MODE=off` and/or
  `EVENT_RISK_MODE=off`.
- **Strategy presets** are unchanged — `conservative` and `balanced`
  still pin both to `live`; `aggressive` still pins both to `shadow`
  by design (its blurb says "earnings and execution checks stay in
  log-only mode until you tighten them").

### Rollback

If the promoted defaults cause issues:

1. Set `EXEC_QUALITY_MODE=off` and/or `EVENT_RISK_MODE=off` in env.
2. Re-run validation: `python scripts/validate_all.py --profile local --strict --skip-backtest`.
3. Optionally append a rollback ledger entry:
   ```
   python scripts/promotion_ledger.py append --target EXEC_QUALITY_MODE=off --reason "Rollback: <reason>"
   ```
4. If the rollback should be permanent, revert this change set
   (`config.py` + `webapp/settings.py` + `scripts/validate_plugin_modes.py`).

### Verification

- `python scripts/validate_plugin_modes.py` — passes with the new defaults.
- `python scripts/promotion_ledger.py verify` — chain intact (2 entries).
- `python scripts/validate_all.py --profile local --strict --skip-backtest`
  recommended before next deploy.
