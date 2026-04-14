from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from webapp.db import Base
from webapp.learning_state import (
    LEARNING_LAST_RUN_KEY,
    append_challenger_result,
    load_challenger_history,
    load_state_json,
    save_learning_last_run,
    save_strategy_update,
    upsert_trade_outcome,
)
from webapp.models import User


def _session():
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng)
    db = Session()
    db.add(User(id="u1", email="u1@example.com"))
    db.commit()
    return db


def test_trade_outcome_upsert_updates_existing_order() -> None:
    db = _session()
    try:
        upsert_trade_outcome(
            db,
            "u1",
            {
                "order_id": "ord-1",
                "ticker": "AAPL",
                "side": "BUY",
                "qty": 1,
                "fill_price": 100.0,
            },
        )
        rows = upsert_trade_outcome(
            db,
            "u1",
            {
                "order_id": "ord-1",
                "ticker": "AAPL",
                "side": "BUY",
                "qty": 1,
                "fill_price": 102.5,
            },
        )
        assert len(rows) == 1
        assert rows[0]["fill_price"] == 102.5
    finally:
        db.close()


def test_challenger_history_is_bounded() -> None:
    db = _session()
    try:
        for idx in range(75):
            append_challenger_result(
                db,
                "u1",
                {"run_at": f"2026-01-01T00:00:{idx:02d}Z", "verdict": "tie", "score_delta": 0},
            )
        history = load_challenger_history(db, "u1")
        assert len(history) == 60
        assert history[0]["run_at"].endswith("15Z")
        assert history[-1]["run_at"].endswith("74Z")
    finally:
        db.close()


def test_strategy_update_and_last_run_persist() -> None:
    db = _session()
    try:
        save_strategy_update(
            db,
            "u1",
            {
                "generated_at": "2026-04-14T00:00:00Z",
                "env_overrides": {"QUALITY_MIN_SIGNAL_SCORE": "56"},
                "updates": [{"env_key": "QUALITY_MIN_SIGNAL_SCORE", "suggested_value": 56}],
            },
        )
        save_learning_last_run(
            db,
            "u1",
            component="evolve",
            status="ok",
            message="generated",
            data={"updates_count": 1},
        )
        save_learning_last_run(
            db,
            "u1",
            component="challenger",
            status="no_update",
            message="missing overrides",
            data={},
        )

        last_run = load_state_json(db, "u1", LEARNING_LAST_RUN_KEY, {})
        assert isinstance(last_run, dict)
        assert last_run["evolve"]["status"] == "ok"
        assert last_run["challenger"]["status"] == "no_update"
    finally:
        db.close()
