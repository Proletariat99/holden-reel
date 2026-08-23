from collections.abc import Iterator
import os
from pathlib import Path
import stat
from typing import BinaryIO, Literal
from uuid import UUID

from fastapi import APIRouter, FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import DomainError
from .jobs import Job, JobService
from .media import MediaAsset, MediaService
from .plans import ComposePlanRequest, PlanService, ReelPlan
from .projects import Project, ProjectService


class CreateProjectRequest(BaseModel):
    name: str


class ImportMediaRequest(BaseModel):
    path: str


class SubmitRenderRequest(BaseModel):
    profile: Literal["preview", "final"]


api_router = APIRouter(prefix="/api")


def project_service(request: Request) -> ProjectService:
    return request.app.state.project_service


def media_service(request: Request) -> MediaService:
    return request.app.state.media_service


def plan_service(request: Request) -> PlanService:
    return request.app.state.plan_service


def job_service(request: Request) -> JobService:
    return request.app.state.job_service


@api_router.post("/projects", response_model=Project, status_code=status.HTTP_201_CREATED)
def create_project(payload: CreateProjectRequest, request: Request) -> Project:
    return project_service(request).create(payload.name)


@api_router.get("/projects", response_model=list[Project])
def list_projects(request: Request) -> list[Project]:
    return project_service(request).list()


@api_router.get("/projects/{project_id}", response_model=Project)
def get_project(project_id: UUID, request: Request) -> Project:
    return project_service(request).get(project_id)


@api_router.post(
    "/projects/{project_id}/media/import",
    response_model=dict[str, list[MediaAsset]],
    status_code=status.HTTP_201_CREATED,
)
def import_media(
    project_id: UUID, payload: ImportMediaRequest, request: Request
) -> dict[str, list[MediaAsset]]:
    return {"assets": media_service(request).import_path(project_id, Path(payload.path))}


@api_router.get(
    "/projects/{project_id}/media", response_model=dict[str, list[MediaAsset]]
)
def list_media(project_id: UUID, request: Request) -> dict[str, list[MediaAsset]]:
    return {"assets": media_service(request).list(project_id)}


@api_router.post(
    "/projects/{project_id}/plans/compose",
    response_model=ReelPlan,
    status_code=status.HTTP_201_CREATED,
)
def compose_plan(
    project_id: UUID, payload: ComposePlanRequest, request: Request
) -> ReelPlan:
    return plan_service(request).compose(project_id, payload)


@api_router.get("/plans/{plan_id}", response_model=ReelPlan)
def get_plan(plan_id: UUID, request: Request) -> ReelPlan:
    return plan_service(request).get(plan_id)


@api_router.post(
    "/plans/{plan_id}/renders",
    response_model=Job,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_render(
    plan_id: UUID,
    payload: SubmitRenderRequest,
    request: Request,
    response: Response,
) -> Job:
    job = job_service(request).submit_render(plan_id, payload.profile)
    response.headers["Location"] = f"/api/jobs/{job.id}"
    return job


@api_router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: UUID, request: Request) -> Job:
    return job_service(request).get(job_id)


@api_router.get("/jobs/{job_id}/artifact", response_class=StreamingResponse)
def get_job_artifact(job_id: UUID, request: Request) -> StreamingResponse:
    job = job_service(request).get(job_id)
    if job.status != "succeeded":
        raise DomainError(
            "artifact_not_ready",
            "Render artifact is not ready",
            status_code=status.HTTP_409_CONFLICT,
        )
    if job.artifact_path is None:
        raise DomainError(
            "artifact_missing",
            "Render artifact is missing",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    artifact, size = _open_artifact(
        request.app.state.settings.data_dir, Path(job.artifact_path)
    )
    filename = f"holden-reel-{job.kind}-{job.id}.mp4"
    return _ArtifactStreamingResponse(
        artifact,
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(size),
        },
        background=BackgroundTask(artifact.close),
    )


@api_router.post("/jobs/{job_id}/cancel", response_model=Job)
def cancel_job(job_id: UUID, request: Request) -> Job:
    return job_service(request).cancel(job_id)


def _open_artifact(data_dir: Path, artifact_path: Path) -> tuple[BinaryIO, int]:
    data_root = Path(os.path.abspath(data_dir))
    candidate = Path(os.path.abspath(artifact_path))
    try:
        relative = candidate.relative_to(data_root)
    except ValueError:
        raise _unsafe_artifact_path() from None
    if not relative.parts or relative.suffix.lower() != ".mp4":
        raise _unsafe_artifact_path()

    directory_fd = -1
    file_fd = -1
    try:
        directory_fd = _open_verified_root(data_root)
        for component in relative.parts[:-1]:
            next_fd = _open_verified_directory(directory_fd, component)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd, size = _open_verified_file(directory_fd, relative.parts[-1])
        artifact = os.fdopen(file_fd, "rb")
        file_fd = -1
        return artifact, size
    except FileNotFoundError:
        raise DomainError(
            "artifact_missing",
            "Render artifact is missing",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from None
    except _UnsafeArtifact:
        raise _unsafe_artifact_path() from None
    except OSError:
        raise _unsafe_artifact_path() from None
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if directory_fd >= 0:
            os.close(directory_fd)


class _UnsafeArtifact(Exception):
    pass


def _open_verified_root(path: Path) -> int:
    before = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(before.st_mode):
        raise _UnsafeArtifact
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        _verify_same_inode(before, os.fstat(descriptor), stat.S_ISDIR)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_verified_directory(parent_fd: int, name: str) -> int:
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(before.st_mode):
        raise _UnsafeArtifact
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=parent_fd)
    try:
        _verify_same_inode(before, os.fstat(descriptor), stat.S_ISDIR)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_verified_file(parent_fd: int, name: str) -> tuple[int, int]:
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise _UnsafeArtifact
    descriptor = _open_file_at(parent_fd, name)
    try:
        after = os.fstat(descriptor)
        _verify_same_inode(before, after, stat.S_ISREG)
        return descriptor, after.st_size
    except BaseException:
        os.close(descriptor)
        raise


def _open_file_at(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(name, flags, dir_fd=parent_fd)


def _verify_same_inode(before, after, expected_type) -> None:
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or not expected_type(after.st_mode)
    ):
        raise _UnsafeArtifact


def _stream_file(artifact: BinaryIO, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    try:
        while chunk := artifact.read(chunk_size):
            yield chunk
    finally:
        artifact.close()


class _ArtifactStreamingResponse(StreamingResponse):
    def __init__(self, artifact: BinaryIO, **kwargs):
        self._artifact = artifact
        super().__init__(_stream_file(artifact), **kwargs)

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._artifact.close()


def _unsafe_artifact_path() -> DomainError:
    return DomainError(
        "unsafe_artifact_path",
        "Render artifact path is unsafe",
        status_code=status.HTTP_409_CONFLICT,
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        _: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        details = {"status_code": error.status_code}
        message = error.detail if isinstance(error.detail, str) else "HTTP request failed"
        if not isinstance(error.detail, str):
            details["detail"] = jsonable_encoder(error.detail)
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": "http_error",
                    "message": message,
                    "details": details,
                }
            },
            headers=error.headers,
        )

    @app.exception_handler(DomainError)
    async def handle_domain_error(_: Request, error: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Request validation failed",
                    "details": {"errors": jsonable_encoder(error.errors())},
                }
            },
        )
