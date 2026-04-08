# Controlled Live Canary Runbook

This runbook validates live trading with strict blast-radius limits.

## Preconditions

- `python scripts/validate_all.py --profile local --strict` passes.
- `python scripts/validate_all.py --profile server --strict` passes in target runtime.
- If advisory auto-promotion is enabled, latest decision artifact shows expected status:
  - `python scripts/report_advisory_status.py`
- Schwab auth sessions are valid (`tokens_market.enc`, `tokens_account.enc` exist).
- `MAX_POSITION_PER_TICKER`, `MAX_TRADES_PER_DAY`, and `POSITION_SIZE_USD` are set conservatively.
- `WEB_API_KEY` set if using web approvals.

## Canary Scope

- One liquid symbol only (example: `AAPL`).
- Single trade attempt with minimum allowed size.
- Normal market hours only.
- Human-in-the-loop approval required.

## Step-by-Step Procedure

1. **Confirm readiness**
   - Run `python healthcheck.py`.
   - If using web dashboard, verify `GET /api/health/deep` is green.

2. **Dry-run guard**
   - Set `EXECUTION_SHADOW_MODE=true`.
   - Trigger one mock approval path and confirm no live order submission.
   - Revert to `EXECUTION_SHADOW_MODE=false` only after successful shadow check.

3. **Live canary trade**
   - Queue one candidate.
   - Approve one buy with smallest canary size.
   - Validate returned result includes order id / accepted response.

4. **Post-order checks**
   - Confirm trailing stop protection status (`attached` expected).
   - Confirm order monitor transitions are emitted (submitted -> filled/rejected).
   - Confirm audit and safety metrics updated.

5. **Close canary window**
   - No additional live orders until canary review is complete.
   - Record outcomes in `validation_artifacts/` notes or team log.

## Rollback / Abort Criteria

Abort immediately if any occur:
- Stop protection fails to attach.
- Unexpected circuit breaker instability.
- API error burst or repeated transport errors.
- Unexpected guardrail bypass or inconsistent account/position state.

Immediate actions:
- Set `EXECUTION_SHADOW_MODE=true`.
- Halt scheduler/process.
- Investigate logs and execution safety metrics before any new live action.

## Canary Success Criteria

- One live canary completes without safety violations.
- No unresolved critical alerts for 24h after canary.
- Observability gates remain within threshold.
- Operator sign-off recorded.

