from uuid import UUID

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import DomainError
from .projects import Project, ProjectService


class CreateProjectRequest(BaseModel):
    name: str


api_router = APIRouter(prefix="/api")


def project_service(request: Request) -> ProjectService:
    return request.app.state.project_service


@api_router.post("/projects", response_model=Project, status_code=status.HTTP_201_CREATED)
def create_project(payload: CreateProjectRequest, request: Request) -> Project:
    return project_service(request).create(payload.name)


@api_router.get("/projects", response_model=list[Project])
def list_projects(request: Request) -> list[Project]:
    return project_service(request).list()


@api_router.get("/projects/{project_id}", response_model=Project)
def get_project(project_id: UUID, request: Request) -> Project:
    return project_service(request).get(project_id)


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
