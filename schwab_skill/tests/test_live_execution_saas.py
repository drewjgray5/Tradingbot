"""SaaS live execution opt-in and staged-order confirmation."""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from webapp import main_saas, tenant_dashboard
from webapp.db import Base
from webapp.models import AuditLog, PendingTrade, ScanResult, User, UserCredential
from webapp.oauth_schwab import (
    SCHWAB_OAUTH_KIND_ACCOUNT,
    SCHWAB_OAUTH_KIND_MARKET,
    sign_schwab_oauth_state,
)
from webapp.security import decrypt_secret, encrypt_secret, get_current_user


@pytest.fixture
def cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "unit_test_jwt_secret")
    monkeypatch.delenv("SAAS_BILLING_ENFORCE", raising=False)
    monkeypatch.setenv("SCHWAB_MARKET_APP_KEY", "mk")
    monkeypatch.setenv("SCHWAB_MARKET_APP_SECRET", "ms")
    monkeypatch.setenv("SCHWAB_ACCOUNT_APP_KEY", "ak")
    monkeypatch.setenv("SCHWAB_ACCOUNT_APP_SECRET", "as")


@pytest.fixture
def test_db(cred_key: None) -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def saas_client(test_db: sessionmaker, cred_key: None) -> TestClient:
    def override_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    def override_get_current_user():
        db = test_db()
        try:
            u = db.query(User).filter(User.id == "user_1").first()
            assert u is not None
            return u
        finally:
            db.close()

    app = main_saas.app
    app.dependency_overrides[main_saas._db] = override_db
    app.dependency_overrides[tenant_dashboard._db] = override_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def _auth_header() -> dict[str, str]:
    token = jwt.encode({"sub": "user_1"}, "unit_test_jwt_secret", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _seed_user_with_schwab(db: Session) -> None:
    db.add(User(id="user_1", email="a@b.c", auth_provider="supabase", live_execution_enabled=False))
    market_json = json.dumps({"access_token": "m1", "refresh_token": "mr1"})
    account_json = json.dumps({"access_token": "a1", "refresh_token": "ar1"})
    db.add(
        UserCredential(
            user_id="user_1",
            market_token_payload_enc=encrypt_secret(market_json),
            account_token_payload_enc=encrypt_secret(account_json),
        )
    )
    db.commit()


def test_orders_execute_returns_410(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
    finally:
        db.close()

    r = saas_client.post(
        "/api/orders/execute",
        json={"ticker": "AAPL", "qty": 1, "side": "BUY", "order_type": "MARKET"},
        headers=_auth_header(),
    )
    assert r.status_code == 410


def test_approve_blocked_until_live_enabled(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
        db.add(
            PendingTrade(
                id="abc12345",
                user_id="user_1",
                ticker="AAPL",
                qty=1,
                price=100.0,
                status="pending",
                signal_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()

    r = saas_client.post(
        "/api/trades/abc12345/approve?confirm_live=true",
        json={"typed_ticker": "AAPL"},
        headers=_auth_header(),
    )
    assert r.status_code == 403


def test_enable_live_trading_then_approve(
    saas_client: TestClient,
    test_db: sessionmaker,
) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
        db.add(
            PendingTrade(
                id="abc12345",
                user_id="user_1",
                ticker="AAPL",
                qty=1,
                price=100.0,
                status="pending",
                signal_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()

    en = saas_client.post(
        "/api/settings/enable-live-trading",
        json={"risk_acknowledged": True, "typed_phrase": "ENABLE"},
        headers=_auth_header(),
    )
    assert en.status_code == 200
    body = en.json()
    assert body.get("ok") is True
    assert (body.get("data") or {}).get("live_execution_enabled") is True

    with (
        patch("webapp.tenant_dashboard.get_account_status", return_value={"accounts": []}),
        patch("webapp.tenant_dashboard.place_order", return_value={"orderId": "ord_1"}),
    ):
        ap = saas_client.post(
            "/api/trades/abc12345/approve?confirm_live=true",
            json={"typed_ticker": "AAPL"},
            headers=_auth_header(),
        )
    assert ap.status_code == 200
    payload = ap.json()
    assert payload.get("ok") is True


def test_enable_live_trading_requires_risk_ack(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
    finally:
        db.close()
    r = saas_client.post(
        "/api/settings/enable-live-trading",
        json={"risk_acknowledged": False, "typed_phrase": "ENABLE"},
        headers=_auth_header(),
    )
    assert r.status_code == 400
    assert "risk_acknowledged" in (r.json().get("detail") or "")


def test_enable_live_trading_requires_typed_enable(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
    finally:
        db.close()
    r = saas_client.post(
        "/api/settings/enable-live-trading",
        json={"risk_acknowledged": True, "typed_phrase": "enable"},
        headers=_auth_header(),
    )
    assert r.status_code == 400
    assert "Type the word ENABLE exactly" in (r.json().get("detail") or "")


def test_wrong_typed_ticker_rejected(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
        u = db.query(User).filter(User.id == "user_1").one()
        u.live_execution_enabled = True
        db.add(
            PendingTrade(
                id="abc12345",
                user_id="user_1",
                ticker="AAPL",
                qty=1,
                price=100.0,
                status="pending",
                signal_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()

    r = saas_client.post(
        "/api/trades/abc12345/approve?confirm_live=true",
        json={"typed_ticker": "MSFT"},
        headers=_auth_header(),
    )
    assert r.status_code == 200
    assert r.json().get("ok") is False


def test_trading_halted_blocks_approve(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
        u = db.query(User).filter(User.id == "user_1").one()
        u.live_execution_enabled = True
        u.trading_halted = True
        db.add(
            PendingTrade(
                id="halt001",
                user_id="user_1",
                ticker="MSFT",
                qty=1,
                price=200.0,
                status="pending",
                signal_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()

    r = saas_client.post(
        "/api/trades/halt001/approve?confirm_live=true",
        json={"typed_ticker": "MSFT"},
        headers=_auth_header(),
    )
    assert r.status_code == 403


def test_approve_requires_confirm_live_query_flag(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
        u = db.query(User).filter(User.id == "user_1").one()
        u.live_execution_enabled = True
        db.add(
            PendingTrade(
                id="needconfirm",
                user_id="user_1",
                ticker="AAPL",
                qty=1,
                price=100.0,
                status="pending",
                signal_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()

    with patch("webapp.tenant_dashboard.get_account_status", return_value={"accounts": []}):
        r = saas_client.post(
            "/api/trades/needconfirm/approve",
            json={"typed_ticker": "AAPL"},
            headers=_auth_header(),
        )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert "confirm_live=true" in (body.get("error") or "")
    assert isinstance((body.get("data") or {}).get("checklist"), dict)


def test_patch_trading_halt(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
    finally:
        db.close()


def test_portfolio_risk_route_available_in_saas(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
    finally:
        db.close()

    fake_status = {
        "accounts": [
            {
                "securitiesAccount": {
                    "positions": [
                        {
                            "instrument": {"symbol": "AAPL"},
                            "longQuantity": 10,
                            "marketValue": 2000,
                            "currentDayProfitLoss": 25,
                            "averagePrice": 190,
                        }
                    ]
                }
            }
        ]
    }
    with (
        patch("webapp.tenant_dashboard.get_account_status", return_value=fake_status),
        patch("sector_strength.get_ticker_sector_etf", return_value="XLK"),
    ):
        r = saas_client.get("/api/portfolio/risk", headers=_auth_header())
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    data = body.get("data") or {}
    assert data.get("position_count") == 1
    assert isinstance(data.get("sector_allocation"), list)
    rec = data.get("recommendation") or {}
    assert isinstance(rec, dict)
    assert rec.get("headline")


def test_pending_trade_delete_endpoints_in_saas(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
        db.add(
            PendingTrade(
                id="del1",
                user_id="user_1",
                ticker="AAPL",
                qty=1,
                price=100.0,
                status="pending",
                signal_json="{}",
            )
        )
        db.add(
            PendingTrade(
                id="del2",
                user_id="user_1",
                ticker="MSFT",
                qty=2,
                price=250.0,
                status="executed",
                signal_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()

    one = saas_client.post("/api/trades/del1/delete", headers=_auth_header())
    assert one.status_code == 200
    assert one.json().get("ok") is True
    assert (one.json().get("data") or {}).get("deleted") == "del1"

    all_rows = saas_client.post("/api/pending-trades/delete-all", headers=_auth_header())
    assert all_rows.status_code == 200
    payload = all_rows.json()
    assert payload.get("ok") is True
    data = payload.get("data") or {}
    assert data.get("deleted") == 1
    assert (data.get("by_status") or {}).get("executed") == 1


def test_analytics_event_endpoint_records_audit_row(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
    finally:
        db.close()

    r = saas_client.post(
        "/api/analytics/event",
        json={"event": "first_scan", "properties": {"signals_found": 7}},
        headers=_auth_header(),
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True

    db = test_db()
    try:
        rows = (
            db.query(AuditLog).filter(AuditLog.user_id == "user_1", AuditLog.action == "product_analytics_event").all()
        )
        assert rows
        latest = rows[-1]
        detail = json.loads(latest.detail_json or "{}")
        assert detail.get("event") == "first_scan"
        assert (detail.get("properties") or {}).get("signals_found") == 7
    finally:
        db.close()


def test_scan_task_status_excludes_recent_by_default(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
        db.add(
            main_saas.AppState(
                user_id="user_1",
                key="task_binding:scan:task_123",
                value_json=json.dumps({"task_id": "task_123", "scope": "scan"}),
            )
        )
        db.add(
            ScanResult(
                user_id="user_1",
                job_id="job_123",
                ticker="AAPL",
                signal_score=77.7,
                payload_json=json.dumps({"ticker": "AAPL"}),
            )
        )
        db.commit()
    finally:
        db.close()

    with patch("webapp.main_saas.AsyncResult") as mock_result:
        mock_result.return_value.status = "PENDING"
        mock_result.return_value.ready.return_value = False
        resp = saas_client.get("/api/scan/task_123", headers=_auth_header())
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["task_id"] == "task_123"
    assert "recent_results" not in payload


def test_scan_task_status_can_include_recent(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
        db.add(
            main_saas.AppState(
                user_id="user_1",
                key="task_binding:scan:task_123",
                value_json=json.dumps({"task_id": "task_123", "scope": "scan"}),
            )
        )
        db.add(
            ScanResult(
                user_id="user_1",
                job_id="job_123",
                ticker="MSFT",
                signal_score=66.6,
                payload_json=json.dumps({"ticker": "MSFT"}),
            )
        )
        db.commit()
    finally:
        db.close()

    with patch("webapp.main_saas.AsyncResult") as mock_result:
        mock_result.return_value.status = "PENDING"
        mock_result.return_value.ready.return_value = False
        resp = saas_client.get("/api/scan/task_123?include_recent=true", headers=_auth_header())
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert isinstance(payload.get("recent_results"), list)
    assert payload["recent_results"][0]["ticker"] == "MSFT"


def test_scan_enqueue_routes_to_scan_queue(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
    finally:
        db.close()

    class _Task:
        id = "scan_task_1"

    with (
        patch("webapp.main_saas._scan_rate_limit", return_value=None),
        patch("webapp.main_saas._scan_daily_limit_check", return_value=None),
        patch("webapp.main_saas.acquire_scan_cooldown", return_value=True),
        patch("webapp.main_saas.scan_for_user.apply_async", return_value=_Task()) as mock_async,
    ):
        resp = saas_client.post("/api/scan?async_mode=true", json={}, headers=_auth_header())

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert (body.get("data") or {}).get("task_id") == "scan_task_1"
    assert mock_async.call_args.kwargs.get("queue") == "scan"


def test_phase2_stage1_enqueue_routes_to_phase2_queue(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
    finally:
        db.close()

    class _Task:
        id = "phase2_task_1"

    with (
        patch("webapp.main_saas._backtest_rate_limit", return_value=None),
        patch("webapp.main_saas.phase2_stage1_for_user.apply_async", return_value=_Task()) as mock_async,
    ):
        resp = saas_client.post("/api/phase2/stage1-runs", headers=_auth_header())

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert (body.get("data") or {}).get("task_id") == "phase2_task_1"
    assert mock_async.call_args.kwargs.get("queue") == "phase2"


def test_scan_lifecycle_task_id_preserves_response_shape(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
        db.add(
            main_saas.AppState(
                user_id="user_1",
                key="task_binding:scan:task_123",
                value_json=json.dumps({"task_id": "task_123", "scope": "scan"}),
            )
        )
        db.commit()
    finally:
        db.close()

    with patch("webapp.main_saas.AsyncResult") as mock_result:
        mock_result.return_value.status = "PENDING"
        mock_result.return_value.ready.return_value = False
        resp = saas_client.get("/api/scan-lifecycle?task_id=task_123", headers=_auth_header())

    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["mode"] == "saas"
    assert payload["transport"] == "celery"
    assert payload["task_id"] == "task_123"
    assert payload["status"] in {"pending", "received", "started", "running", "unknown"}


def test_scan_lifecycle_idle_with_last_scan(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
        db.add(
            main_saas.AppState(
                user_id="user_1",
                key="last_scan",
                value_json=json.dumps({"at": "2026-01-01T00:00:00Z", "signals_found": 2, "job_id": "job_123"}),
            )
        )
        db.commit()
    finally:
        db.close()

    resp = saas_client.get("/api/scan-lifecycle", headers=_auth_header())
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["mode"] == "saas"
    assert payload["transport"] == "celery"
    assert payload["status"] == "idle"
    assert payload["task_id"] is None
    assert (payload.get("last_scan") or {}).get("job_id") == "job_123"
    r = saas_client.patch(
        "/api/settings/trading-halt",
        json={"halted": True},
        headers=_auth_header(),
    )
    assert r.status_code == 200
    assert (r.json().get("data") or {}).get("trading_halted") is True
    db = test_db()
    try:
        u = db.query(User).filter(User.id == "user_1").one()
        assert u.trading_halted is True
    finally:
        db.close()


def test_onboarding_status_returns_json_error_when_internal_probe_fails(
    saas_client: TestClient,
    test_db: sessionmaker,
) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
    finally:
        db.close()
    with patch("webapp.main_saas._tenant_api_health_snapshot", side_effect=RuntimeError("boom")):
        resp = saas_client.get("/api/onboarding/status", headers=_auth_header())
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is False
    assert "Unable to load onboarding status right now." in (body.get("error") or "")


def test_scan_task_status_requires_user_binding(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
    finally:
        db.close()

    with patch("webapp.main_saas.AsyncResult") as mock_result:
        mock_result.return_value.status = "PENDING"
        mock_result.return_value.ready.return_value = False
        resp = saas_client.get("/api/scan/foreign_task", headers=_auth_header())
    assert resp.status_code == 404


def test_health_ready_reports_worker_not_ready(saas_client: TestClient) -> None:
    class _SessionOk:
        def execute(self, _query):
            return 1

        def close(self):
            return None

    with (
        patch("webapp.main_saas.SessionLocal", return_value=_SessionOk()),
        patch("webapp.main_saas.redis_ping", return_value=True),
        patch(
            "webapp.main_saas._celery_worker_health",
            return_value={"reachable": False, "workers": 0, "queues": []},
        ),
    ):
        resp = saas_client.get("/api/health/ready")
    assert resp.status_code == 503
    payload = resp.json()["data"]
    assert payload["database"] is True
    assert payload["redis"] is True
    assert payload["worker_ok"] is False
    assert payload["queues_ok"] is False


def test_public_config_and_runtime_contract_in_saas(saas_client: TestClient) -> None:
    cfg = saas_client.get("/api/public-config")
    assert cfg.status_code == 200
    body = cfg.json()
    assert body.get("ok") is True
    data = body.get("data") or {}
    assert data.get("saas_mode") is True
    assert data.get("runtime_mode") == "saas"
    assert data.get("scan_transport") == "celery"
    assert data.get("sse_enabled") is False
    assert data.get("ui_contract_version") == "2026-04-webapp-stabilization"

    runtime = saas_client.get("/api/runtime-contract")
    assert runtime.status_code == 200
    runtime_data = runtime.json().get("data") or {}
    assert runtime_data.get("runtime_mode") == "saas"
    assert runtime_data.get("scan_transport") == "celery"
    assert runtime_data.get("sse_enabled") is False
    assert runtime_data.get("api_envelope") == "ApiResponse"


def test_validate_startup_configuration_requires_prod_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    for key in (
        "DATABASE_URL",
        "REDIS_URL",
        "CREDENTIAL_ENCRYPTION_KEY",
        "OAUTH_STATE_SECRET",
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_JWT_SECRET",
        "SUPABASE_JWT_AUDIENCE",
        "SUPABASE_JWT_ISSUER",
        "WEB_ALLOWED_ORIGINS",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        main_saas._validate_startup_configuration()
    text = str(exc_info.value)
    assert "DATABASE_URL" in text
    assert "WEB_ALLOWED_ORIGINS" in text


def test_metrics_requires_internal_key_when_configured(
    saas_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEB_INTERNAL_API_KEY", "internal-123")
    no_key = saas_client.get("/metrics")
    assert no_key.status_code == 401
    with_key = saas_client.get("/metrics", headers={"X-Internal-Key": "internal-123"})
    assert with_key.status_code == 200
    assert "tradingbot_process_uptime_seconds" in with_key.text


def test_metrics_rejected_in_production_without_internal_key(
    saas_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("WEB_INTERNAL_API_KEY", raising=False)
    resp = saas_client.get("/metrics")
    assert resp.status_code == 503
    assert "WEB_INTERNAL_API_KEY" in (resp.json().get("detail") or "")


def test_market_oauth_authorize_falls_back_to_request_origin_when_callback_missing(
    saas_client: TestClient, test_db: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = test_db()
    try:
        db.add(User(id="user_1", email="a@b.c", auth_provider="supabase"))
        db.commit()
    finally:
        db.close()
    monkeypatch.delenv("SCHWAB_MARKET_CALLBACK_URL", raising=False)
    r = saas_client.get("/api/oauth/schwab/market/authorize-url", headers=_auth_header())
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    url = (body.get("data") or {}).get("url") or ""
    assert "redirect_uri=http%3A%2F%2Ftestserver%2Fapi%2Foauth%2Fschwab%2Fmarket%2Fcallback" in url


def test_market_oauth_authorize_returns_url(
    saas_client: TestClient, test_db: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "SCHWAB_MARKET_CALLBACK_URL",
        "https://example.com/api/oauth/schwab/market/callback",
    )
    db = test_db()
    try:
        db.add(User(id="user_1", email="a@b.c", auth_provider="supabase"))
        db.commit()
    finally:
        db.close()
    r = saas_client.get("/api/oauth/schwab/market/authorize-url", headers=_auth_header())
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    url = (body.get("data") or {}).get("url") or ""
    assert "client_id=mk" in url.replace("%3D", "=") or "mk" in url


def test_market_oauth_callback_stores_payload(
    saas_client: TestClient, test_db: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCHWAB_MARKET_CALLBACK_URL", "https://127.0.0.1:8182")
    monkeypatch.setenv("SAAS_FRONTEND_URL", "http://front.test")
    db = test_db()
    try:
        db.add(User(id="user_1", email="a@b.c", auth_provider="supabase"))
        db.commit()
    finally:
        db.close()
    state = sign_schwab_oauth_state("user_1", SCHWAB_OAUTH_KIND_MARKET)
    with patch(
        "webapp.tenant_dashboard.exchange_schwab_code_for_tokens",
        return_value={
            "access_token": "ma",
            "refresh_token": "mr",
            "token_type": "Bearer",
        },
    ) as exchange_mock:
        r = saas_client.get(
            "/api/oauth/schwab/market/callback",
            params={"code": "c1", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 302
    loc = r.headers.get("location") or ""
    assert "schwab_market_oauth=ok" in loc
    exchange_mock.assert_called_once()
    assert exchange_mock.call_args.args[3] == "http://testserver/api/oauth/schwab/market/callback"

    db = test_db()
    try:
        row = db.query(UserCredential).filter(UserCredential.user_id == "user_1").one()
        raw = decrypt_secret(row.market_token_payload_enc or "")
        assert raw
        blob = json.loads(raw)
        assert blob.get("access_token") == "ma"
        assert blob.get("refresh_token") == "mr"
    finally:
        db.close()


def test_market_oauth_callback_rejects_account_state(saas_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHWAB_MARKET_CALLBACK_URL", "https://cb.example/m")
    monkeypatch.setenv("SAAS_FRONTEND_URL", "http://front.test")
    state = sign_schwab_oauth_state("user_1", SCHWAB_OAUTH_KIND_ACCOUNT)
    r = saas_client.get(
        "/api/oauth/schwab/market/callback",
        params={"code": "c1", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "schwab_market_oauth=error" in (r.headers.get("location") or "")


def test_account_oauth_callback_rejects_market_state(saas_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHWAB_CALLBACK_URL", "https://cb.example/a")
    monkeypatch.setenv("SAAS_FRONTEND_URL", "http://front.test")
    state = sign_schwab_oauth_state("user_1", SCHWAB_OAUTH_KIND_MARKET)
    r = saas_client.get(
        "/api/oauth/schwab/callback",
        params={"code": "c1", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "schwab_oauth=error" in (r.headers.get("location") or "")


def test_account_oauth_authorize_ignores_loopback_callback_on_hosted_origin(
    saas_client: TestClient, test_db: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182")
    db = test_db()
    try:
        db.add(User(id="user_1", email="a@b.c", auth_provider="supabase"))
        db.commit()
    finally:
        db.close()
    r = saas_client.get("/api/oauth/schwab/authorize-url", headers=_auth_header())
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    url = (body.get("data") or {}).get("url") or ""
    assert "redirect_uri=http%3A%2F%2Ftestserver%2Fapi%2Foauth%2Fschwab%2Fcallback" in url
