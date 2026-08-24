from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import shutil
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from holden_reel.config import Settings
from holden_reel.db import Database, open_database
from holden_reel.focus import FocusResult
from holden_reel.main import create_app
from holden_reel.media import MediaRepository, MediaService, ProbeResult
from holden_reel.projects import ProjectRepository, ProjectService

from fixture_media import FixtureMedia, generate_fixture_media


class RecordingFocusAnalyzer:
    def __init__(self, result: FocusResult | None = None):
        self.result = result or FocusResult(0.25, 0.75, 0.8, "face")
        self.calls: list[tuple[Path, str]] = []

    def analyze(self, path: Path, kind: str) -> FocusResult:
        self.calls.append((path, kind))
        return self.result


class DeterministicFFprobe:
    def probe(self, path: Path) -> ProbeResult:
        if path.suffix.casefold() in {".wav", ".mp3", ".m4a", ".aac"}:
            return ProbeResult("audio", 18_000, None, None, "pcm_s16le", True, 18_000)
        if path.suffix.casefold() in {".jpg", ".jpeg", ".png"}:
            return ProbeResult("image", None, 320, 240, "mjpeg")
        return ProbeResult("video", 4_000, 320, 240, "h264")


@dataclass(frozen=True)
class MediaServiceHarness:
    service: MediaService
    repository: MediaRepository
    database: Database
    project_id: UUID
    analyzer: RecordingFocusAnalyzer


@pytest.fixture
def focus_analyzer() -> RecordingFocusAnalyzer:
    return RecordingFocusAnalyzer()


@pytest.fixture
def media_service_harness(tmp_path, focus_analyzer) -> Iterator[MediaServiceHarness]:
    connection = open_database(tmp_path / "catalog.sqlite3")
    database = Database(connection)
    projects = ProjectService(ProjectRepository(database))
    project = projects.create("Focus fixture")
    repository = MediaRepository(database)
    service = MediaService(repository, projects, DeterministicFFprobe(), focus_analyzer)
    yield MediaServiceHarness(service, repository, database, project.id, focus_analyzer)
    connection.close()


@pytest.fixture(scope="session")
def ffmpeg_bins() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and FFprobe are required for renderer tests")
    return ffmpeg, ffprobe


@pytest.fixture
def client(tmp_path, focus_analyzer) -> Iterator[TestClient]:
    app = create_app(Settings(data_dir=tmp_path))
    app.state.media_service.focus_analyzer = focus_analyzer
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def media_fixture(tmp_path) -> FixtureMedia:
    root = tmp_path / "source-media"
    paths = generate_fixture_media(root)
    (root / "unsupported.txt").write_text("not media")
    return FixtureMedia(root=root.resolve(), paths=paths)
