from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator


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
    # Full JSON body from Schwab token endpoint (encrypted at rest), per OAuth app.
    market_oauth_json: str | None = None
    account_oauth_json: str | None = None


class ApproveTradeRequest(BaseModel):
    """In-app confirmation: user must re-type the trade ticker before a live order is sent."""

    typed_ticker: str = Field(min_length=1, max_length=16)


class EnableLiveTradingRequest(BaseModel):
    risk_acknowledged: bool = False
    typed_phrase: str = Field(min_length=1, max_length=32)


class UpdateTradingHaltRequest(BaseModel):
    """Pause all live orders for this account (exits still allowed unless platform blocks exits)."""

    halted: bool = False


class ExecuteOrderRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    qty: int = Field(gt=0)
    side: str = Field(default="BUY")
    order_type: str = Field(default="MARKET")
    price: float | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class BillingCheckoutPayload(BaseModel):
    """Stripe Checkout URLs (optional; env fallbacks used when unset)."""

    success_url: HttpUrl | None = None
    cancel_url: HttpUrl | None = None


# Backward-compatible name (avoid `*Request | None` annotations — FastAPI can misread them).
BillingCheckoutRequest = BillingCheckoutPayload


class QueueUserBacktestRequest(BaseModel):
    spec: dict[str, Any]


class StrategyChatRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=50)

    @field_validator("messages")
    @classmethod
    def _message_shape(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for i, m in enumerate(v):
            if not isinstance(m, dict):
                raise ValueError(f"message {i} must be an object")
            if m.get("role") not in ("user", "assistant"):
                raise ValueError(f"message {i}: role must be user or assistant")
            if m.get("content") is None:
                raise ValueError(f"message {i}: content is required")
        return v

