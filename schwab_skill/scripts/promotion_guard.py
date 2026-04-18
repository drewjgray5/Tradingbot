"""Block unattended ``--apply`` flows on promotion-touching scripts.

Two layers, in order of preference:

1. **Signed ledger approval** (preferred) — operator runs
   ``promotion_ledger.py append --target <X> --reason <why>`` to drop a
   chained, SHA-256-signed entry into ``scripts/promotion_ledger.jsonl``,
   then runs the apply script with no special env vars. The guard
   verifies chain integrity AND that a recent matching entry exists.

2. **Legacy env knob** (deprecated) — ``MANUAL_PROMOTION_APPROVED=1``
   still works during the transition so existing runbooks don't break,
   but emits a deprecation warning. Will be removed once every caller
   has migrated to ledger approvals.

Either layer is sufficient on its own; both being present is fine and
just logs the ledger path.

Why a ledger?
-------------
The plain env knob has no audit trail and any process can set it. The
ledger is append-only, signed, and every apply leaves a row tying the
change to an approver, a timestamp, and the previous chain head — so
post-incident review can prove what was approved, by whom, and in what
order. See [[promotion-playbook]] in the wiki.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MANUAL_PROMOTION_ENV = "MANUAL_PROMOTION_APPROVED"
MANUAL_PROMOTION_VALUE = "1"

# Operators occasionally need to bypass the ledger entirely (e.g. local
# dev where they want to test the apply path without seeding a real
# approval). Setting this env var to "1" downgrades a missing/invalid
# ledger approval from a hard block to a warning. Production CI never
# sets it.
SKIP_LEDGER_ENV = "PROMOTION_LEDGER_SKIP"


def _load_ledger_helpers():
    """Lazy-import ``promotion_ledger`` from the same scripts/ dir.

    The two scripts are siblings and not part of the package import path,
    so we add this dir to ``sys.path`` on first use. Lazy because not
    every caller of ``promotion_guard`` needs the ledger machinery (the
    legacy ``ensure_manual_promotion_approval`` path stays import-free).
    """
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import promotion_ledger  # noqa: WPS433  (intentional lazy import)

    return promotion_ledger


def ensure_manual_promotion_approval(apply_requested: bool) -> bool:
    """Legacy entry point. Kept for back-compat — new callers should use
    :func:`ensure_signed_approval` so promotions land in the audit
    ledger instead of relying on an env var that any process can flip.

    Behaviour unchanged from the original implementation: returns
    ``True`` when ``apply_requested`` is False or the env knob is set;
    otherwise prints a refusal message and returns False.
    """
    if not apply_requested:
        return True
    approved = str(os.environ.get(MANUAL_PROMOTION_ENV, "")).strip()
    if approved == MANUAL_PROMOTION_VALUE:
        return True
    print(
        "Refusing --apply without explicit manual approval. "
        f"Set {MANUAL_PROMOTION_ENV}={MANUAL_PROMOTION_VALUE} for this command."
    )
    return False


def ensure_signed_approval(
    target: str,
    *,
    apply_requested: bool,
    max_age_hours: float = 24.0,
    allow_legacy_env: bool = True,
) -> bool:
    """Block ``--apply`` unless a recent signed ledger entry approves
    ``target``.

    Checks, in order:

    1. ``apply_requested`` False → no-op, return True.
    2. ``PROMOTION_LEDGER_SKIP=1`` set → emit warning, return True (escape
       hatch for local dev).
    3. Verify ledger chain integrity. A tampered chain blocks the apply
       even if a matching row exists.
    4. Look for a row whose ``target`` matches and whose ``ts`` is within
       ``max_age_hours``. Found → return True.
    5. Fall back to ``MANUAL_PROMOTION_APPROVED=1`` (deprecated). When
       set AND ``allow_legacy_env`` is True → emit deprecation warning,
       return True.
    6. Otherwise → print operator guidance pointing at
       ``promotion_ledger.py append`` and return False.

    Caller should propagate False as a non-zero exit code from the
    apply script. The ``target`` string is the contract between the
    apply script and the ledger entry — keep it short, stable, and
    grep-friendly (e.g. ``"strategy_champion_params"``,
    ``"advisory_model"``).
    """
    if not apply_requested:
        return True

    if str(os.environ.get(SKIP_LEDGER_ENV, "")).strip() == "1":
        print(
            f"WARNING: {SKIP_LEDGER_ENV}=1 — bypassing ledger check for "
            f"target={target!r}. Do not use in CI / production."
        )
        return True

    try:
        ledger = _load_ledger_helpers()
    except ImportError as exc:
        # Defensive: if the ledger module is missing for any reason we
        # must not silently allow the apply. Surface the failure and
        # let the operator decide whether the env-knob fallback is OK.
        print(
            f"Refusing --apply: failed to load promotion_ledger module ({exc}). "
            "Verify scripts/promotion_ledger.py is present."
        )
        return False

    rows = ledger.read_entries()
    chain_ok, chain_msg = ledger.verify_chain(rows)
    if not chain_ok:
        print(
            f"Refusing --apply: promotion_ledger chain is broken ({chain_msg}). "
            "Repair before retrying — DO NOT bypass without forensic review."
        )
        return False

    found = ledger.find_recent_approval(
        target, max_age_hours=max_age_hours, entries=rows
    )
    if found:
        print(
            f"Signed approval accepted for target={target!r} "
            f"(seq={found.get('seq')} approver={found.get('approver')!r} "
            f"ts={found.get('ts')})."
        )
        return True

    legacy_env_set = (
        str(os.environ.get(MANUAL_PROMOTION_ENV, "")).strip()
        == MANUAL_PROMOTION_VALUE
    )
    if legacy_env_set and allow_legacy_env:
        print(
            f"WARNING: accepting deprecated {MANUAL_PROMOTION_ENV}=1 for "
            f"target={target!r}. Migrate to a ledger entry:\n"
            f"  python scripts/promotion_ledger.py append "
            f"--target {target} --reason '<why>'"
        )
        return True

    print(
        f"Refusing --apply: no signed approval for target={target!r} "
        f"within {max_age_hours}h. Append one with:\n"
        f"  python scripts/promotion_ledger.py append "
        f"--target {target} --reason '<why>'\n"
        f"Then re-run the apply command. (See [[promotion-playbook]].)"
    )
    return False
