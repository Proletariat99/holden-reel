from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
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


@api_router.get("/jobs/{job_id}/artifact", response_class=FileResponse)
def get_job_artifact(job_id: UUID, request: Request) -> FileResponse:
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

    data_root = request.app.state.settings.data_dir.resolve()
    artifact = Path(job.artifact_path)
    resolved_artifact = artifact.resolve()
    if (
        resolved_artifact == data_root
        or not resolved_artifact.is_relative_to(data_root)
        or resolved_artifact.suffix.lower() != ".mp4"
    ):
        raise DomainError(
            "unsafe_artifact_path",
            "Render artifact path is unsafe",
            status_code=status.HTTP_409_CONFLICT,
        )
    if not resolved_artifact.is_file():
        raise DomainError(
            "artifact_missing",
            "Render artifact is missing",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return FileResponse(
        resolved_artifact,
        media_type="video/mp4",
        filename=f"holden-reel-{job.kind}-{job.id}.mp4",
    )


@api_router.post("/jobs/{job_id}/cancel", response_model=Job)
def cancel_job(job_id: UUID, request: Request) -> Job:
    return job_service(request).cancel(job_id)


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
