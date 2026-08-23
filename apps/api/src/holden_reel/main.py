from fastapi import FastAPI

from .api import api_router, register_error_handlers
from .config import Settings
from .db import open_database
from .projects import ProjectRepository, ProjectService


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Holden Reel", version="0.1.0")
    app.state.settings = settings or Settings()
    database = open_database(app.state.settings.data_dir / "holden-reel.sqlite3")
    app.state.project_service = ProjectService(ProjectRepository(database))
    register_error_handlers(app)
    app.include_router(api_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": app.version}

    return app


app = create_app()
