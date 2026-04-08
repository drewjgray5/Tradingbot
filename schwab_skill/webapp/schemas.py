from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    ok: bool
    data: Any | None = None
    error: str | None = None


class CreatePendingTrade(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    qty: int | None = None
    price: float | None = None
    signal: dict[str, Any] | None = None
    note: str | None = None


class AdvisoryPrediction(BaseModel):
    p_up_10d: float | None = None
    p_up_10d_raw: float | None = None
    confidence_bucket: str | None = None
    model_version: str | None = None
    expected_move_10d: float | None = None
    feature_coverage: float | None = None
    reasoning: str | None = None


class SchwabCredentialUpsert(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str | None = None
    expires_at: str | None = None
    scopes: list[str] | None = None


class ExecuteOrderRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    qty: int = Field(gt=0)
    side: str = Field(default="BUY")
    order_type: str = Field(default="MARKET")
    price: float | None = None

