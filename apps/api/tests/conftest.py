from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from holden_reel.config import Settings
from holden_reel.main import create_app


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(data_dir=tmp_path))) as test_client:
        yield test_client
