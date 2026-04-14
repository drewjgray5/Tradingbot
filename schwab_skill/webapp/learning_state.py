from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from .models import AppState
from .security import parse_json

LEARNING_STRATEGY_UPDATE_KEY = "learning_strategy_update"
LEARNING_CHALLENGER_HISTORY_KEY = "challenger_history"
LEARNING_LAST_RUN_KEY = "learning_last_run"
LEARNING_TRADE_OUTCOMES_KEY = "learning_trade_outcomes"

MAX_CHALLENGER_HISTORY = 60
MAX_TRADE_OUTCOMES = 2500


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _state_row(db: Session, user_id: str, key: str) -> AppState | None:
    return db.query(AppState).filter(AppState.user_id == user_id, AppState.key == key).first()


def load_state_json(db: Session, user_id: str, key: str, default: Any) -> Any:
    row = _state_row(db, user_id, key)
    if not row:
        return default
    parsed = parse_json(row.value_json, default)
    if isinstance(default, list):
        return parsed if isinstance(parsed, list) else default
    if isinstance(default, dict):
        return parsed if isinstance(parsed, dict) else default
    return parsed


def save_state_json(db: Session, user_id: str, key: str, payload: Any) -> None:
    blob = json.dumps(payload, default=_json_default)
    row = _state_row(db, user_id, key)
    if not row:
        db.add(AppState(user_id=user_id, key=key, value_json=blob))
    else:
        row.value_json = blob
    db.commit()


def load_strategy_update(db: Session, user_id: str) -> dict[str, Any] | None:
    data = load_state_json(db, user_id, LEARNING_STRATEGY_UPDATE_KEY, {})
    if not isinstance(data, dict):
        return None
    return data if data.get("env_overrides") else None


def save_strategy_update(db: Session, user_id: str, payload: dict[str, Any]) -> None:
    save_state_json(db, user_id, LEARNING_STRATEGY_UPDATE_KEY, payload)


def load_challenger_history(db: Session, user_id: str) -> list[dict[str, Any]]:
    history = load_state_json(db, user_id, LEARNING_CHALLENGER_HISTORY_KEY, [])
    return history if isinstance(history, list) else []


def append_challenger_result(db: Session, user_id: str, comparison: dict[str, Any]) -> list[dict[str, Any]]:
    history = load_challenger_history(db, user_id)
    history.append(comparison)
    if len(history) > MAX_CHALLENGER_HISTORY:
        history = history[-MAX_CHALLENGER_HISTORY:]
    save_state_json(db, user_id, LEARNING_CHALLENGER_HISTORY_KEY, history)
    return history


def load_trade_outcomes(db: Session, user_id: str) -> list[dict[str, Any]]:
    rows = load_state_json(db, user_id, LEARNING_TRADE_OUTCOMES_KEY, [])
    return rows if isinstance(rows, list) else []


def upsert_trade_outcome(db: Session, user_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = load_trade_outcomes(db, user_id)
    order_id = str(payload.get("order_id") or "").strip()
    updated = False
    if order_id:
        for row in rows:
            if str(row.get("order_id") or "").strip() == order_id:
                row.update(payload)
                updated = True
                break
    if not updated:
        rows.append(payload)
    if len(rows) > MAX_TRADE_OUTCOMES:
        rows = rows[-MAX_TRADE_OUTCOMES:]
    save_state_json(db, user_id, LEARNING_TRADE_OUTCOMES_KEY, rows)
    return rows


def save_learning_last_run(
    db: Session,
    user_id: str,
    *,
    component: str,
    status: str,
    message: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    payload = load_state_json(db, user_id, LEARNING_LAST_RUN_KEY, {})
    if not isinstance(payload, dict):
        payload = {}
    payload[component] = {
        "status": status,
        "message": message,
        "data": data or {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state_json(db, user_id, LEARNING_LAST_RUN_KEY, payload)
