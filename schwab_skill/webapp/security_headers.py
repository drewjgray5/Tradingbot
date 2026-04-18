"""Security-headers middleware shared by `webapp.main` and `webapp.main_saas`.

Opt-in via env so we don't break local dev:

* ``WEB_SECURITY_HEADERS=1`` enables baseline headers (X-Content-Type-Options,
  Referrer-Policy, X-Frame-Options, Permissions-Policy).
* ``WEB_CSP_MODE=report-only`` adds a Content-Security-Policy-Report-Only header
  derived from the same policy. Use this first to surface violations in browser
  consoles without breaking the dashboard.
* ``WEB_CSP_MODE=enforce`` switches to ``Content-Security-Policy``.
* ``WEB_CSP_REPORT_URI=/api/csp-report`` (optional) appends a report-uri so
  browsers POST violations back; the receiving endpoint is up to the app.

Tightening the CSP requires moving the inline ``<script>``/``<style>`` blocks
in ``static/index.html`` to external files (or adding nonces). Until that's
done, the baseline policy below allows ``'unsafe-inline'`` for both, which is
intentional — running this in report-only mode first is the safer rollout.
"""

from __future__ import annotations

import os

from starlette.types import ASGIApp


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _build_csp(report_uri: str | None) -> str:
    # Allow the lightweight-charts CDN host explicitly. SRI on the script tag
    # protects against payload tampering; CSP restricts which hosts can be loaded.
    parts = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://unpkg.com",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data:",
        "connect-src 'self' https: wss:",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ]
    if report_uri:
        parts.append(f"report-uri {report_uri}")
    return "; ".join(parts)


class SecurityHeadersMiddleware:
    """ASGI middleware that decorates HTML responses with security headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):  # type: ignore[override]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        enabled = _truthy(os.getenv("WEB_SECURITY_HEADERS"))
        csp_mode = (os.getenv("WEB_CSP_MODE") or "").strip().lower()
        report_uri = (os.getenv("WEB_CSP_REPORT_URI") or "").strip() or None

        async def send_wrapper(message):
            if enabled and message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                # Avoid clobbering anything an inner handler already set.
                existing = {k.lower() for k, _v in headers}
                additions: list[tuple[bytes, bytes]] = []
                if b"x-content-type-options" not in existing:
                    additions.append((b"x-content-type-options", b"nosniff"))
                if b"referrer-policy" not in existing:
                    additions.append((b"referrer-policy", b"no-referrer-when-downgrade"))
                if b"x-frame-options" not in existing:
                    additions.append((b"x-frame-options", b"DENY"))
                if b"permissions-policy" not in existing:
                    additions.append(
                        (b"permissions-policy", b"geolocation=(), microphone=(), camera=()")
                    )
                if csp_mode in ("report-only", "report_only"):
                    if b"content-security-policy-report-only" not in existing:
                        additions.append(
                            (
                                b"content-security-policy-report-only",
                                _build_csp(report_uri).encode("ascii"),
                            )
                        )
                elif csp_mode == "enforce":
                    if b"content-security-policy" not in existing:
                        additions.append(
                            (b"content-security-policy", _build_csp(report_uri).encode("ascii"))
                        )
                # Encode header keys for ASGI.
                additions = [(k if isinstance(k, bytes) else k.encode("ascii"), v) for k, v in additions]
                headers.extend(additions)
                message = dict(message)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


__all__ = ["SecurityHeadersMiddleware"]
