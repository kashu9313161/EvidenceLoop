import json
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from core import config

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

-- M2: code analysis --------------------------------------------------------

-- One row per (file, stage). Stages: python_ast, js_shallow, fts (later: graph,
-- scanners, history). status: analyzed | failed | not_applicable.
CREATE TABLE IF NOT EXISTS file_stages (
    snapshot_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    PRIMARY KEY (snapshot_id, relative_path, stage),
    FOREIGN KEY (snapshot_id)
        REFERENCES snapshots(snapshot_id)
);

CREATE TABLE IF NOT EXISTS symbols (
    snapshot_id TEXT NOT NULL,
    symbol_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    module TEXT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    parent TEXT,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    decorators TEXT NOT NULL DEFAULT '[]',
    bases TEXT NOT NULL DEFAULT '[]',
    has_docstring INTEGER NOT NULL DEFAULT 0,
    is_async INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_id, symbol_id),
    FOREIGN KEY (snapshot_id)
        REFERENCES snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_symbols_path
ON symbols(snapshot_id, relative_path);

CREATE TABLE IF NOT EXISTS imports (
    import_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    kind TEXT NOT NULL,
    module TEXT NOT NULL,
    name TEXT,
    alias TEXT,
    level INTEGER NOT NULL DEFAULT 0,
    line INTEGER NOT NULL,
    FOREIGN KEY (snapshot_id)
        REFERENCES snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_imports_path
ON imports(snapshot_id, relative_path);

CREATE TABLE IF NOT EXISTS calls (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    callee TEXT NOT NULL,
    line INTEGER NOT NULL,
    enclosing_symbol TEXT,
    FOREIGN KEY (snapshot_id)
        REFERENCES snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_calls_path
ON calls(snapshot_id, relative_path);

CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    snapshot_id UNINDEXED,
    symbol_id UNINDEXED,
    qualified_name,
    path,
    start_line UNINDEXED,
    end_line UNINDEXED,
    text
);

-- One row per analysis run that finished (latest wins in the UI).
CREATE TABLE IF NOT EXISTS analysis_reports (
    snapshot_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    report TEXT NOT NULL,
    FOREIGN KEY (snapshot_id)
        REFERENCES snapshots(snapshot_id)
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    analyzer_version TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    summary TEXT NOT NULL,
    FOREIGN KEY (snapshot_id)
        REFERENCES snapshots(snapshot_id)
);

-- AI-written file explanations, cached until the file, the prompt or the model changes.
CREATE TABLE IF NOT EXISTS file_explanations (
    snapshot_id TEXT NOT NULL,
    path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    explanation TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, path, content_sha256, prompt_version, model)
);

-- M3: Ask. One row per question; its claims; the evidence each claim cites.
CREATE TABLE IF NOT EXISTS asks (
    ask_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    question TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    usage TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    meta TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_asks_snapshot ON asks(snapshot_id);

CREATE TABLE IF NOT EXISTS ask_claims (
    ask_id TEXT NOT NULL,
    claim_index INTEGER NOT NULL,
    snapshot_id TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    support REAL,
    contradiction REAL,
    sufficiency REAL,
    evaluator TEXT,
    model_version TEXT,
    checks TEXT NOT NULL,
    PRIMARY KEY (ask_id, claim_index)
);

CREATE TABLE IF NOT EXISTS ask_claim_evidence (
    ask_id TEXT NOT NULL,
    claim_index INTEGER NOT NULL,
    snapshot_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    path TEXT,
    start_line INTEGER,
    end_line INTEGER,
    sha256 TEXT
);

-- Every call to a language-model provider: what was sent, by whom, and what it cost.
CREATE TABLE IF NOT EXISTS provider_sends (
    send_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    ask_id TEXT,
    purpose TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    prompt_chars INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL,
    error TEXT
);
"""


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Open a connection; commit on success, roll back on error, always close."""
    config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(config.DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")

    try:
        with connection:
            yield connection
    finally:
        connection.close()


# Columns added after a table first shipped: (table, column, definition). CREATE TABLE IF NOT
# EXISTS leaves an existing table alone, so these are added on start-up when missing.
ADDED_COLUMNS = [("asks", "meta", "TEXT NOT NULL DEFAULT '{}'")]


def initialize_database() -> None:
    with get_connection() as connection:
        connection.executescript(SCHEMA)
        for table, column, definition in ADDED_COLUMNS:
            have = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def get_snapshot(snapshot_id: str):
    initialize_database()
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT *
            FROM snapshots
            WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()


def snapshot_root(snapshot) -> Path | None:
    """The snapshot's files on disk, or None if they are gone.

    `root_path` is absolute, so it breaks when the project folder moves to another place or
    machine. Snapshots always live at `<repositories>/<repo dir>/<snapshot dir>`, so fall back
    to that layout under the current repositories directory.
    """
    stored = Path(snapshot["root_path"])
    if stored.is_dir():
        return stored.resolve()
    parts = str(snapshot["root_path"]).replace("\\", "/").rstrip("/").split("/")
    candidate = Path(config.REPOSITORIES_DIR, *parts[-2:])
    if candidate.is_dir():
        return candidate.resolve()
    return None


SNAPSHOT_TABLES = ("files", "files_fts", "file_stages", "symbols", "symbols_fts", "imports",
                   "calls", "graph_nodes", "graph_edges", "analysis_runs", "analysis_reports",
                   "file_explanations", "asks", "ask_claims", "ask_claim_evidence",
                   "provider_sends")


def delete_snapshot(snapshot_id: str, repositories_dir: Path | None = None) -> bool:
    """Remove a snapshot: every row about it, its repository row when no other snapshot uses
    it, and its extracted files (only when they live under the repositories directory).
    Returns False if there was no such snapshot."""
    snapshot = get_snapshot(snapshot_id)
    if snapshot is None:
        return False
    root = snapshot_root(snapshot)
    with get_connection() as connection:
        for table in SNAPSHOT_TABLES:
            connection.execute(f"DELETE FROM {table} WHERE snapshot_id = ?", (snapshot_id,))
        connection.execute("DELETE FROM snapshots WHERE snapshot_id = ?", (snapshot_id,))
        connection.execute(
            "DELETE FROM repositories WHERE repository_id = ? AND NOT EXISTS "
            "(SELECT 1 FROM snapshots WHERE repository_id = ?)",
            (snapshot["repository_id"], snapshot["repository_id"]),
        )
    base = Path(repositories_dir or config.REPOSITORIES_DIR).resolve()
    if root is not None and root != base and root.is_relative_to(base):
        shutil.rmtree(root, ignore_errors=True)
        parent = root.parent
        if parent != base and parent.is_relative_to(base) and not any(parent.iterdir()):
            parent.rmdir()  # the repository's folder, now empty
    return True


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


def list_snapshots() -> list[sqlite3.Row]:
    """All ingested snapshots, newest first, joined with repository identity."""
    initialize_database()

    with get_connection() as connection:
        return connection.execute(
            """
            SELECT s.snapshot_id, s.repository_id, s.commit_sha, s.archive_sha256,
                   s.root_path, s.total_files, s.eligible_files, s.skipped_files,
                   s.created_at, r.source_type, r.source_url, r.owner, r.name
            FROM snapshots AS s
            JOIN repositories AS r ON r.repository_id = s.repository_id
            ORDER BY s.created_at DESC, s.snapshot_id
            """
        ).fetchall()


def get_coverage_summary(snapshot_id: str) -> list[sqlite3.Row]:
    """File counts and bytes grouped by status and skip reason."""
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT status, skip_reason, COUNT(*) AS files, SUM(size_bytes) AS bytes
            FROM files
            WHERE snapshot_id = ?
            GROUP BY status, skip_reason
            ORDER BY files DESC
            """,
            (snapshot_id,),
        ).fetchall()


def get_latest_analysis(snapshot_id: str) -> dict | None:
    """Summary of the most recent analysis run for a snapshot, or None if never analyzed."""
    initialize_database()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT run_id, analyzer_version, finished_at, summary
            FROM analysis_runs
            WHERE snapshot_id = ?
            ORDER BY run_id DESC
            LIMIT 1
            """,
            (snapshot_id,),
        ).fetchone()

    if row is None:
        return None

    return {
        "run_id": row["run_id"],
        "analyzer_version": row["analyzer_version"],
        "finished_at": row["finished_at"],
        **json.loads(row["summary"]),
    }


def get_file_stages(snapshot_id: str) -> list[sqlite3.Row]:
    initialize_database()

    with get_connection() as connection:
        return connection.execute(
            """
            SELECT relative_path, stage, status, reason
            FROM file_stages
            WHERE snapshot_id = ?
            ORDER BY relative_path, stage
            """,
            (snapshot_id,),
        ).fetchall()


def list_symbols(snapshot_id: str, relative_path: str | None = None) -> list[sqlite3.Row]:
    initialize_database()

    query = """
        SELECT symbol_id, relative_path, module, kind, name, qualified_name, parent,
               start_line, end_line, decorators, bases, has_docstring, is_async
        FROM symbols
        WHERE snapshot_id = ?
    """
    params: list = [snapshot_id]

    if relative_path is not None:
        query += " AND relative_path = ?"
        params.append(relative_path)

    query += " ORDER BY relative_path, start_line, end_line DESC"

    with get_connection() as connection:
        return connection.execute(query, params).fetchall()


def list_imports(snapshot_id: str, relative_path: str | None = None) -> list[sqlite3.Row]:
    initialize_database()

    query = """
        SELECT relative_path, kind, module, name, alias, level, line
        FROM imports
        WHERE snapshot_id = ?
    """
    params: list = [snapshot_id]

    if relative_path is not None:
        query += " AND relative_path = ?"
        params.append(relative_path)

    query += " ORDER BY relative_path, line, import_id"

    with get_connection() as connection:
        return connection.execute(query, params).fetchall()


def search_symbols(snapshot_id: str, text: str, limit: int = 50) -> list[sqlite3.Row]:
    """Full-text search over symbol names, paths and source text."""
    terms = [term for term in text.replace('"', " ").split() if term]
    if not terms:
        return []

    # Quote every term so FTS5 operators in user input are treated as plain words.
    match = " ".join(f'"{term}"*' for term in terms)

    initialize_database()

    with get_connection() as connection:
        return connection.execute(
            """
            SELECT symbol_id, qualified_name, path, start_line, end_line
            FROM symbols_fts
            WHERE symbols_fts MATCH ? AND snapshot_id = ?
            ORDER BY rank
            LIMIT ?
            """,
            (match, snapshot_id, limit),
        ).fetchall()
