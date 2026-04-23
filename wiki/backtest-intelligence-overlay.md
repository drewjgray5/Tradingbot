---
source: schwab_skill/backtest_intelligence.py
created: 2026-04-17
updated: 2026-04-18
tags: [backtest, plugins, promotion, intelligence, validation]
---

# Backtest Intelligence Overlay

> Historical PnL attribution layer that lets the [[promotion-playbook]] gate
> any shadow plugin (meta-policy, uncertainty, mirofish weighting,
> event-risk, exit-manager, exec-quality) on years of data instead of weeks
> of forward observation.

## Why this exists

Several intelligence plugins live in `shadow` mode and compute "what we would
have done" diagnostics, but the live runtime can't tell us *what would have
happened to PnL*. Without that signal, the [[promotion-playbook]]'s
"effect direction" and "no regression" gates can't be evaluated. This overlay
fills the gap by re-running the [[backtest]] with each plugin toggled on or
off and reporting a delta.

## Module layout

`schwab_skill/backtest_intelligence.py` exposes four pure overlay functions
plus a `BacktestIntelligenceConfig` dataclass:

| Overlay                                 | Join point in `run_backtest`                       | Effect when `live` |
|-----------------------------------------|----------------------------------------------------|--------------------|
| `apply_meta_policy_overlay`             | After quality gates, before sizing                 | May suppress entry or downsize via `meta_policy_size_multiplier` |
| `evaluate_event_risk_for_backtest` + `apply_event_risk_overlay` | After meta-policy | Block or downsize when `pead_info` shows earnings within `EVENT_BLOCK_EARNINGS_DAYS` |
| `simulate_exit_with_manager`            | Replaces `_simulate_exit`                         | Adds partial-TP at `EXIT_PARTIAL_TP_R_MULT * R`, optional breakeven move, time-stop at `EXIT_MAX_HOLD_DAYS` |
| `apply_exec_quality_overlay`            | At cost calculation                                | Halves slippage for liquid (≤0.2% participation) names, inflates 1.5× for illiquid (≥1%) |

Every overlay honours three modes:

* `off` — byte-identical to the legacy backtest.
* `shadow` — never alters trade decisions; emits `*_shadow_*` diagnostics so
  the comparison report shows what *would* have happened.
* `live` — actually applies the action; bumps `*_live_*` diagnostics.

## Point-in-time safety

The live `signal_scanner.evaluate_event_risk_policy` queries the *current*
earnings calendar and macro-blackout file, both of which are forward-looking
and would introduce look-ahead bias in a backtest. The
`evaluate_event_risk_for_backtest` variant uses **only** the `pead_info`
payload that the backtest already fetched per-candidate, and skips macro
blackouts entirely. This keeps the comparison strictly point-in-time.

## Calling from a backtest

```python
from backtest import run_backtest
from backtest_intelligence import BacktestIntelligenceConfig

result = run_backtest(
    start_date="2018-01-01",
    end_date="2024-12-31",
    intelligence_overlay=BacktestIntelligenceConfig.all_live(),
)
```

The `intelligence_overlay` parameter accepts either a `BacktestIntelligenceConfig`,
a plain `dict[str, str]`, or `None`.

**Default semantics (post 2026-04-18 core fix):** `intelligence_overlay=None`
resolves at runtime to `BacktestIntelligenceConfig.from_env(skill_dir)`, so
the backtest matches the **live code path** (which means `event_risk=live`
and `exec_quality=live` by default per `config.py`). Callers that need a
strict overlays-off baseline must pass `BacktestIntelligenceConfig.all_off()`
explicitly. The byte-identical guarantee is preserved by
`test_exit_manager_off_matches_legacy_simulate_exit` when overlays are
explicitly off.

> **Historical note — 2026-04-18 wiring bug:** prior to the core fix,
> `intelligence_overlay=None` resolved to `BacktestIntelligenceConfig.all_off()`,
> meaning every caller that didn't forward an explicit overlay silently
> disabled all four plugins regardless of `*_MODE` env vars. Affected
> scripts: `scripts/run_multi_era_backtest_schwab_only.py`,
> `scripts/validate_pf_robustness.py`, `scripts/validate_backtest.py`,
> `scripts/optimize_strategy_loop.py`, `webapp/backtest_spec.py`,
> `prediction_market_experiment.py`. Every multi-era artifact written before
> 2026-04-18 — including the `PF≈1.04` cross-era champion baseline at
> `multi_era_backtest_schwab_only_20260417T174813Z.json` — measured the
> strategy with all four overlays forced off, even though `event_risk` and
> `exec_quality` default to `live` in production. Those numbers remain
> valid as a "legacy / overlays-off" baseline but **must not be compared
> directly to overlay-on results**. See
> `validation_artifacts/phase1_prologue_overlay_wiring_bug.md` for the smoke
> evidence and `validation_artifacts/phase1_callsite_audit.md` for the full
> callsite audit.

### Audit checklist for any new caller of `run_backtest()`

If you are adding a script that calls `run_backtest()`:

1. **Production parity (default):** pass nothing — the new default
   `from_env(skill_dir)` matches live. Or pass it explicitly for clarity.
2. **Controlled experiments:** pass an explicit `BacktestIntelligenceConfig`
   with the modes you want (e.g. `BacktestIntelligenceConfig(exit_manager="live")`).
3. **Strict overlays-off baseline:** pass `BacktestIntelligenceConfig.all_off()`
   explicitly. Do not rely on `None`.
4. Cite the `intelligence_overlay` block emitted into the JSON result so
   readers can verify which overlays were active.

## Comparison script

`schwab_skill/scripts/backtest_intelligence_compare.py` runs the same window
twice — once with overlays off, once with the requested treatment — and
emits a JSON delta report plus a verdict per metric.

```bash
python schwab_skill/scripts/backtest_intelligence_compare.py \
    --start-date 2018-01-01 --end-date 2024-12-31 \
    --treatment exit_manager \
    --output validation_artifacts/exit_manager_compare.json
```

Available presets: `all_live`, `all_shadow`, `meta_policy`, `event_risk`,
`exit_manager`, `exec_quality`. Use the single-overlay presets when generating
gate evidence for a single feature (the [[promotion-playbook]] requires
isolated effect direction).

## Promotion gate evidence

The output JSON includes a `verdict` block:

```json
{
  "verdict": {
    "win_rate_net": "improved",
    "total_return_net_pct": "improved",
    "cagr_net_pct": "improved",
    "max_drawdown_net_pct": "regressed",
    "profit_factor_net": "improved"
  }
}
```

A feature is gate-eligible for `shadow → live` promotion when:

1. At least three of the five core metrics are `improved` or `neutral`.
2. `max_drawdown_net_pct` is not regressed by more than 2 percentage points.
3. The treatment arm has at least 100 trades (statistical relevance).

These rules are enforced narratively today; codifying them into
`scripts/decide_pm_promotion.py` is on the deferred list.

## Related Pages

- [[backtest]] — The pipeline this overlay augments.
- [[promotion-playbook]] — Why the comparison exists.
- [[agent-intelligence]] — Source of meta-policy + uncertainty.
- [[meta-policy]] — Decision combiner whose live behaviour the overlay simulates.
- [[plugin-modes]] — Live counterparts of every overlay.
- [[validation]] — Where the comparison report lives in the artefact tree.

---

*Last compiled: 2026-04-18*
