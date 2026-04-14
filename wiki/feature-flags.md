---
source: Brain/Config/Feature Flags.md
created: 2026-04-13
updated: 2026-04-13
tags: [config, features, flags]
---

# Feature Flags

> Boolean toggles and mode switches that enable/disable system capabilities.

## Execution Control

| Env Var | Default | Description |
|---------|---------|-------------|
| `EXECUTION_SHADOW_MODE` | false | Compute but don't submit orders |
| `PAPER_TRADING_ENABLED` | false | Alias for shadow mode |
| `LIVE_TRADING_KILL_SWITCH` | false | Platform-wide trading halt |
| `USER_TRADING_HALTED` | false | Per-user trading pause (SaaS) |

## Data Quality

| Env Var | Default | Description |
|---------|---------|-------------|
| `DATA_QUALITY_EXEC_POLICY` | off | `off`, `warn`, or `block_risk_increasing` |
| `DATA_CROSSCHECK_ENABLED` | false | Compare Schwab vs yfinance quotes |

## SEC Enrichment

| Env Var | Default | Description |
|---------|---------|-------------|
| `SEC_ENRICHMENT_ENABLED` | true | Enable SEC enrichment |
| `SEC_SHADOW_MODE` | true | SEC score hints diagnostics-only |
| `SEC_FILING_ANALYSIS_ENABLED` | true | Enable filing analysis endpoints |

## Advisory Model

| Env Var | Default | Description |
|---------|---------|-------------|
| `ADVISORY_MODEL_ENABLED` | true | Enable advisory scoring |
| `ADVISORY_REQUIRE_MODEL` | false | Fail validation if model missing |

## Hypothesis Ledger

| Env Var | Default | Description |
|---------|---------|-------------|
| `HYPOTHESIS_LEDGER_ENABLED` | false | Enable hypothesis recording |
| `HYPOTHESIS_SELF_STUDY_MERGE` | false | Include in self-study output |
| `HYPOTHESIS_PROMOTION_GUARD_ENABLED` | false | Gate promotions on hit rate |

## Forensic & PEAD

| Env Var | Default | Description |
|---------|---------|-------------|
| `FORENSIC_ENABLED` | true | Enable forensic accounting |
| `FORENSIC_FILTER_MODE` | shadow | off/shadow/soft/hard |
| `PEAD_ENABLED` | true | Enable PEAD scoring |

## Related Pages

- [[scanner-tunables]] — scanner-specific tunables
- [[plugin-modes-config]] — plugin env vars
- [[guardrails]] — kill switches
- [[signal-scanner]] — where flags take effect

---

*Last compiled: 2026-04-13*
