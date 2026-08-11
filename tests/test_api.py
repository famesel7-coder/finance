from fastapi.testclient import TestClient

from app.api import app

client = TestClient(app)


def test_dashboard_and_health_are_available() -> None:
    dashboard = client.get("/")
    health = client.get("/health")

    assert dashboard.status_code == 200
    assert "Finance Radar" in dashboard.text
    assert health.json()["status"] == "ok"


def test_universe_contains_known_company() -> None:
    response = client.get("/universe")

    assert response.status_code == 200
    assert any(item["ticker"] == "SBER" for item in response.json()["companies"])
