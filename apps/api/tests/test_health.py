from fastapi.testclient import TestClient

from holden_reel.config import Settings
from holden_reel.main import create_app


def test_health_reports_ready(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "0.1.0"
