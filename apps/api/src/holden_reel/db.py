import sqlite3
from pathlib import Path


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
