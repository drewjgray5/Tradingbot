from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from celery import Celery

from execution import place_order
from signal_scanner import scan_for_signals_detailed

from .db import SessionLocal
from .models import Order, ScanResult

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "tradingbot_webapp",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
)

SKILL_DIR = Path(__file__).resolve().parent.parent


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@celery_app.task(name="webapp.scan_for_user")
def scan_for_user(user_id: str) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    db = SessionLocal()
    try:
        signals, diagnostics = scan_for_signals_detailed(skill_dir=SKILL_DIR)
        inserted = 0
        for sig in signals:
            row = ScanResult(
                user_id=user_id,
                job_id=job_id,
                ticker=str(sig.get("ticker") or sig.get("symbol") or "").upper(),
                signal_score=(float(sig.get("signal_score")) if sig.get("signal_score") is not None else None),
                payload_json=json.dumps(sig, default=_json_default),
            )
            db.add(row)
            inserted += 1
        db.commit()
        return {
            "ok": True,
            "job_id": job_id,
            "signals_found": inserted,
            "diagnostics": diagnostics,
        }
    except Exception as exc:
        db.rollback()
        return {"ok": False, "job_id": job_id, "error": str(exc)}
    finally:
        db.close()


@celery_app.task(name="webapp.execute_order_for_user")
def execute_order_for_user(
    user_id: str,
    ticker: str,
    qty: int,
    side: str = "BUY",
    order_type: str = "MARKET",
    price: float | None = None,
) -> dict[str, Any]:
    db = SessionLocal()
    order_id = uuid.uuid4().hex[:12]
    row = Order(
        id=order_id,
        user_id=user_id,
        ticker=ticker.upper().strip(),
        qty=qty,
        side=side.upper().strip(),
        order_type=order_type.upper().strip(),
        price=price,
        status="queued",
        result_json="{}",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        result = place_order(
            ticker=row.ticker,
            qty=row.qty,
            side=row.side,
            order_type=row.order_type,
            price_hint=row.price,
            skill_dir=SKILL_DIR,
        )
        if isinstance(result, str):
            row.status = "failed"
            row.error_message = result
            row.result_json = json.dumps({"ok": False, "error": result})
            db.commit()
            return {"ok": False, "order_id": row.id, "error": result}

        row.status = "executed"
        row.result_json = json.dumps(result, default=_json_default)
        db.commit()
        return {"ok": True, "order_id": row.id, "result": result}
    except Exception as exc:
        db.rollback()
        row = db.query(Order).filter(Order.id == order_id).first()
        if row:
            row.status = "failed"
            row.error_message = str(exc)
            row.result_json = json.dumps({"ok": False, "error": str(exc)})
            db.commit()
        return {"ok": False, "order_id": order_id, "error": str(exc)}
    finally:
        db.close()
