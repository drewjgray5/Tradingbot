---
tags: [strategy, technical]
---
# VCP Detection

Volume Contraction Pattern, based on Mark Minervini's methodology. Identifies stocks where volume is drying up before a potential breakout.

## The Pattern
A VCP occurs when:
1. A stock is in an uptrend (Stage 2)
2. Price consolidates in tightening ranges
3. Volume contracts progressively -- selling pressure is exhausted
4. A breakout on expanding volume signals institutional accumulation

## Qualification Criteria

| Check | Env Var | Default | Logic |
|-------|---------|---------|-------|
| Volume contraction days | `VCP_DAYS` | 4 | N consecutive days with volume below 50-day average |

## Implementation
- `check_vcp_volume(df)` in `stage_analysis.py`
- Compares daily volume to 50-day volume moving average
- Requires `VCP_DAYS` consecutive days below average

## Why Volume Contraction Matters
- Declining volume during consolidation means sellers are running out
- When buyers return on increased volume, the breakout is more likely to sustain
- Combined with [[Stage 2 Analysis]], this filters for high-probability setups

## Breakout Confirmation
Optional additional check for intraday confirmation:
- `BREAKOUT_CONFIRM_ENABLED` (default true) — require intraday price above prior high
- `BREAKOUT_CONFIRM_MIN_TIME` (default 570 = 9:30 AM) — minutes from midnight

## Quality Gate: Breakout Volume
- `QUALITY_REQUIRE_BREAKOUT_VOLUME` (default false) — require latest volume above 50-day average
- `QUALITY_BREAKOUT_VOLUME_MIN_RATIO` (default 0.90) — minimum volume ratio threshold
- This is always a **hard gate** regardless of `QUALITY_GATES_MODE`

## Tuning
- Increase `VCP_DAYS` to 6-8 for stricter contraction (fewer but higher-quality signals)
- Decrease to 2-3 for looser detection (more signals, more noise)
- Scanner diagnostics show `vcp_fail` count

## Related
- [[Stage 2 Analysis]] — prerequisite for VCP detection
- [[Signal Scanner]] — VCP is the second filter in Stage A
- [[Scanner Tunables]] — env var reference
