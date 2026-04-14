"""CORS allowed origins: dev defaults + Render / public URL discovery."""

from __future__ import annotations

import os
from urllib.parse import urlparse

_DEV_DEFAULT = (
    "http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5173,http://localhost:5173"
)


def _origin_from_url(url: str) -> str | None:
    u = (url or "").strip().rstrip("/")
    if not u:
        return None
    p = urlparse(u)
    if not p.scheme or not p.netloc:
        return None
    return f"{p.scheme}://{p.netloc}"


def build_allowed_origins() -> list[str]:
    """
    Merge explicit WEB_ALLOWED_ORIGINS with:
    - RENDER_EXTERNAL_URL (Render injects this on web services)
    - WEB_PUBLIC_ORIGIN (optional custom domain, full https://... URL)

    If WEB_ALLOWED_ORIGINS is unset or blank, use local dev defaults so
    `WEB_ALLOWED_ORIGINS=` on Render does not collapse to localhost-only.
    """
    raw = os.getenv("WEB_ALLOWED_ORIGINS")
    env = (os.getenv("ENV") or os.getenv("APP_ENV") or "").strip().lower()
    production_like = env in ("prod", "production", "staging") or bool((os.getenv("RENDER") or "").strip())
    if raw is None or not raw.strip():
        parts = [] if production_like else _DEV_DEFAULT.split(",")
    else:
        parts = raw.split(",")
    out: list[str] = []
    seen: set[str] = set()
    for o in parts:
        s = o.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    for env_key in ("RENDER_EXTERNAL_URL", "WEB_PUBLIC_ORIGIN"):
        origin = _origin_from_url(os.getenv(env_key, ""))
        if origin and origin not in seen:
            seen.add(origin)
            out.append(origin)

    if out:
        return out
    return [] if production_like else ["http://127.0.0.1:8000"]
