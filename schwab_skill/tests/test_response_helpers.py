from __future__ import annotations

import pytest

from webapp import main, main_saas


def _as_dict(payload):
    return payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()


def test_ok_response_envelope_is_consistent_across_local_and_saas() -> None:
    local = _as_dict(main._ok({"k": "v"}))
    saas = _as_dict(main_saas._ok({"k": "v"}))
    assert local == {"ok": True, "data": {"k": "v"}, "error": None}
    assert saas == {"ok": True, "data": {"k": "v"}, "error": None}


def test_main_err_maps_recovery_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_record_endpoint_error", lambda _endpoint: None)
    monkeypatch.setattr(
        main,
        "_map_failure",
        lambda raw, source: {
            "title": "Auth issue",
            "summary": "Reconnect OAuth",
            "raw_error": f"{source}: {raw}",
        },
    )
    out = _as_dict(main._err("status", RuntimeError("token expired")))
    assert out["ok"] is False
    assert out["error"] == "Auth issue: Reconnect OAuth — status: token expired"
    assert out["data"]["recovery"]["summary"] == "Reconnect OAuth"


def test_saas_err_response_envelope() -> None:
    out = _as_dict(main_saas._err("bad request", {"hint": "check token"}))
    assert out == {"ok": False, "error": "bad request", "data": {"hint": "check token"}}
