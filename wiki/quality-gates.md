---
source: Brain/Strategies/Quality Gates.md
created: 2026-04-13
updated: 2026-04-13
tags: [strategy, quality]
---

# Quality Gates

> Signal quality filtering that removes weak setups before reaching the final signal list.

## Modes

| Mode | Behavior |
|------|----------|
| `off` | Disabled |
| `shadow` | Track would-filter counts only (default) |
| `soft` | Filter when 2+ weak reasons exist |
| `hard` | Filter on any single weak reason |

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `QUALITY_GATES_MODE` | shadow | Gate mode |
| `QUALITY_SOFT_MIN_REASONS` | 2 | Min weak reasons for soft mode |
| `QUALITY_MIN_SIGNAL_SCORE` | 50 | Minimum score threshold |
| `QUALITY_MIN_CONTINUATION_PROB` | 0.55 | Min continuation probability |
| `QUALITY_MAX_BULL_TRAP_PROB` | 0.45 | Max bull-trap probability |

Note: `weak_breakout_volume` is always a hard gate regardless of mode.

## Related Pages

- [[signal-scanner]] — quality gates applied after Stage B
- [[signal-quality-rollout]] — rollout plan
- [[scanner-tunables]] — env var reference

---

*Last compiled: 2026-04-13*
