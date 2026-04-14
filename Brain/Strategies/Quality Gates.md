---
tags: [strategy, quality]
---
# Quality Gates

Signal quality filtering that removes weak setups before they reach the final signal list.

## Modes

| Mode | Behavior |
|------|----------|
| `off` | Disabled, diagnostics only |
| `shadow` | Disabled but tracks would-filter counts (default if `QUALITY_GATES_ENABLED` not set) |
| `soft` | Filter when multiple weak reasons exist (default when enabled) |
| `hard` | Filter on any single weak reason |

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `QUALITY_GATES_MODE` | `shadow` | Gate mode (off/shadow/soft/hard) |
| `QUALITY_SOFT_MIN_REASONS` | 2 | Min weak reasons before filtering in soft mode |
| `QUALITY_MIN_SIGNAL_SCORE` | 50 | Minimum score threshold |
| `QUALITY_MIN_CONTINUATION_PROB` | 0.55 | Min continuation probability |
| `QUALITY_MAX_BULL_TRAP_PROB` | 0.45 | Max acceptable bull-trap probability |
| `QUALITY_REQUIRE_BREAKOUT_VOLUME` | false | Require volume above 50-day avg |
| `QUALITY_BREAKOUT_VOLUME_MIN_RATIO` | 0.90 | Min volume ratio for breakout |
| `QUALITY_WATCHLIST_PREFILTER_ENABLED` | false | Pre-scan universe filtering |
| `QUALITY_WATCHLIST_PREFILTER_MAX` | 800 | Max symbols after prefiltering |

Note: `weak_breakout_volume` is always a hard gate regardless of mode.

## Related
- [[Signal Scanner]] — quality gates applied after Stage B enrichment
- [[Signal Quality Rollout]] — rollout plan for quality gates
- [[Scanner Tunables]] — env var details
