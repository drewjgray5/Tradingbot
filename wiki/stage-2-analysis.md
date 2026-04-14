---
source: Brain/Strategies/Stage 2 Analysis.md
created: 2026-04-13
updated: 2026-04-13
tags: [strategy, technical]
---

# Stage 2 Analysis

> Weinstein Stage 2 trend qualification — the optimal phase for momentum entries.

## Weinstein's Four Stages

1. **Stage 1** — Basing (accumulation)
2. **Stage 2** — Advancing (markup) — **buy here**
3. **Stage 3** — Topping (distribution)
4. **Stage 4** — Declining (markdown)

## Qualification Criteria

| Check | Env Var | Default | Logic |
|-------|---------|---------|-------|
| 52W high proximity | `STAGE2_52W_PCT` | 0.85 | Price within 15% of 52W high |
| 200 SMA trend | `STAGE2_SMA_UPWARD_DAYS` | 20 | 200-day SMA rising for 20+ days |

## Implementation

`is_stage_2(df)` in `stage_analysis.py`. Uses TA-Lib for SMA when available, falls back to pandas.

## Tuning

- Raise `STAGE2_52W_PCT` to 0.90+ for breakout-oriented (fewer signals)
- Lower `STAGE2_SMA_UPWARD_DAYS` to 10 for faster detection (more noise)
- Scanner diagnostics show `stage2_fail` count

## Related Pages

- [[vcp-detection]] — next filter after Stage 2
- [[signal-scanner]] — Stage A first-pass filter
- [[scanner-tunables]] — env var reference

---

*Last compiled: 2026-04-13*
