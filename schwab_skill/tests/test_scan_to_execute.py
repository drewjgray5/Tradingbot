"""Integration tests: full scan → pending trade → preflight → approve/reject flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from webapp.db import Base
from webapp.models import User

FAKE_SIGNALS = [
    {
        "ticker": "AAPL",
        "price": 185.50,
        "signal_score": 72.0,
        "mirofish_conviction": 35,
        "sector_etf": "XLK",
        "strategy_attribution": {"top_live": "breakout"},
        "advisory": {"confidence_bucket": "high"},
        "event_risk": {"flagged": False},
    },
    {
        "ticker": "MSFT",
        "price": 420.10,
        "signal_score": 65.0,
        "mirofish_conviction": 28,
        "sector_etf": "XLK",
        "strategy_attribution": {"top_live": "pullback"},
        "advisory": {"confidence_bucket": "medium"},
        "event_risk": {"flagged": False},
    },
]

FAKE_DIAGNOSTICS = {
    "watchlist_size": 200,
    "stage2_fail": 150,
    "vcp_fail": 30,
    "sector_fail": 10,
    "scan_blocked": 0,
    "exceptions": 0,
}

FAKE_QUOTE = {"AAPL": {"quote": {"lastPrice": 186.0, "mark": 186.0}}}

FAKE_ACCOUNT_STATUS = {
    "accounts": [
        {
            "securitiesAccount": {
                "accountNumber": "12345",
                "hashValue": "abc",
                "positions": [
                    {
                        "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
                        "longQuantity": 10,
                        "marketValue": 1860.0,
                        "currentDayProfitLoss": 15.0,
                        "averagePrice": 180.0,
                    }
                ],
            }
        }
    ],
    "account_ids": ["12345"],
}


@pytest.fixture
def test_db(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WEB_API_KEY", "test-key-123")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    db = Session()
    db.add(User(id="local", email=None, auth_provider="local_dashboard"))
    db.commit()
    db.close()

    return Session


@pytest.fixture
def client(test_db: sessionmaker) -> TestClient:
    from webapp import main as webapp_main

    def override_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    app = webapp_main.app
    app.dependency_overrides[webapp_main.get_db] = override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _auth_headers() -> dict[str, str]:
    return {"X-API-Key": "test-key-123"}


class TestScanEndpoint:
    @patch("webapp.main.scan_for_signals_detailed")
    def test_sync_scan_returns_signals(self, mock_scan, client: TestClient):
        mock_scan.return_value = (FAKE_SIGNALS, FAKE_DIAGNOSTICS)
        resp = client.post(
            "/api/scan?async_mode=false",
            json={},
            headers=_auth_headers(),
        )
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["signals_found"] == 2
        assert data["data"]["signals"][0]["ticker"] == "AAPL"
        assert "diagnostics" in data["data"]
        assert "diagnostics_summary" in data["data"]
        assert "strategy_summary" in data["data"]

    @patch("webapp.main.scan_for_signals_detailed")
    def test_async_scan_starts_job(self, mock_scan, client: TestClient):
        mock_scan.return_value = (FAKE_SIGNALS, FAKE_DIAGNOSTICS)
        resp = client.post(
            "/api/scan?async_mode=true",
            json={},
            headers=_auth_headers(),
        )
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["status"] in ("running", "completed")

    def test_scan_requires_api_key_when_configured(self, client: TestClient):
        resp = client.post("/api/scan?async_mode=false", json={})
        assert resp.status_code == 401

    def test_scan_status_idle_initially(self, client: TestClient):
        resp = client.get("/api/scan/status")
        data = resp.json()
        assert data["ok"] is True

    def test_scan_lifecycle_idle_initially(self, client: TestClient):
        resp = client.get("/api/scan-lifecycle")
        data = resp.json()
        assert data["ok"] is True
        lifecycle = data["data"]
        assert lifecycle["mode"] == "local"
        assert lifecycle["transport"] == "local_thread"
        assert lifecycle["status"] in {"idle", "running", "completed", "failed"}
        assert lifecycle["task_id"] is None


class TestPendingTradeFlow:
    @patch("webapp.main.get_current_quote", return_value=FAKE_QUOTE)
    @patch("webapp.main.extract_schwab_last_price", return_value=186.0)
    @patch("webapp.main.get_position_size_usd", return_value=5000.0)
    @patch("webapp.main.DualSchwabAuth")
    def test_create_pending_trade(
        self, mock_auth, mock_size, mock_price, mock_quote, client: TestClient
    ):
        resp = client.post(
            "/api/pending-trades",
            json={"ticker": "AAPL", "signal": FAKE_SIGNALS[0]},
            headers=_auth_headers(),
        )
        data = resp.json()
        assert data["ok"] is True
        trade = data["data"]
        assert trade["ticker"] == "AAPL"
        assert trade["status"] == "pending"
        assert trade["qty"] > 0
        assert trade["price"] == 186.0

    def test_create_trade_requires_api_key(self, client: TestClient):
        resp = client.post(
            "/api/pending-trades",
            json={"ticker": "AAPL"},
        )
        assert resp.status_code == 401

    @patch("webapp.main.get_current_quote", return_value=FAKE_QUOTE)
    @patch("webapp.main.extract_schwab_last_price", return_value=186.0)
    @patch("webapp.main.get_position_size_usd", return_value=5000.0)
    @patch("webapp.main.DualSchwabAuth")
    def test_list_pending_trades(
        self, mock_auth, mock_size, mock_price, mock_quote, client: TestClient
    ):
        client.post(
            "/api/pending-trades",
            json={"ticker": "AAPL", "signal": FAKE_SIGNALS[0]},
            headers=_auth_headers(),
        )
        resp = client.get("/api/pending-trades")
        data = resp.json()
        assert data["ok"] is True
        assert len(data["data"]) >= 1
        assert data["data"][0]["ticker"] == "AAPL"

    @patch("webapp.main.get_current_quote", return_value=FAKE_QUOTE)
    @patch("webapp.main.extract_schwab_last_price", return_value=186.0)
    @patch("webapp.main.get_position_size_usd", return_value=5000.0)
    @patch("webapp.main.DualSchwabAuth")
    def test_preflight_trade(
        self, mock_auth, mock_size, mock_price, mock_quote, client: TestClient
    ):
        create_resp = client.post(
            "/api/pending-trades",
            json={"ticker": "AAPL", "signal": FAKE_SIGNALS[0]},
            headers=_auth_headers(),
        )
        trade_id = create_resp.json()["data"]["id"]
        resp = client.get(f"/api/trades/{trade_id}/preflight")
        data = resp.json()
        assert data["ok"] is True
        assert "checklist" in data["data"]
        checklist = data["data"]["checklist"]
        assert "blocked" in checklist
        assert "max_daily_trades" in checklist

    @patch("webapp.main.get_current_quote", return_value=FAKE_QUOTE)
    @patch("webapp.main.extract_schwab_last_price", return_value=186.0)
    @patch("webapp.main.get_position_size_usd", return_value=5000.0)
    @patch("webapp.main.DualSchwabAuth")
    def test_reject_trade(
        self, mock_auth, mock_size, mock_price, mock_quote, client: TestClient
    ):
        create_resp = client.post(
            "/api/pending-trades",
            json={"ticker": "AAPL", "signal": FAKE_SIGNALS[0]},
            headers=_auth_headers(),
        )
        trade_id = create_resp.json()["data"]["id"]
        resp = client.post(
            f"/api/trades/{trade_id}/reject",
            headers=_auth_headers(),
        )
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["status"] == "rejected"

    @patch("webapp.main.get_current_quote", return_value=FAKE_QUOTE)
    @patch("webapp.main.extract_schwab_last_price", return_value=186.0)
    @patch("webapp.main.get_position_size_usd", return_value=5000.0)
    @patch("webapp.main.DualSchwabAuth")
    def test_reject_already_rejected_fails(
        self, mock_auth, mock_size, mock_price, mock_quote, client: TestClient
    ):
        create_resp = client.post(
            "/api/pending-trades",
            json={"ticker": "AAPL", "signal": FAKE_SIGNALS[0]},
            headers=_auth_headers(),
        )
        trade_id = create_resp.json()["data"]["id"]
        client.post(f"/api/trades/{trade_id}/reject", headers=_auth_headers())
        resp = client.post(f"/api/trades/{trade_id}/reject", headers=_auth_headers())
        data = resp.json()
        assert data["ok"] is False
        assert "already" in data["error"].lower()

    @patch("webapp.main.place_order")
    @patch("webapp.main.get_current_quote", return_value=FAKE_QUOTE)
    @patch("webapp.main.extract_schwab_last_price", return_value=186.0)
    @patch("webapp.main.get_position_size_usd", return_value=5000.0)
    @patch("webapp.main.DualSchwabAuth")
    def test_approve_trade_success(
        self, mock_auth, mock_size, mock_price, mock_quote, mock_place, client: TestClient
    ):
        mock_place.return_value = {"order_id": "ORD123", "status": "FILLED"}
        create_resp = client.post(
            "/api/pending-trades",
            json={"ticker": "AAPL", "signal": FAKE_SIGNALS[0]},
            headers=_auth_headers(),
        )
        trade_id = create_resp.json()["data"]["id"]
        resp = client.post(
            f"/api/trades/{trade_id}/approve?confirm_live=true",
            json={"typed_ticker": "AAPL"},
            headers=_auth_headers(),
        )
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["trade"]["status"] == "executed"
        assert data["data"]["result"]["order_id"] == "ORD123"

    @patch("webapp.main.get_current_quote", return_value=FAKE_QUOTE)
    @patch("webapp.main.extract_schwab_last_price", return_value=186.0)
    @patch("webapp.main.get_position_size_usd", return_value=5000.0)
    @patch("webapp.main.DualSchwabAuth")
    def test_approve_wrong_ticker_fails(
        self, mock_auth, mock_size, mock_price, mock_quote, client: TestClient
    ):
        create_resp = client.post(
            "/api/pending-trades",
            json={"ticker": "AAPL", "signal": FAKE_SIGNALS[0]},
            headers=_auth_headers(),
        )
        trade_id = create_resp.json()["data"]["id"]
        resp = client.post(
            f"/api/trades/{trade_id}/approve?confirm_live=true",
            json={"typed_ticker": "MSFT"},
            headers=_auth_headers(),
        )
        data = resp.json()
        assert data["ok"] is False
        assert "typed_ticker" in data["error"].lower()

    @patch("webapp.main.place_order", return_value="Schwab auth failed: token expired")
    @patch("webapp.main.get_current_quote", return_value=FAKE_QUOTE)
    @patch("webapp.main.extract_schwab_last_price", return_value=186.0)
    @patch("webapp.main.get_position_size_usd", return_value=5000.0)
    @patch("webapp.main.DualSchwabAuth")
    def test_approve_execution_failure(
        self, mock_auth, mock_size, mock_price, mock_quote, mock_place, client: TestClient
    ):
        create_resp = client.post(
            "/api/pending-trades",
            json={"ticker": "AAPL", "signal": FAKE_SIGNALS[0]},
            headers=_auth_headers(),
        )
        trade_id = create_resp.json()["data"]["id"]
        resp = client.post(
            f"/api/trades/{trade_id}/approve?confirm_live=true",
            json={"typed_ticker": "AAPL"},
            headers=_auth_headers(),
        )
        data = resp.json()
        assert data["ok"] is False
        assert "recovery" in data["data"]

        trades = client.get("/api/pending-trades").json()["data"]
        matched = [t for t in trades if t["id"] == trade_id]
        assert matched[0]["status"] == "failed"

    @patch("webapp.main.place_order")
    @patch("webapp.main.get_current_quote", return_value=FAKE_QUOTE)
    @patch("webapp.main.extract_schwab_last_price", return_value=186.0)
    @patch("webapp.main.get_position_size_usd", return_value=5000.0)
    @patch("webapp.main.DualSchwabAuth")
    def test_approve_already_executed_fails(
        self, mock_auth, mock_size, mock_price, mock_quote, mock_place, client: TestClient
    ):
        mock_place.return_value = {"order_id": "ORD123", "status": "FILLED"}
        create_resp = client.post(
            "/api/pending-trades",
            json={"ticker": "AAPL", "signal": FAKE_SIGNALS[0]},
            headers=_auth_headers(),
        )
        trade_id = create_resp.json()["data"]["id"]
        client.post(
            f"/api/trades/{trade_id}/approve?confirm_live=true",
            json={"typed_ticker": "AAPL"},
            headers=_auth_headers(),
        )
        resp = client.post(
            f"/api/trades/{trade_id}/approve?confirm_live=true",
            json={"typed_ticker": "AAPL"},
            headers=_auth_headers(),
        )
        data = resp.json()
        assert data["ok"] is False
        assert "already" in data["error"].lower()


class TestKillSwitch:
    @patch("webapp.main.get_current_quote", return_value=FAKE_QUOTE)
    @patch("webapp.main.extract_schwab_last_price", return_value=186.0)
    @patch("webapp.main.get_position_size_usd", return_value=5000.0)
    @patch("webapp.main.DualSchwabAuth")
    def test_kill_switch_blocks_approve(
        self, mock_auth, mock_size, mock_price, mock_quote, client: TestClient, monkeypatch
    ):
        monkeypatch.setenv("LIVE_TRADING_KILL_SWITCH", "true")
        create_resp = client.post(
            "/api/pending-trades",
            json={"ticker": "AAPL", "signal": FAKE_SIGNALS[0]},
            headers=_auth_headers(),
        )
        trade_id = create_resp.json()["data"]["id"]
        resp = client.post(
            f"/api/trades/{trade_id}/approve?confirm_live=true",
            json={"typed_ticker": "AAPL"},
            headers=_auth_headers(),
        )
        data = resp.json()
        assert data["ok"] is False
        assert "kill switch" in data["error"].lower()


class TestDeleteOperations:
    @patch("webapp.main.get_current_quote", return_value=FAKE_QUOTE)
    @patch("webapp.main.extract_schwab_last_price", return_value=186.0)
    @patch("webapp.main.get_position_size_usd", return_value=5000.0)
    @patch("webapp.main.DualSchwabAuth")
    def test_delete_single_trade(
        self, mock_auth, mock_size, mock_price, mock_quote, client: TestClient
    ):
        create_resp = client.post(
            "/api/pending-trades",
            json={"ticker": "AAPL", "signal": FAKE_SIGNALS[0]},
            headers=_auth_headers(),
        )
        trade_id = create_resp.json()["data"]["id"]
        resp = client.post(
            f"/api/trades/{trade_id}/delete",
            headers=_auth_headers(),
        )
        assert resp.json()["ok"] is True
        trades = client.get("/api/pending-trades").json()["data"]
        assert all(t["id"] != trade_id for t in trades)

    def test_delete_requires_api_key(self, client: TestClient):
        resp = client.post("/api/trades/fake-id/delete")
        assert resp.status_code == 401

    @patch("webapp.main.get_current_quote", return_value=FAKE_QUOTE)
    @patch("webapp.main.extract_schwab_last_price", return_value=186.0)
    @patch("webapp.main.get_position_size_usd", return_value=5000.0)
    @patch("webapp.main.DualSchwabAuth")
    def test_delete_all_trades(
        self, mock_auth, mock_size, mock_price, mock_quote, client: TestClient
    ):
        for sig in FAKE_SIGNALS:
            client.post(
                "/api/pending-trades",
                json={"ticker": sig["ticker"], "signal": sig},
                headers=_auth_headers(),
            )
        resp = client.post(
            "/api/pending-trades/delete-all",
            headers=_auth_headers(),
        )
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["deleted"] >= 2
        trades = client.get("/api/pending-trades").json()["data"]
        assert len(trades) == 0


class TestHealthAndStatus:
    def test_health(self, client: TestClient):
        resp = client.get("/api/health")
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["status"] == "ok"

    def test_public_config(self, client: TestClient):
        resp = client.get("/api/public-config")
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["saas_mode"] is False
        assert data["data"]["api_key_required"] is True
        assert data["data"]["sse_enabled"] is True
        assert data["data"]["scan_transport"] == "local_thread"

    def test_static_pages(self, client: TestClient):
        for path in ["/", "/simple", "/login"]:
            resp = client.get(path)
            assert resp.status_code == 200


class TestLocalOAuthEndpoints:
    def test_local_authorize_url_returns_schwab_oauth_link(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SCHWAB_ACCOUNT_APP_KEY", "ak")
        monkeypatch.setenv("SCHWAB_ACCOUNT_APP_SECRET", "as")
        monkeypatch.setenv("SCHWAB_CALLBACK_URL", "http://testserver/api/oauth/schwab/callback")
        resp = client.get("/api/oauth/schwab/authorize-url")
        data = resp.json()
        assert data["ok"] is True
        url = (data.get("data") or {}).get("url") or ""
        assert "response_type=code" in url
        assert "client_id=ak" in url
        assert "state=" in url

    def test_local_authorize_url_rewrites_legacy_loopback_callback(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SCHWAB_ACCOUNT_APP_KEY", "ak")
        monkeypatch.setenv("SCHWAB_ACCOUNT_APP_SECRET", "as")
        monkeypatch.setenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182")
        resp = client.get("/api/oauth/schwab/authorize-url")
        data = resp.json()
        assert data["ok"] is True
        url = (data.get("data") or {}).get("url") or ""
        assert "redirect_uri=http%3A%2F%2Ftestserver%2Fapi%2Foauth%2Fschwab%2Fcallback" in url

    @patch("webapp.main.write_encrypted_token_file")
    @patch(
        "webapp.main.exchange_schwab_code_for_tokens",
        return_value={"access_token": "a", "refresh_token": "r", "token_type": "Bearer"},
    )
    def test_local_account_callback_writes_token_file(
        self,
        _exchange_mock,
        write_mock,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SCHWAB_ACCOUNT_APP_KEY", "ak")
        monkeypatch.setenv("SCHWAB_ACCOUNT_APP_SECRET", "as")
        monkeypatch.setenv("SCHWAB_CALLBACK_URL", "http://testserver/api/oauth/schwab/callback")
        auth_resp = client.get("/api/oauth/schwab/authorize-url").json()
        state = ((auth_resp.get("data") or {}).get("state") or "").strip()
        callback = client.get(
            "/api/oauth/schwab/callback",
            params={"code": "code123", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 302
        assert "schwab_oauth=ok" in (callback.headers.get("location") or "")
        write_mock.assert_called_once()
        out_path = str(write_mock.call_args.args[0]).replace("\\", "/")
        assert out_path.endswith("tokens_account.enc")
