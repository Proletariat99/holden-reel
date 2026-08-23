from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import Event, Lock
import time
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel

from .artifacts import ArtifactStore
from .db import Database
from .errors import DomainError
from .plans import PlanService, ReelPlan
from .renderer import FINAL, PREVIEW, RenderCancelled, RenderProfile, RenderResult


JobKind = Literal["preview", "final"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
_PROFILE_BY_NAME: dict[str, RenderProfile] = {"preview": PREVIEW, "final": FINAL}
_PROGRESS_WRITE_INTERVAL_SECONDS = 0.25


class JobError(BaseModel):
    code: str
    message: str


class Job(BaseModel):
    id: UUID
    project_id: UUID
    kind: JobKind
    status: JobStatus
    progress: float
    plan_id: UUID
    artifact_path: str | None
    error: JobError | None
    created_at: datetime
    updated_at: datetime


class RenderWorker(Protocol):
    def render(
        self,
        plan: ReelPlan,
        profile: RenderProfile,
        output_path: Path,
        on_progress: Callable[[float], None],
        is_cancelled: Callable[[], bool],
    ) -> RenderResult: ...


class JobRepository:
    def __init__(self, database: Database):
        self.database = database

    def insert(self, job: Job) -> Job:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                  id, project_id, kind, status, progress, plan_id,
                  artifact_path, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(job.id),
                    str(job.project_id),
                    job.kind,
                    job.status,
                    job.progress,
                    str(job.plan_id),
                    job.artifact_path,
                    _dump_error(job.error),
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                ),
            )
        return job

    def get(self, job_id: UUID) -> Job | None:
        row = self.database.fetch_one("SELECT * FROM jobs WHERE id = ?", (str(job_id),))
        return self._to_job(row) if row is not None else None

    def mark_running(self, job_id: UUID) -> Job | None:
        return self._transition(job_id, from_statuses=("queued",), status="running")

    def update_progress(self, job_id: UUID, progress: float) -> bool:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET progress = ?, updated_at = ?
                WHERE id = ? AND status = 'running' AND progress < ?
                """,
                (progress, now, str(job_id), progress),
            )
        return cursor.rowcount == 1

    def succeed(self, job_id: UUID, artifact_path: Path) -> Job | None:
        return self._transition(
            job_id,
            from_statuses=("running",),
            status="succeeded",
            progress=1.0,
            artifact_path=str(artifact_path),
        )

    def fail(self, job_id: UUID, error: JobError) -> Job | None:
        return self._transition(
            job_id,
            from_statuses=("queued", "running"),
            status="failed",
            artifact_path=None,
            error=error,
        )

    def cancel(self, job_id: UUID) -> Job | None:
        return self._transition(
            job_id,
            from_statuses=("queued", "running"),
            status="cancelled",
            artifact_path=None,
        )

    def cancel_active(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'cancelled', artifact_path = NULL, error = NULL,
                    updated_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (datetime.now(UTC).isoformat(),),
            )

    def recover_running(self) -> None:
        error = JobError(
            code="application_restarted",
            message="Render stopped because the application restarted",
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'failed', artifact_path = NULL, error = ?, updated_at = ?
                WHERE status = 'running'
                """,
                (_dump_error(error), datetime.now(UTC).isoformat()),
            )

    def _transition(
        self,
        job_id: UUID,
        *,
        from_statuses: tuple[JobStatus, ...],
        status: JobStatus,
        progress: float | None = None,
        artifact_path: str | None = None,
        error: JobError | None = None,
    ) -> Job | None:
        assignments = ["status = ?", "artifact_path = ?", "error = ?", "updated_at = ?"]
        parameters: list[object] = [
            status,
            artifact_path,
            _dump_error(error),
            datetime.now(UTC).isoformat(),
        ]
        if progress is not None:
            assignments.append("progress = ?")
            parameters.append(progress)
        placeholders = ", ".join("?" for _ in from_statuses)
        parameters.extend([str(job_id), *from_statuses])
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"""
                UPDATE jobs SET {", ".join(assignments)}
                WHERE id = ? AND status IN ({placeholders})
                """,
                parameters,
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
        if cursor.rowcount != 1 or row is None:
            return None
        return self._to_job(row)

    @staticmethod
    def _to_job(row) -> Job:
        error_payload = json.loads(row["error"]) if row["error"] is not None else None
        return Job(
            id=UUID(row["id"]),
            project_id=UUID(row["project_id"]),
            kind=row["kind"],
            status=row["status"],
            progress=row["progress"],
            plan_id=UUID(row["plan_id"]),
            artifact_path=row["artifact_path"],
            error=JobError.model_validate(error_payload) if error_payload else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class JobService:
    def __init__(
        self,
        database: Database,
        plans: PlanService,
        renderer: RenderWorker,
        artifacts: ArtifactStore,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.repository = JobRepository(database)
        self.plans = plans
        self.renderer = renderer
        self.artifacts = artifacts
        self.monotonic = monotonic
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="holden-reel-render"
        )
        self._events: dict[UUID, Event] = {}
        self._lock = Lock()
        self._closed = False
        self.repository.recover_running()

    def submit_render(self, plan_id: UUID, profile: str) -> Job:
        render_profile = _PROFILE_BY_NAME.get(profile)
        if render_profile is None:
            raise DomainError(
                "invalid_render_profile",
                "Render profile must be preview or final",
                status_code=422,
            )
        plan = self.plans.get(plan_id)
        now = datetime.now(UTC)
        job = Job(
            id=uuid4(),
            project_id=plan.project_id,
            kind=profile,
            status="queued",
            progress=0.0,
            plan_id=plan.id,
            artifact_path=None,
            error=None,
            created_at=now,
            updated_at=now,
        )
        event = Event()
        with self._lock:
            if self._closed:
                raise DomainError(
                    "job_service_closed",
                    "Render job service is shutting down",
                    status_code=503,
                )
            self.repository.insert(job)
            self._events[job.id] = event
            try:
                self._executor.submit(self._run, job.id, plan, render_profile, event)
            except BaseException:
                self._events.pop(job.id, None)
                self.repository.fail(
                    job.id,
                    JobError(
                        code="render_failed", message="Render could not be scheduled"
                    ),
                )
                raise
        return job

    def get(self, job_id: UUID) -> Job:
        job = self.repository.get(job_id)
        if job is None:
            raise DomainError("job_not_found", "Render job was not found", status_code=404)
        return job

    def cancel(self, job_id: UUID) -> Job:
        current = self.get(job_id)
        if current.status in _TERMINAL_STATUSES:
            return current
        with self._lock:
            event = self._events.get(job_id)
            if event is not None:
                event.set()
        return self.repository.cancel(job_id) or self.get(job_id)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            events = list(self._events.values())
            for event in events:
                event.set()
        self.repository.cancel_active()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(
        self,
        job_id: UUID,
        plan: ReelPlan,
        profile: RenderProfile,
        cancellation: Event,
    ) -> None:
        output_path: Path | None = None
        try:
            if self.repository.mark_running(job_id) is None:
                return
            output_path = self.artifacts.path_for(
                plan.project_id, profile.name, job_id, ".mp4"
            )
            last_progress_write = float("-inf")

            def persist_progress(progress: float) -> None:
                nonlocal last_progress_write
                progress = min(0.99, max(0.0, progress))
                now = self.monotonic()
                if (
                    progress <= 0.0
                    or now - last_progress_write < _PROGRESS_WRITE_INTERVAL_SECONDS
                ):
                    return
                if self.repository.update_progress(job_id, progress):
                    last_progress_write = now

            result = self.renderer.render(
                plan,
                profile,
                output_path,
                persist_progress,
                cancellation.is_set,
            )
            if result.path.resolve() != output_path.resolve():
                raise RuntimeError("Renderer returned an unexpected artifact path")
            if cancellation.is_set():
                output_path.unlink(missing_ok=True)
                self.repository.cancel(job_id)
                return
            if self.repository.succeed(job_id, output_path) is None:
                output_path.unlink(missing_ok=True)
        except RenderCancelled:
            if output_path is not None:
                output_path.unlink(missing_ok=True)
            self.repository.cancel(job_id)
        except Exception as error:
            if output_path is not None:
                output_path.unlink(missing_ok=True)
            self.repository.fail(
                job_id,
                JobError(code="render_failed", message=str(error) or type(error).__name__),
            )
        finally:
            with self._lock:
                if self._events.get(job_id) is cancellation:
                    self._events.pop(job_id, None)


def _dump_error(error: JobError | None) -> str | None:
    return error.model_dump_json() if error is not None else None
