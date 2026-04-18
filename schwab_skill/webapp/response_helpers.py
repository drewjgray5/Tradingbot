from __future__ import annotations

from datetime import datetime
from typing import Any

from .schemas import ApiResponse


def api_ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def api_err(message: str, data: Any = None) -> ApiResponse:
    return ApiResponse(ok=False, error=message, data=data)


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
