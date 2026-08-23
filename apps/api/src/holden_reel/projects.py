import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .errors import DomainError


@dataclass(frozen=True)
class Project:
    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime


class ProjectRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def insert(self, project: Project) -> Project:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO projects (id, name, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(project.id),
                    project.name,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                ),
            )
        return project

    def list_all(self) -> list[Project]:
        rows = self.connection.execute("SELECT * FROM projects ORDER BY rowid").fetchall()
        return [self._to_project(row) for row in rows]

    def get(self, project_id: UUID) -> Project | None:
        row = self.connection.execute(
            "SELECT * FROM projects WHERE id = ?", (str(project_id),)
        ).fetchone()
        return self._to_project(row) if row is not None else None

    @staticmethod
    def _to_project(row: sqlite3.Row) -> Project:
        return Project(
            id=UUID(row["id"]),
            name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class ProjectService:
    def __init__(self, repository: ProjectRepository):
        self.repository = repository

    def create(self, name: str) -> Project:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise DomainError(
                "invalid_project_name",
                "Project name must not be blank",
                status_code=422,
            )

        now = datetime.now(UTC)
        project = Project(id=uuid4(), name=cleaned_name, created_at=now, updated_at=now)
        return self.repository.insert(project)

    def list(self) -> list[Project]:
        return self.repository.list_all()

    def get(self, project_id: UUID) -> Project:
        project = self.repository.get(project_id)
        if project is None:
            raise DomainError(
                "project_not_found",
                "Project was not found",
                status_code=404,
            )
        return project
