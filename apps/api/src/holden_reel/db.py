import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from threading import RLock


class Database:
    """Serialize access to a shared SQLite connection owned by the application."""

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._lock = RLock()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def fetch_all(self, query: str, parameters: Sequence[object] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(query, parameters).fetchall()

    def fetch_one(
        self, query: str, parameters: Sequence[object] = ()
    ) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(query, parameters).fetchone()


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
    )

    migration = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 1"
    ).fetchone()
    if migration is None:
        connection.execute(
            """
            CREATE TABLE projects (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute("INSERT INTO schema_migrations (version) VALUES (1)")

    migration = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 2"
    ).fetchone()
    if migration is None:
        connection.execute(
            """
            CREATE TABLE media_assets (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL REFERENCES projects(id),
              path TEXT NOT NULL,
              kind TEXT NOT NULL,
              duration_ms INTEGER,
              width INTEGER,
              height INTEGER,
              codec TEXT,
              available INTEGER NOT NULL,
              size_bytes INTEGER NOT NULL,
              modified_ns INTEGER NOT NULL,
              fingerprint TEXT NOT NULL,
              UNIQUE(project_id, path)
            )
            """
        )
        connection.execute("INSERT INTO schema_migrations (version) VALUES (2)")

    migration = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 3"
    ).fetchone()
    if migration is None:
        connection.execute(
            """
            CREATE TABLE reel_plans (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL REFERENCES projects(id),
              version INTEGER NOT NULL,
              plan_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(project_id, version)
            )
            """
        )
        connection.execute("INSERT INTO schema_migrations (version) VALUES (3)")

    migration = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 4"
    ).fetchone()
    if migration is None:
        connection.execute(
            """
            CREATE TABLE jobs (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL REFERENCES projects(id),
              kind TEXT NOT NULL CHECK (kind IN ('preview', 'final')),
              status TEXT NOT NULL CHECK (
                status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
              ),
              progress REAL NOT NULL CHECK (progress >= 0.0 AND progress <= 1.0),
              plan_id TEXT NOT NULL REFERENCES reel_plans(id),
              artifact_path TEXT,
              error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute("INSERT INTO schema_migrations (version) VALUES (4)")

    migration = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 5"
    ).fetchone()
    if migration is None:
        connection.execute(
            "ALTER TABLE media_assets ADD COLUMN has_audio INTEGER NOT NULL DEFAULT 0"
        )
        connection.execute("ALTER TABLE media_assets ADD COLUMN audio_duration_ms INTEGER")
        connection.execute(
            """
            UPDATE media_assets
            SET has_audio = 1, audio_duration_ms = duration_ms
            WHERE kind = 'audio'
            """
        )
        connection.execute("INSERT INTO schema_migrations (version) VALUES (5)")

    migration = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 6"
    ).fetchone()
    if migration is None:
        connection.execute("ALTER TABLE media_assets ADD COLUMN focus_x REAL")
        connection.execute("ALTER TABLE media_assets ADD COLUMN focus_y REAL")
        connection.execute("ALTER TABLE media_assets ADD COLUMN focus_confidence REAL")
        connection.execute("ALTER TABLE media_assets ADD COLUMN focus_method TEXT")
        connection.execute(
            "ALTER TABLE media_assets ADD COLUMN focus_analyzer_version INTEGER"
        )
        connection.execute("ALTER TABLE media_assets ADD COLUMN focus_fingerprint TEXT")
        connection.execute("INSERT INTO schema_migrations (version) VALUES (6)")

    connection.commit()
    return connection
