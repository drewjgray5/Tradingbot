from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog
from .redaction import redact_mapping

LOG = logging.getLogger("webapp.audit")


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def log_audit(
    db: Session,
    *,
    action: str,
    user_id: str | None = None,
    detail: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> None:
    safe_detail = redact_mapping(detail)
    row = AuditLog(
        user_id=user_id,
        action=action[:64],
        detail_json=json.dumps(safe_detail, default=_json_default),
        request_id=request_id[:64] if request_id else None,
    )
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        LOG.warning("audit log commit failed for action=%s", action, exc_info=True)
