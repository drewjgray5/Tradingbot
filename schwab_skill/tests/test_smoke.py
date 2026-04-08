from pathlib import Path

from fastapi.testclient import TestClient

from webapp.main import app


def test_health_endpoint_contract() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload.get("ok") is True
    assert (payload.get("data") or {}).get("status") == "ok"


def test_env_example_exists() -> None:
    assert Path(".env.example").exists()
