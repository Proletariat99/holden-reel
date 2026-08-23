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

    connection.commit()
    return connection
