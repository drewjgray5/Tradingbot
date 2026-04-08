from __future__ import annotations

import os

MANUAL_PROMOTION_ENV = "MANUAL_PROMOTION_APPROVED"
MANUAL_PROMOTION_VALUE = "1"


def ensure_manual_promotion_approval(apply_requested: bool) -> bool:
    """
    Block unattended apply flows unless explicit operator approval env is present.

    Usage:
      MANUAL_PROMOTION_APPROVED=1 python scripts/... --apply
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
