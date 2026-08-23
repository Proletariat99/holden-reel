from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from holden_reel.config import Settings
from holden_reel.main import create_app

from fixture_media import FixtureMedia, generate_fixture_media


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(data_dir=tmp_path))) as test_client:
        yield test_client


@pytest.fixture
def media_fixture(tmp_path) -> FixtureMedia:
    root = tmp_path / "source-media"
    paths = generate_fixture_media(root)
    (root / "unsupported.txt").write_text("not media")
    return FixtureMedia(root=root.resolve(), paths=paths)
