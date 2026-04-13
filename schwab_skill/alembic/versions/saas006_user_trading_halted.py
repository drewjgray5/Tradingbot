"""Add users.trading_halted (self-service pause).

Revision ID: saas006
Revises: saas005
Create Date: 2026-04-11

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "saas006"
down_revision: Union[str, Sequence[str], None] = "saas005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    insp = sa.inspect(conn)
    if "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "trading_halted" in cols:
        return
    if dialect == "sqlite":
        with op.batch_alter_table("users", schema=None) as batch:
            batch.add_column(
                sa.Column(
                    "trading_halted",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
    else:
        op.add_column(
            "users",
            sa.Column(
                "trading_halted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    insp = sa.inspect(conn)
    if "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "trading_halted" not in cols:
        return
    if dialect == "sqlite":
        with op.batch_alter_table("users", schema=None) as batch:
            batch.drop_column("trading_halted")
    else:
        op.drop_column("users", "trading_halted")
