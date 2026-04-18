"""Tests for ``webapp/route_helpers.py``.

These pin the contract that the local (`webapp.main`), tenant-scoped SaaS
(`webapp.tenant_dashboard`), and SaaS top-level (`webapp.main_saas`)
modules all delegate to the same shared helpers — preventing the three
files from drifting on response envelope, redirect URI rules, profile
activation, or request-id propagation.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from webapp import main as local_main
from webapp import main_saas, tenant_dashboard
from webapp import route_helpers as rh


def _make_request(*, scheme: str = "http", netloc: str = "localhost:8000",
                  forwarded_proto: str | None = None,
                  forwarded_host: str | None = None,
                  request_id: str | None = None) -> MagicMock:
    request = MagicMock()
    request.url.scheme = scheme
    request.url.netloc = netloc
    request.base_url = f"{scheme}://{netloc}/"
    headers: dict[str, str] = {}
    if forwarded_proto:
        headers["x-forwarded-proto"] = forwarded_proto
    if forwarded_host:
        headers["x-forwarded-host"] = forwarded_host
    request.headers = headers
    state = MagicMock()
    state.request_id = request_id
    request.state = state
    return request


# ---------------------------------------------------------------------------
# Response envelope parity (`ok` / `simple_err` / `saas_error_response`).
# ---------------------------------------------------------------------------


def test_ok_envelope_is_identical_across_modules() -> None:
    payload = {"x": 1, "nested": {"y": [1, 2]}}
    expected = rh.ok(payload).model_dump()
    assert local_main._ok(payload).model_dump() == expected
    assert tenant_dashboard._ok(payload).model_dump() == expected
    assert main_saas._ok(payload).model_dump() == expected


def test_simple_err_envelope_is_identical_for_saas_modules() -> None:
    expected = rh.simple_err("oops", {"detail": "x"}).model_dump()
    assert tenant_dashboard._err("oops", {"detail": "x"}).model_dump() == expected
    assert main_saas._err("oops", {"detail": "x"}).model_dump() == expected


def test_saas_error_response_includes_recovery_envelope() -> None:
    exc = RuntimeError("boom")
    out = rh.saas_error_response(exc, source="status", fallback="Status failed").model_dump()
    assert out["ok"] is False
    assert out["error"] == "Status failed"
    assert "recovery" in (out.get("data") or {})
    # tenant_dashboard's wrapper must produce the same envelope.
    out_tenant = tenant_dashboard._saas_error_response(
        exc, source="status", fallback="Status failed"
    ).model_dump()
    assert out_tenant == out


# ---------------------------------------------------------------------------
# Profile activation parity.
# ---------------------------------------------------------------------------


def test_apply_profile_to_runtime_sets_env_and_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("POSITION_SIZE_USD", "MAX_TRADES_PER_DAY"):
        monkeypatch.delenv(key, raising=False)
    payload = rh.apply_profile_to_runtime("balanced")
    assert payload["POSITION_SIZE_USD"] == "500"
    assert os.environ["POSITION_SIZE_USD"] == "500"
    # Local + tenant wrappers should produce the same payload.
    assert local_main._apply_profile_to_runtime("balanced") == payload
    assert tenant_dashboard._apply_profile_to_runtime("balanced") == payload


def test_apply_profile_to_runtime_falls_back_to_default_for_unknown_profile() -> None:
    payload = rh.apply_profile_to_runtime("not-a-real-profile")
    # Falls back to the "balanced" preset.
    balanced = rh.apply_profile_to_runtime("balanced")
    assert payload == balanced


# ---------------------------------------------------------------------------
# Request origin / loopback / redirect URI parity.
# ---------------------------------------------------------------------------


def test_request_origin_uses_forwarded_headers_when_present() -> None:
    req = _make_request(
        scheme="http", netloc="localhost:8000",
        forwarded_proto="https", forwarded_host="dash.example.com",
    )
    assert rh.request_origin(req) == "https://dash.example.com"
    # Local + tenant wrappers must be byte-identical.
    assert local_main._request_origin(req) == "https://dash.example.com"
    assert tenant_dashboard._request_origin(req) == "https://dash.example.com"


def test_request_origin_falls_back_to_request_url() -> None:
    req = _make_request(scheme="https", netloc="api.example.com")
    assert rh.request_origin(req) == "https://api.example.com"


def test_is_loopback_host_recognises_standard_loopback_names() -> None:
    for host in ("127.0.0.1", "localhost", "::1", "LOCALHOST", "  127.0.0.1  "):
        assert rh.is_loopback_host(host)
    for host in ("api.example.com", "192.168.1.10", "", None):
        assert not rh.is_loopback_host(host)


def test_resolve_schwab_redirect_uri_uses_inferred_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCHWAB_CALLBACK_URL", raising=False)
    monkeypatch.delenv("SCHWAB_MARKET_CALLBACK_URL", raising=False)
    req = _make_request(
        scheme="http", netloc="localhost:8000",
        forwarded_proto="https", forwarded_host="dash.example.com",
    )
    assert (
        rh.resolve_schwab_redirect_uri(req, market=False)
        == "https://dash.example.com/api/oauth/schwab/callback"
    )
    assert (
        rh.resolve_schwab_redirect_uri(req, market=True)
        == "https://dash.example.com/api/oauth/schwab/market/callback"
    )


def test_resolve_schwab_redirect_uri_overrides_legacy_loopback_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182")
    req = _make_request(
        scheme="https", netloc="dash.example.com",
        forwarded_proto="https", forwarded_host="dash.example.com",
    )
    # Legacy loopback callback with no matching path -> prefer inferred webapp callback.
    assert (
        rh.resolve_schwab_redirect_uri(req, market=False)
        == "https://dash.example.com/api/oauth/schwab/callback"
    )


def test_resolve_schwab_redirect_uri_prefers_configured_when_path_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SCHWAB_CALLBACK_URL",
        "https://oauth.example.com/api/oauth/schwab/callback",
    )
    req = _make_request(
        scheme="https", netloc="dash.example.com",
        forwarded_proto="https", forwarded_host="dash.example.com",
    )
    assert (
        rh.resolve_schwab_redirect_uri(req, market=False)
        == "https://oauth.example.com/api/oauth/schwab/callback"
    )


def test_resolve_schwab_redirect_uri_swaps_loopback_when_serving_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    # Loopback configured callback path matches the suffix, but the request
    # arrived from a public host -> prefer the inferred URL so SaaS doesn't
    # send users to localhost.
    monkeypatch.setenv(
        "SCHWAB_CALLBACK_URL",
        "https://127.0.0.1/api/oauth/schwab/callback",
    )
    req = _make_request(
        scheme="https", netloc="dash.example.com",
        forwarded_proto="https", forwarded_host="dash.example.com",
    )
    assert (
        rh.resolve_schwab_redirect_uri(req, market=False)
        == "https://dash.example.com/api/oauth/schwab/callback"
    )


# ---------------------------------------------------------------------------
# Request-id propagation parity.
# ---------------------------------------------------------------------------


def test_request_id_returns_state_value() -> None:
    req = _make_request(request_id="abc-123")
    assert rh.request_id(req) == "abc-123"
    assert tenant_dashboard._request_id(req) == "abc-123"
    assert main_saas._request_id(req) == "abc-123"


def test_request_id_returns_none_when_middleware_absent() -> None:
    req = MagicMock()
    req.state = MagicMock(spec=[])  # no `request_id` attribute
    assert rh.request_id(req) is None
