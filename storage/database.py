 
import sqlite3

from core.config import DATABASE_PATH


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS repositories (
    repository_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_url TEXT,
    owner TEXT,
    name TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL,
    commit_sha TEXT,
    archive_sha256 TEXT NOT NULL,
    root_path TEXT NOT NULL,
    total_files INTEGER NOT NULL DEFAULT 0,
    eligible_files INTEGER NOT NULL DEFAULT 0,
    skipped_files INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (repository_id)
        REFERENCES repositories(repository_id)
);

CREATE TABLE IF NOT EXISTS files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    extension TEXT,
    size_bytes INTEGER NOT NULL,
    content_sha256 TEXT,
    status TEXT NOT NULL,
    skip_reason TEXT,
    FOREIGN KEY (snapshot_id)
        REFERENCES snapshots(snapshot_id),
    UNIQUE(snapshot_id, relative_path)
);

CREATE INDEX IF NOT EXISTS idx_files_snapshot
ON files(snapshot_id);

CREATE INDEX IF NOT EXISTS idx_files_status
ON files(status);

CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    snapshot_id UNINDEXED,
    relative_path,
    content
);

CREATE TABLE IF NOT EXISTS graph_nodes (
    node_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    name TEXT NOT NULL,
    file_path TEXT,
    start_line INTEGER,
    end_line INTEGER,
    FOREIGN KEY (snapshot_id)
        REFERENCES snapshots(snapshot_id)
);

CREATE TABLE IF NOT EXISTS graph_edges (
    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    relationship TEXT NOT NULL,
    source_path TEXT,
    start_line INTEGER,
    end_line INTEGER,
    extraction_method TEXT NOT NULL,
    evidence_status TEXT NOT NULL DEFAULT 'inferred',
    FOREIGN KEY (snapshot_id)
        REFERENCES snapshots(snapshot_id)
);
"""


def get_connection() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")

    return connection


def initialize_database() -> None:
    with get_connection() as connection:
        connection.executescript(SCHEMA)


def get_snapshot(snapshot_id: str):
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT *
            FROM snapshots
            WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()


def get_coverage(snapshot_id: str):
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT relative_path, status, skip_reason, size_bytes
            FROM files
            WHERE snapshot_id = ?
            ORDER BY relative_path
            """,
            (snapshot_id,),
        ).fetchall()
