---
tags: [config, features]
---
# Feature Flags

Boolean toggles and mode switches that enable/disable system capabilities.

## Execution Control

| Env Var | Default | Description |
|---------|---------|-------------|
| `EXECUTION_SHADOW_MODE` | false | Shadow mode: compute but don't submit orders |
| `PAPER_TRADING_ENABLED` | false | Alias for shadow mode |
| `LIVE_TRADING_KILL_SWITCH` | false | Platform-wide trading halt |
| `USER_TRADING_HALTED` | false | Per-user trading pause (SaaS) |
| `LIVE_TRADING_KILL_SWITCH_BLOCKS_EXITS` | false | Block SELL orders too when halted |

## Data Quality

| Env Var | Default | Description |
|---------|---------|-------------|
| `DATA_QUALITY_EXEC_POLICY` | off | `off`, `warn`, or `block_risk_increasing` |
| `DATA_QUOTE_MAX_AGE_SEC` | 600 | Mark quote stale after N seconds |
| `DATA_BAR_MAX_STALENESS_DAYS` | 7 | Mark daily bars stale after N days |
| `DATA_EDGAR_MAX_AGE_HOURS` | 72 | Flag old SEC cache entries |
| `DATA_CROSSCHECK_ENABLED` | false | Compare Schwab quote vs yfinance |
| `DATA_CROSSCHECK_MAX_REL_DIFF` | 0.012 | Relative diff triggering conflict |

## SEC Enrichment

| Env Var | Default | Description |
|---------|---------|-------------|
| `SEC_ENRICHMENT_ENABLED` | true | Enable SEC enrichment |
| `SEC_TAGGING_ENABLED` | true | Attach SEC tags to signal payloads |
| `SEC_SHADOW_MODE` | true | SEC score hints diagnostics-only |
| `SEC_SCORE_HINT_ENABLED` | false | Apply SEC hints to ranking |
| `SEC_CACHE_HOURS` | 12 | SEC cache TTL |
| `EDGAR_USER_AGENT` | default | SEC requests user-agent (must include email) |

## SEC Filing Analysis

| Env Var | Default | Description |
|---------|---------|-------------|
| `SEC_FILING_ANALYSIS_ENABLED` | true | Enable filing analysis endpoints |
| `SEC_FILING_COMPARE_ENABLED` | true | Enable compare endpoints |
| `SEC_FILING_CACHE_HOURS` | 24 | Filing text cache TTL |
| `SEC_FILING_MAX_CHARS` | 120000 | Max chars per filing |
| `SEC_FILING_MAX_COMPARE_ITEMS` | 2 | UI/API compare item limit |
| `SEC_FILING_LLM_SUMMARY_ENABLED` | true | Optional LLM summaries |

## Advisory Model

| Env Var | Default | Description |
|---------|---------|-------------|
| `ADVISORY_MODEL_ENABLED` | true | Enable advisory scoring |
| `ADVISORY_MODEL_PATH` | `artifacts/advisory_model_v1.json` | Model artifact path |
| `ADVISORY_CONFIDENCE_HIGH` | 0.62 | High confidence threshold |
| `ADVISORY_CONFIDENCE_LOW` | 0.52 | Medium confidence threshold |
| `ADVISORY_REQUIRE_MODEL` | false | Fail validation if missing |

## Hypothesis Ledger

| Env Var | Default | Description |
|---------|---------|-------------|
| `HYPOTHESIS_LEDGER_ENABLED` | false | Enable hypothesis recording |
| `HYPOTHESIS_SCORE_HORIZONS` | 1,5,20 | Scoring horizons (trading days) |
| `HYPOTHESIS_SELF_STUDY_MERGE` | false | Include in self-study output |
| `HYPOTHESIS_PROMOTION_GUARD_ENABLED` | false | Gate promotions on hit rate |
| `HYPOTHESIS_PROMOTION_MIN_N` | 30 | Minimum scored outcomes |
| `HYPOTHESIS_PROMOTION_MIN_HIT_RATE` | 0.45 | Minimum combined hit rate |

## Forensic & PEAD

| Env Var | Default | Description |
|---------|---------|-------------|
| `FORENSIC_ENABLED` | true | Enable forensic accounting |
| `FORENSIC_FILTER_MODE` | shadow | off/shadow/soft/hard |
| `PEAD_ENABLED` | true | Enable PEAD scoring |
| `GUIDANCE_SCORE_ENABLED` | true | Enable guidance tone scoring |

## Discord Alerts

| Env Var | Default | Description |
|---------|---------|-------------|
| `DISCORD_WEBHOOK_URL` | none | Discord webhook URL |
| `DISCORD_USER_ID` | none | User ID for @mention pings |
| `ALERT_MIN_CONVICTION` | 20 | Min conviction for any alert |
| `ALERT_PING_CONVICTION` | 50 | Conviction threshold for @ping |
| `ALERT_PING_SCORE` | 60 | Score threshold for @ping |

## Related
- [[Config Reference MOC]] — all config domains
- [[Plugin Modes Config]] — plugin-specific tunables
- [[Scanner Tunables]] — scanner-specific tunables
