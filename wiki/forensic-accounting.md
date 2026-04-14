---
source: Brain/Strategies/Forensic Accounting.md
created: 2026-04-13
updated: 2026-04-13
tags: [strategy, fundamentals, risk]
---

# Forensic Accounting

> Financial health checks flagging accounting irregularities or distress before entry.

## Checks

| Metric | Env Var | Default | Flag |
|--------|---------|---------|------|
| Sloan Ratio | `FORENSIC_SLOAN_MAX` | 0.10 | Accrual risk above threshold |
| Beneish M-Score | `FORENSIC_BENEISH_MAX` | -1.78 | Manipulation above threshold |
| Altman Z-Score | `FORENSIC_ALTMAN_MIN` | 1.80 | Distress below threshold |

## Filter Modes

off / shadow (default) / soft / hard

## Related Pages

- [[signal-scanner]] — forensic flags in quality gates
- [[quality-gates]] — forensic results feed weak reasons
- [[feature-flags]] — `FORENSIC_ENABLED`, `FORENSIC_FILTER_MODE`

---

*Last compiled: 2026-04-13*
