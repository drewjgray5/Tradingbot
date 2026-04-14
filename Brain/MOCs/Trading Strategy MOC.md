---
tags: [moc, strategy]
---
# Trading Strategy MOC

The reasoning and logic behind how the bot identifies and acts on trading signals.

## Signal Identification
- [[Stage 2 Analysis]] — Weinstein trend stage qualification
- [[VCP Detection]] — Mark Minervini's Volume Contraction Pattern
- [[Sector Strength]] — relative strength filter vs SPY
- [[Signal Ranking]] — composite scoring and top-N selection

## Signal Quality
- [[Quality Gates]] — soft/hard filtering of weak signals
- [[Forensic Accounting]] — Sloan, Beneish, Altman checks
- [[PEAD]] — post-earnings announcement drift scoring

## Risk Management
- [[Guardrails]] — position size caps, daily trade limits, sector exposure
- [[Plugin Modes]] — execution quality, exit manager, event risk, regime v2, correlation guard
- [[Adaptive Stops]] — ATR-based trailing stop sizing

## Learning & Calibration
- [[Hypothesis Ledger]] — decision quality tracking separate from P&L
- [[Advisory Model]] — calibrated probability model for scan signals
- [[Self-Study]] — automated trade outcome analysis

## Strategy Evolution
- [[Plugin Modes]] > Recommended Rollout Sequence
- See `schwab_skill/ADVISORY_MODEL_SPEC.md` for champion/challenger workflow
- See `schwab_skill/SIGNAL_QUALITY_ROLLOUT.md` for quality gate rollout plan
