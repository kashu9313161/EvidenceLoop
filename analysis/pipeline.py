"""Analyze a stored snapshot: per-file facts, full-text index and the coverage ledger.

`analyze_snapshot` is idempotent: it reads every eligible file, then deletes that snapshot's
analysis rows and rewrites them in one transaction, so a rerun never duplicates anything and
a crash mid-way leaves the previous results intact.
"""

import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from analysis.javascript import shallow
from analysis.models import AnalysisFailure, FileFacts
from analysis.python import extractor as python_extractor
from core.textio import decode_text
from storage.database import get_connection, get_snapshot, initialize_database, snapshot_root

ANALYZER_VERSION = "m2.1"
PYTHON_EXTENSIONS = {".py", ".pyi"}

STAGE_PYTHON = "python_ast"
STAGE_JS = "js_shallow"
STAGE_FTS = "fts"

ProgressCallback = Callable[[int, int, str], None]


class AnalysisError(Exception):
    """The snapshot as a whole cannot be analyzed (e.g. unknown ID, files missing)."""


@dataclass
class FileResult:
    relative_path: str
    status: str  # analyzed | indexed | failed
    reason: str | None = None
    stages: list[tuple[str, str, str | None]] = field(default_factory=list)
    facts: FileFacts | None = None
    text: str | None = None


def code_stage_for(relative_path: str) -> str | None:
    suffix = PurePosixPath(relative_path).suffix.lower()
    if suffix in PYTHON_EXTENSIONS:
        return STAGE_PYTHON
    if suffix in shallow.SHALLOW_EXTENSIONS:
        return STAGE_JS
    return None


def _read_inside(root: Path, relative_path: str) -> bytes:
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise AnalysisFailure("Path escapes the snapshot root")
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise AnalysisFailure("File missing from the stored snapshot") from exc
    except OSError as exc:
        raise AnalysisFailure(f"Could not read file: {exc.strerror or type(exc).__name__}") from exc


def analyze_file(root: Path, relative_path: str) -> FileResult:
    """Run every applicable stage on one file. Never raises for per-file problems."""
    result = FileResult(relative_path=relative_path, status="indexed")
    code_stage = code_stage_for(relative_path)

    try:
        data = _read_inside(root, relative_path)
    except AnalysisFailure as exc:
        result.status, result.reason = "failed", exc.reason
        for stage in filter(None, (code_stage, STAGE_FTS)):
            result.stages.append((stage, "failed", exc.reason))
        return result

    # Full-text stage: any eligible text file (UTF-8, or UTF-16 with a byte-order mark).
    text = decode_text(data)
    if text is None:
        result.stages.append((STAGE_FTS, "failed", "Binary content"))
    else:
        result.text = text
        result.stages.append((STAGE_FTS, "analyzed", None))

    if code_stage is None:
        if result.text is None:
            result.status, result.reason = "failed", "Binary content"
        return result

    try:
        if code_stage == STAGE_PYTHON:
            source = python_extractor.decode_source(data)
            result.facts = python_extractor.extract_file(relative_path, source)
        else:
            result.facts = shallow.extract_file(relative_path, data.decode("utf-8", "replace"))
    except AnalysisFailure as exc:
        result.status, result.reason = "failed", exc.reason
        result.stages.append((code_stage, "failed", exc.reason))
    else:
        result.status = "analyzed"
        result.stages.append((code_stage, "analyzed", None))

    return result


def _symbol_rows(facts: FileFacts):
    """Yield (symbol_id, symbol). IDs are `path::qualified_name`, with `@line` on repeats
    (e.g. a property getter and setter share a name)."""
    seen: Counter[str] = Counter()

    for symbol in facts.symbols:
        base = f"{facts.relative_path}::{symbol.qualified_name}"
        seen[base] += 1
        symbol_id = base if seen[base] == 1 else f"{base}@{symbol.start_line}"
        yield symbol_id, symbol


def _write(snapshot_id: str, results: list[FileResult], summary: dict) -> None:
    with get_connection() as connection:
        for table in ("file_stages", "symbols", "imports", "calls", "files_fts", "symbols_fts"):
            connection.execute(f"DELETE FROM {table} WHERE snapshot_id = ?", (snapshot_id,))

        for result in results:
            path = result.relative_path

            connection.execute(
                "UPDATE files SET status = ?, skip_reason = ? "
                "WHERE snapshot_id = ? AND relative_path = ?",
                (result.status, result.reason, snapshot_id, path),
            )
            connection.executemany(
                "INSERT INTO file_stages (snapshot_id, relative_path, stage, status, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                [(snapshot_id, path, stage, status, reason)
                 for stage, status, reason in result.stages],
            )

            if result.text is not None:
                connection.execute(
                    "INSERT INTO files_fts (snapshot_id, relative_path, content) VALUES (?, ?, ?)",
                    (snapshot_id, path, result.text),
                )

            facts = result.facts
            if facts is None:
                continue

            for symbol_id, symbol in _symbol_rows(facts):
                connection.execute(
                    """
                    INSERT INTO symbols (
                        snapshot_id, symbol_id, relative_path, module, kind, name,
                        qualified_name, parent, start_line, end_line, decorators, bases,
                        has_docstring, is_async
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id, symbol_id, path, facts.module, symbol.kind, symbol.name,
                        symbol.qualified_name, symbol.parent, symbol.start_line,
                        symbol.end_line, json.dumps(symbol.decorators),
                        json.dumps(symbol.bases), int(symbol.has_docstring),
                        int(symbol.is_async),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO symbols_fts (
                        snapshot_id, symbol_id, qualified_name, path, start_line, end_line, text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id, symbol_id, symbol.qualified_name, path,
                        symbol.start_line, symbol.end_line, symbol.text,
                    ),
                )

            connection.executemany(
                "INSERT INTO imports (snapshot_id, relative_path, kind, module, name, alias, "
                "level, line) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(snapshot_id, path, ref.kind, ref.module, ref.name, ref.alias, ref.level,
                  ref.line) for ref in facts.imports],
            )
            connection.executemany(
                "INSERT INTO calls (snapshot_id, relative_path, callee, line, enclosing_symbol) "
                "VALUES (?, ?, ?, ?, ?)",
                [(snapshot_id, path, call.callee, call.line, call.enclosing_symbol)
                 for call in facts.calls],
            )

        connection.execute(
            "INSERT INTO analysis_runs (snapshot_id, analyzer_version, summary) VALUES (?, ?, ?)",
            (snapshot_id, ANALYZER_VERSION, json.dumps(summary)),
        )


def analyze_snapshot(snapshot_id: str, progress: ProgressCallback | None = None) -> dict:
    """Analyze every non-skipped file of a snapshot and return a summary dict."""
    initialize_database()
    started = time.perf_counter()

    snapshot = get_snapshot(snapshot_id)
    if snapshot is None:
        raise AnalysisError(f"Unknown snapshot: {snapshot_id}")

    root = snapshot_root(snapshot)
    if root is None:
        raise AnalysisError("The snapshot's files are missing on disk. Ingest it again.")

    with get_connection() as connection:
        paths = [
            row["relative_path"]
            for row in connection.execute(
                "SELECT relative_path FROM files WHERE snapshot_id = ? AND status != 'skipped' "
                "ORDER BY relative_path",
                (snapshot_id,),
            )
        ]

    results: list[FileResult] = []
    for index, path in enumerate(paths, start=1):
        results.append(analyze_file(root, path))
        if progress is not None:
            progress(index, len(paths), path)

    statuses = Counter(result.status for result in results)
    facts = [result.facts for result in results if result.facts is not None]
    symbol_kinds = Counter(symbol.kind for f in facts for symbol in f.symbols)

    summary = {
        "files": len(results),
        "analyzed": statuses["analyzed"],
        "indexed": statuses["indexed"],
        "failed": statuses["failed"],
        "python_files": sum(f.language == "python" for f in facts),
        "js_ts_files": sum(f.language != "python" for f in facts),
        "symbols": sum(symbol_kinds.values()),
        "symbol_kinds": dict(symbol_kinds),
        "imports": sum(len(f.imports) for f in facts),
        "calls": sum(len(f.calls) for f in facts),
        "seconds": round(time.perf_counter() - started, 2),
    }

    _write(snapshot_id, results, summary)
    return {"snapshot_id": snapshot_id, "analyzer_version": ANALYZER_VERSION, **summary}
