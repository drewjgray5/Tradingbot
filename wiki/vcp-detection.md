---
source: Brain/Strategies/VCP Detection.md
created: 2026-04-13
updated: 2026-04-13
tags: [strategy, technical]
---

# VCP Detection

> Volume Contraction Pattern (Mark Minervini) — identifies stocks where selling pressure is exhausted before breakout.

## The Pattern

1. Stock in uptrend ([[stage-2-analysis]])
2. Price consolidates in tightening ranges
3. Volume contracts progressively
4. Breakout on expanding volume signals institutional accumulation

## Configuration

| Env Var | Default | Logic |
|---------|---------|-------|
| `VCP_DAYS` | 4 | Consecutive days with volume below 50-day average |

## Breakout Confirmation

- `BREAKOUT_CONFIRM_ENABLED` (default true) — require intraday price above prior high
- `QUALITY_REQUIRE_BREAKOUT_VOLUME` (default false) — require volume above 50-day avg (always hard gate)

## Tuning

- Increase `VCP_DAYS` to 6-8 for stricter (fewer but higher-quality signals)
- Decrease to 2-3 for looser (more signals, more noise)
- Diagnostics show `vcp_fail` count

## Related Pages

- [[stage-2-analysis]] — prerequisite for VCP
- [[signal-scanner]] — VCP is second filter in Stage A
- [[scanner-tunables]] — env var reference

---

*Last compiled: 2026-04-13*
