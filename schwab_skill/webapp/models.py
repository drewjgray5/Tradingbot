from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    auth_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="supabase")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class UserCredential(Base):
    __tablename__ = "user_credentials"

    user_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False, default="BUY")
    order_type: Mapped[str] = mapped_column(String(16), nullable=False, default="MARKET")
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True, default="pending")
    broker_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    qty: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    avg_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class ScanResult(Base):
    __tablename__ = "scan_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    signal_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AppState(Base):
    __tablename__ = "app_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class PendingTrade(Base):
    """Compatibility table kept for legacy UI flows."""

    __tablename__ = "pending_trades"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    user_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True, default="pending")
    signal_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

