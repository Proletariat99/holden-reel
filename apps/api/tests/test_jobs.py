from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
import sqlite3
import time
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from holden_reel.artifacts import ArtifactStore
from holden_reel.config import Settings
from holden_reel.db import Database, open_database
from holden_reel.jobs import JobService
from holden_reel.main import create_app
from holden_reel.media import FFprobe, MediaRepository, MediaService
from holden_reel.plans import (
    AudioBed,
    PlanRepository,
    PlanService,
    ReelPlan,
    Shot,
)
from holden_reel.projects import ProjectRepository, ProjectService
from holden_reel.renderer import FINAL, PREVIEW, RenderCancelled, RenderResult, Renderer


WAIT_SECONDS = 3.0


@dataclass
class ServiceHarness:
    service: JobService
    database: Database
    connection: sqlite3.Connection
    plan: ReelPlan
    data_dir: Path


class BlockingRenderer:
    """A controllable replacement for only the slow FFmpeg operation."""

    def __init__(self):
        self.started = Event()
        self.release = Event()
        self.stopped = Event()
        self._lock = Lock()
        self.calls = 0
        self.profiles = []

    def render(
        self, plan, profile, output_path, on_progress, is_cancelled
    ) -> RenderResult:
        if profile is not PREVIEW and profile is not FINAL:
            raise AssertionError("job service passed a non-canonical render profile")
        with self._lock:
            self.calls += 1
            self.profiles.append(profile)
        self.started.set()
        on_progress(0.2)
        deadline = time.monotonic() + WAIT_SECONDS
        try:
            while not self.release.wait(0.01):
                if is_cancelled():
                    raise RenderCancelled("cancelled by job service")
                if time.monotonic() >= deadline:
                    raise AssertionError("test did not release the fake renderer")
            if is_cancelled():
                raise RenderCancelled("cancelled by job service")
            output_path.write_bytes(b"fake mp4")
            on_progress(0.9)
            return _render_result(output_path)
        finally:
            self.stopped.set()


class FailingRenderer:
    def render(self, plan, profile, output_path, on_progress, is_cancelled):
        output_path.write_bytes(b"unpublishable output")
        raise RuntimeError("fixture renderer exploded")


class ImmediateRenderer:
    def render(
        self, plan, profile, output_path, on_progress, is_cancelled
    ) -> RenderResult:
        if profile is not PREVIEW and profile is not FINAL:
            raise AssertionError("job service passed a non-canonical render profile")
        on_progress(0.5)
        output_path.write_bytes(b"fake mp4")
        return _render_result(output_path)


class ManualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ProgressRenderer:
    def __init__(self, clock: ManualClock):
        self.clock = clock

    def render(
        self, plan, profile, output_path, on_progress, is_cancelled
    ) -> RenderResult:
        for timestamp, progress in [
            (0.0, 0.1),
            (0.1, 0.2),
            (0.24, 0.3),
            (0.25, 0.4),
            (0.49, 0.5),
            (0.5, 0.6),
        ]:
            self.clock.now = timestamp
            on_progress(progress)
        output_path.write_bytes(b"fake mp4")
        return _render_result(output_path)


def _render_result(output_path: Path) -> RenderResult:
    return RenderResult(
        path=output_path,
        width=540,
        height=960,
        video_codec="h264",
        audio_codec="aac",
        duration_ms=15_000,
        size_bytes=output_path.stat().st_size,
    )


def _make_harness(
    tmp_path: Path,
    renderer,
    *,
    monotonic=time.monotonic,
) -> ServiceHarness:
    data_dir = tmp_path / "data"
    connection = open_database(data_dir / "holden-reel.sqlite3")
    database = Database(connection)
    projects = ProjectService(ProjectRepository(database))
    project = projects.create("Job fixture")
    media = MediaService(
        MediaRepository(database), projects, FFprobe("unused-ffprobe")
    )
    plans = PlanService(PlanRepository(database), projects, media)
    plan = PlanRepository(database).insert(
        ReelPlan(
            id=uuid4(),
            project_id=project.id,
            version=0,
            duration_ms=15_000,
            audio=AudioBed(
                asset_id=uuid4(), source_start_ms=0, source_end_ms=15_000
            ),
            shots=[
                Shot(
                    asset_id=uuid4(),
                    source_start_ms=0,
                    source_end_ms=15_000,
                    output_start_ms=0,
                    output_end_ms=15_000,
                )
            ],
            rationale="Job test fixture.",
        )
    )
    service = JobService(
        database,
        plans,
        renderer,
        ArtifactStore(data_dir),
        monotonic=monotonic,
    )
    return ServiceHarness(service, database, connection, plan, data_dir)


def _wait_for_job(service: JobService, job_id: UUID, predicate, timeout=WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = service.get(job_id)
        if predicate(job):
            return job
        time.sleep(0.01)
    pytest.fail(f"job {job_id} did not reach expected state within {timeout} seconds")


def _wait_for_terminal_job(service: JobService, job_id: UUID, timeout=WAIT_SECONDS):
    return _wait_for_job(
        service,
        job_id,
        lambda job: job.status in {"succeeded", "failed", "cancelled"},
        timeout,
    )


def _close_harness(harness: ServiceHarness) -> None:
    harness.service.close()
    harness.connection.close()


def test_render_job_persists_queued_running_and_succeeded(tmp_path):
    """Would fail if a worker skipped or failed to persist a lifecycle state."""
    renderer = BlockingRenderer()
    harness = _make_harness(tmp_path, renderer)
    try:
        first = harness.service.submit_render(harness.plan.id, profile="preview")
        assert renderer.started.wait(WAIT_SECONDS), "worker did not start fake render"

        second = harness.service.submit_render(harness.plan.id, profile="final")
        assert harness.service.get(first.id).status == "running"
        assert harness.service.get(second.id).status == "queued"

        renderer.release.set()
        first_finished = _wait_for_terminal_job(harness.service, first.id)
        second_finished = _wait_for_terminal_job(harness.service, second.id)

        assert first_finished.status == "succeeded"
        assert second_finished.status == "succeeded"
        assert first_finished.progress == second_finished.progress == 1.0
        assert first_finished.kind == "preview"
        assert second_finished.kind == "final"
        assert renderer.profiles[0] is PREVIEW
        assert renderer.profiles[1] is FINAL
        assert first_finished.artifact_path.endswith(".mp4")
        assert Path(first_finished.artifact_path).is_file()
        assert Path(second_finished.artifact_path).is_file()
        assert Path(first_finished.artifact_path).is_relative_to(tmp_path)
        assert Path(second_finished.artifact_path).is_relative_to(tmp_path)
    finally:
        renderer.release.set()
        _close_harness(harness)


def test_renderer_exception_persists_failed_error_and_removes_artifact(tmp_path):
    """Would fail if worker errors escaped without a durable terminal record."""
    harness = _make_harness(tmp_path, FailingRenderer())
    try:
        job = harness.service.submit_render(harness.plan.id, profile="preview")
        finished = _wait_for_terminal_job(harness.service, job.id)

        assert finished.status == "failed"
        assert finished.progress < 1.0
        assert finished.artifact_path is None
        assert finished.error is not None
        assert finished.error.code == "render_failed"
        assert "fixture renderer exploded" in finished.error.message
        assert list(harness.data_dir.rglob("*.mp4")) == []
    finally:
        _close_harness(harness)


def test_progress_persistence_is_throttled_to_four_writes_per_second(tmp_path):
    """Would fail if every renderer callback wrote to SQLite."""
    clock = ManualClock()
    harness = _make_harness(tmp_path, ProgressRenderer(clock), monotonic=clock)
    try:
        with harness.database.transaction() as connection:
            connection.execute("CREATE TABLE progress_audit (value REAL NOT NULL)")
            connection.execute(
                """
                CREATE TRIGGER audit_job_progress
                AFTER UPDATE OF progress ON jobs
                WHEN NEW.progress != OLD.progress
                BEGIN
                  INSERT INTO progress_audit(value) VALUES (NEW.progress);
                END
                """
            )

        job = harness.service.submit_render(harness.plan.id, profile="preview")
        finished = _wait_for_terminal_job(harness.service, job.id)
        writes = [
            row["value"]
            for row in harness.database.fetch_all(
                "SELECT value FROM progress_audit ORDER BY rowid"
            )
        ]

        assert finished.status == "succeeded"
        assert writes == [0.1, 0.4, 0.6, 1.0]
    finally:
        _close_harness(harness)


def test_cancel_queued_job_never_invokes_renderer(tmp_path):
    """Would fail if a cancelled queued future later started rendering."""
    renderer = BlockingRenderer()
    harness = _make_harness(tmp_path, renderer)
    try:
        running = harness.service.submit_render(harness.plan.id, profile="preview")
        assert renderer.started.wait(WAIT_SECONDS), "first render did not start"
        queued = harness.service.submit_render(harness.plan.id, profile="preview")
        assert harness.service.get(queued.id).status == "queued"

        cancelled = harness.service.cancel(queued.id)
        renderer.release.set()
        _wait_for_terminal_job(harness.service, running.id)
        persisted = _wait_for_terminal_job(harness.service, queued.id)

        assert cancelled.status == persisted.status == "cancelled"
        assert persisted.artifact_path is None
        assert renderer.calls == 1
    finally:
        renderer.release.set()
        _close_harness(harness)


def test_cancel_running_job_signals_renderer_and_persists_cancelled(tmp_path):
    """Would fail if cancellation changed the row but left rendering alive."""
    renderer = BlockingRenderer()
    harness = _make_harness(tmp_path, renderer)
    try:
        job = harness.service.submit_render(harness.plan.id, profile="preview")
        assert renderer.started.wait(WAIT_SECONDS), "render did not start"

        cancelled = harness.service.cancel(job.id)
        assert renderer.stopped.wait(WAIT_SECONDS), "renderer did not receive cancellation"
        persisted = _wait_for_terminal_job(harness.service, job.id)

        assert cancelled.status == persisted.status == "cancelled"
        assert persisted.artifact_path is None
        assert list(harness.data_dir.rglob("*.mp4")) == []
    finally:
        renderer.release.set()
        _close_harness(harness)


def test_cancel_terminal_job_is_idempotent(tmp_path):
    """Would fail if cancellation overwrote a completed terminal result."""
    harness = _make_harness(tmp_path, ImmediateRenderer())
    try:
        job = harness.service.submit_render(harness.plan.id, profile="preview")
        finished = _wait_for_terminal_job(harness.service, job.id)

        first_cancel = harness.service.cancel(job.id)
        second_cancel = harness.service.cancel(job.id)

        assert first_cancel == second_cancel == finished
        assert Path(finished.artifact_path).is_file()
    finally:
        _close_harness(harness)


def test_service_close_cancels_active_work_without_writing_an_artifact(tmp_path):
    """Would fail if application shutdown stranded an active renderer or output."""
    renderer = BlockingRenderer()
    harness = _make_harness(tmp_path, renderer)
    try:
        job = harness.service.submit_render(harness.plan.id, profile="preview")
        assert renderer.started.wait(WAIT_SECONDS), "render did not start"

        harness.service.close()

        assert renderer.stopped.wait(WAIT_SECONDS), "shutdown did not stop renderer"
        assert harness.service.get(job.id).status == "cancelled"
        assert list(harness.data_dir.rglob("*.mp4")) == []
    finally:
        renderer.release.set()
        harness.service.close()
        harness.connection.close()


def test_startup_recovery_fails_stale_running_job(tmp_path):
    """Would fail if a process restart left an impossible running state."""
    initial = _make_harness(tmp_path, ImmediateRenderer())
    stale_id = uuid4()
    try:
        initial.service.close()
        with initial.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                  id, project_id, kind, status, progress, plan_id,
                  artifact_path, error, created_at, updated_at
                ) VALUES (?, ?, 'preview', 'running', 0.4, ?, NULL, NULL,
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (str(stale_id), str(initial.plan.project_id), str(initial.plan.id)),
            )

        recovered = JobService(
            initial.database,
            PlanService(
                PlanRepository(initial.database),
                ProjectService(ProjectRepository(initial.database)),
                MediaService(
                    MediaRepository(initial.database),
                    ProjectService(ProjectRepository(initial.database)),
                    FFprobe("unused-ffprobe"),
                ),
            ),
            ImmediateRenderer(),
            ArtifactStore(initial.data_dir),
        )
        try:
            job = recovered.get(stale_id)
            assert job.status == "failed"
            assert job.progress == 0.4
            assert job.error is not None
            assert job.error.code == "application_restarted"
        finally:
            recovered.close()
    finally:
        initial.service.close()
        initial.connection.close()


def test_startup_recovery_removes_only_expected_stale_render_outputs(tmp_path):
    """Would fail if recovery left crash outputs or trusted arbitrary stored paths."""
    initial = _make_harness(tmp_path, ImmediateRenderer())
    published_job_id = uuid4()
    partial_job_id = uuid4()
    published_output = (
        initial.data_dir
        / "projects"
        / str(initial.plan.project_id)
        / "previews"
        / f"{published_job_id}.mp4"
    )
    intended_partial_output = (
        initial.data_dir
        / "projects"
        / str(initial.plan.project_id)
        / "exports"
        / f"{partial_job_id}.mp4"
    )
    partial_output = Path(f"{intended_partial_output}.partial.mp4")
    source_sentinel = initial.data_dir / "source-media" / "original.mp4"
    external_sentinel = tmp_path / "external-sentinel.mp4"
    try:
        initial.service.close()
        published_output.parent.mkdir(parents=True)
        intended_partial_output.parent.mkdir(parents=True)
        source_sentinel.parent.mkdir(parents=True)
        published_output.write_bytes(b"published before database transition")
        partial_output.write_bytes(b"partial before atomic publication")
        source_sentinel.write_bytes(b"original source")
        external_sentinel.write_bytes(b"external file")
        with initial.database.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO jobs (
                  id, project_id, kind, status, progress, plan_id,
                  artifact_path, error, created_at, updated_at
                ) VALUES (?, ?, ?, 'running', 0.4, ?, ?, NULL,
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                [
                    (
                        str(published_job_id),
                        str(initial.plan.project_id),
                        "preview",
                        str(initial.plan.id),
                        str(source_sentinel),
                    ),
                    (
                        str(partial_job_id),
                        str(initial.plan.project_id),
                        "final",
                        str(initial.plan.id),
                        str(external_sentinel),
                    ),
                ],
            )

        recovered = JobService(
            initial.database,
            PlanService(
                PlanRepository(initial.database),
                ProjectService(ProjectRepository(initial.database)),
                MediaService(
                    MediaRepository(initial.database),
                    ProjectService(ProjectRepository(initial.database)),
                    FFprobe("unused-ffprobe"),
                ),
            ),
            ImmediateRenderer(),
            ArtifactStore(initial.data_dir),
        )
        try:
            published_job = recovered.get(published_job_id)
            partial_job = recovered.get(partial_job_id)

            assert not published_output.exists()
            assert not partial_output.exists()
            assert source_sentinel.read_bytes() == b"original source"
            assert external_sentinel.read_bytes() == b"external file"
            assert published_job.status == partial_job.status == "failed"
            assert published_job.artifact_path is partial_job.artifact_path is None
            assert published_job.error is not None
            assert partial_job.error is not None
            assert published_job.error.code == "application_restarted"
            assert partial_job.error.code == "application_restarted"
        finally:
            recovered.close()
    finally:
        initial.service.close()
        initial.connection.close()


def test_job_routes_submit_poll_cancel_and_preserve_error_envelope(tmp_path):
    """Would fail if HTTP job contracts diverged from the persisted service."""
    renderer = BlockingRenderer()
    app = create_app(Settings(data_dir=tmp_path / "data"), renderer=renderer)
    try:
        project = app.state.project_service.create("HTTP job")
        plan = PlanRepository(app.state.database).insert(
            ReelPlan(
                id=uuid4(),
                project_id=project.id,
                version=0,
                duration_ms=15_000,
                audio=AudioBed(
                    asset_id=uuid4(), source_start_ms=0, source_end_ms=15_000
                ),
                shots=[
                    Shot(
                        asset_id=uuid4(),
                        source_start_ms=0,
                        source_end_ms=15_000,
                        output_start_ms=0,
                        output_end_ms=15_000,
                    )
                ],
                rationale="HTTP fixture.",
            )
        )
        with TestClient(app) as client:
            response = client.post(
                f"/api/plans/{plan.id}/renders", json={"profile": "preview"}
            )
            assert response.status_code == 202
            assert response.headers["location"] == f"/api/jobs/{response.json()['id']}"
            assert renderer.started.wait(WAIT_SECONDS), "HTTP render did not start"

            polled = client.get(response.headers["location"])
            cancelled = client.post(f"{response.headers['location']}/cancel")
            missing = client.get(f"/api/jobs/{uuid4()}")
            invalid = client.post(
                f"/api/plans/{plan.id}/renders", json={"profile": "custom"}
            )

            assert polled.status_code == 200
            assert polled.json()["status"] == "running"
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "job_not_found"
            assert invalid.status_code == 422
            assert invalid.json()["error"]["code"] == "invalid_request"
    finally:
        renderer.release.set()
        app.state.job_service.close()


def test_real_preview_job_through_http_is_verified_inside_temporary_data(
    tmp_path, media_fixture, ffmpeg_bins
):
    """Would fail if HTTP wiring, real rendering, or artifact containment broke."""
    ffmpeg, ffprobe = ffmpeg_bins
    data_dir = tmp_path / "data"
    app = create_app(
        Settings(data_dir=data_dir, ffmpeg_bin=ffmpeg, ffprobe_bin=ffprobe)
    )
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "Real preview"}).json()
        assets = client.post(
            f"/api/projects/{project['id']}/media/import",
            json={"path": str(media_fixture.root)},
        ).json()["assets"]
        by_name = {Path(asset["path"]).name: asset for asset in assets}
        plan = client.post(
            f"/api/projects/{project['id']}/plans/compose",
            json={
                "duration_ms": 15_000,
                "audio_asset_id": by_name["song.wav"]["id"],
                "audio_start_ms": 0,
                "visual_asset_ids": [
                    by_name["red.mp4"]["id"],
                    by_name["blue.mp4"]["id"],
                ],
            },
        ).json()

        submitted = client.post(
            f"/api/plans/{plan['id']}/renders", json={"profile": "preview"}
        )
        assert submitted.status_code == 202
        job_url = submitted.headers["location"]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = client.get(job_url).json()
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        else:
            pytest.fail("real preview job did not finish within 120 seconds")

        assert job["status"] == "succeeded", job
        artifact = Path(job["artifact_path"])
        assert artifact.is_file()
        assert artifact.is_relative_to(tmp_path)
        app.state.renderer.verify(artifact, 15_000, PREVIEW)
        assert list(tmp_path.rglob("*.partial.mp4")) == []
