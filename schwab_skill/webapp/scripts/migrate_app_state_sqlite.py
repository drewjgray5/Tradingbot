"""
Rebuild SQLite `app_state` to match the current SQLAlchemy model (adds `id`, etc.).

Preserves rows by copying into a new table. Rows without `user_id` are assigned
WEB_LOCAL_USER_ID (default: local).

Run from `schwab_skill/`:

    python -m webapp.scripts.migrate_app_state_sqlite
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from sqlalchemy import text

from webapp.db import Base, engine


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn, name: str) -> bool:
    r = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    ).scalar()
    return r is not None


def _column_names(conn, table: str) -> list[str]:
    rows = conn.execute(text(f'PRAGMA table_info("{table}")')).fetchall()
    return [r[1] for r in rows]


def _ensure_local_user(conn, user_id: str) -> None:
    now = _utc_iso()
    conn.execute(
        text("""
            INSERT OR IGNORE INTO users (id, email, auth_provider, created_at, updated_at)
            VALUES (:id, NULL, 'local_dashboard', :created_at, :updated_at)
        """),
        {"id": user_id, "created_at": now, "updated_at": now},
    )


def migrate() -> int:
    if not str(engine.url).startswith("sqlite"):
        print("DATABASE_URL is not sqlite; nothing to do.", file=sys.stderr)
        return 0

    local_uid = (os.getenv("WEB_LOCAL_USER_ID", "local") or "local").strip() or "local"

    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))

        if not _table_exists(conn, "app_state"):
            print("No app_state table; schema will be created on next app start.")
            return 0

        cols = _column_names(conn, "app_state")
        if not cols:
            print("app_state has no columns; aborting.", file=sys.stderr)
            return 1

        if "id" in cols:
            print("app_state already has id column; no migration needed.")
            return 0

        if "key" not in cols:
            print("app_state missing required column key; manual fix required.", file=sys.stderr)
            return 1

        _ensure_local_user(conn, local_uid)

        conn.execute(
            text("""
                CREATE TABLE app_state__new (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    user_id VARCHAR(128) NOT NULL,
                    "key" VARCHAR(64) NOT NULL,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT fk_app_state_user_id FOREIGN KEY (user_id)
                        REFERENCES users (id) ON DELETE CASCADE
                )
            """)
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_app_state_user_key "
                "ON app_state__new (user_id, \"key\")"
            )
        )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_app_state_user_id ON app_state__new (user_id)")
        )
        conn.execute(
            text('CREATE INDEX IF NOT EXISTS ix_app_state_key ON app_state__new ("key")')
        )

        result = conn.execute(text('SELECT * FROM app_state'))
        keys = list(result.keys())
        rows = result.fetchall()

        merged: dict[tuple[str, str], tuple[str, str]] = {}
        for tup in rows:
            row = dict(zip(keys, tup))
            uid = str(row["user_id"]).strip() if row.get("user_id") not in (None, "") else local_uid
            k = str(row["key"])
            vj = row.get("value_json")
            if vj is None or vj == "":
                vj = "{}"
            else:
                vj = str(vj)
            ua = row.get("updated_at")
            if ua is None or ua == "":
                ua = _utc_iso()
            else:
                ua = str(ua)
            merged[(uid, k)] = (vj, ua)

        ins = text("""
            INSERT INTO app_state__new (user_id, "key", value_json, updated_at)
            VALUES (:user_id, :k, :value_json, :updated_at)
        """)
        for (uid, k), (vj, ua) in merged.items():
            conn.execute(ins, {"user_id": uid, "k": k, "value_json": vj, "updated_at": ua})

        conn.execute(text("DROP TABLE app_state"))
        conn.execute(text("ALTER TABLE app_state__new RENAME TO app_state"))

    n = len(merged)
    print(
        f"Migrated app_state ({n} row(s)); "
        f"user_id default for missing column: {local_uid!r}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(migrate())
