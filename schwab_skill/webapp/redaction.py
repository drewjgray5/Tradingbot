from __future__ import annotations

import re
from typing import Any

_TOKEN_VALUE_RE = re.compile(
    r"(?i)\b("
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|authorization|bearer|"
    r"api[_-]?key|secret|password|passwd|credential|session"
    r")\b\s*[:=]\s*([^\s,;]+)"
)
_JWT_RE = re.compile(r"\b[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b")
_LONG_HEX_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")


def redact_sensitive_text(value: str) -> str:
    text = str(value or "")
    text = _TOKEN_VALUE_RE.sub(r"\1=[REDACTED]", text)
    text = _JWT_RE.sub("[REDACTED_JWT]", text)
    text = _LONG_HEX_RE.sub("[REDACTED_HEX]", text)
    return text


def safe_exception_message(exc: Exception, *, fallback: str = "Unexpected error.") -> str:
    raw = str(exc or "").strip()
    if not raw:
        return fallback
    return redact_sensitive_text(raw)


def redact_mapping(values: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (values or {}).items():
        k = str(key)
        if any(word in k.lower() for word in ("token", "secret", "password", "api_key", "authorization")):
            out[k] = "[REDACTED]"
            continue
        if isinstance(value, str):
            out[k] = redact_sensitive_text(value)
        else:
            out[k] = value
    return out
