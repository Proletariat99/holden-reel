import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient

from holden_reel.config import Settings
from holden_reel.main import create_app


def test_health_reports_ready(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "0.1.0"


def test_importing_composition_module_has_no_filesystem_side_effects(tmp_path):
    """Would fail if importing the ASGI module initialized the production database."""
    env = os.environ | {
        "HOLDEN_REEL_DATA_DIR": str(tmp_path),
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    subprocess.run(
        [sys.executable, "-c", "import holden_reel.main"],
        check=True,
        cwd=Path(__file__).parents[1],
        env=env,
        timeout=10,
    )
    assert list(tmp_path.rglob("*")) == []
