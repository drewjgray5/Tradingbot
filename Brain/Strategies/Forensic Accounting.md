---
tags: [strategy, fundamentals, risk]
---
# Forensic Accounting

Financial health checks that flag potential accounting irregularities or distress before entry.

## Checks Performed

| Metric | Env Var | Default Threshold | Flag Condition |
|--------|---------|-------------------|----------------|
| Sloan Ratio | `FORENSIC_SLOAN_MAX` | 0.10 | Accrual risk above threshold |
| Beneish M-Score | `FORENSIC_BENEISH_MAX` | -1.78 | Manipulation flag above threshold |
| Altman Z-Score | `FORENSIC_ALTMAN_MIN` | 1.80 | Distress flag below threshold |

## Filter Modes

| Mode | Behavior |
|------|----------|
| `off` | Disabled |
| `shadow` | Diagnostics only (default) |
| `soft` | Add quality reasons but do not hard block |
| `hard` | Block entries with forensic flags |

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `FORENSIC_ENABLED` | true | Enable forensic enrichment |
| `FORENSIC_FILTER_MODE` | shadow | Filter mode |
| `FORENSIC_CACHE_HOURS` | 24.0 | Cache TTL for forensic snapshots |

## Related
- [[Signal Scanner]] — forensic flags integrated into quality gates
- [[Quality Gates]] — forensic results feed into weak reasons
