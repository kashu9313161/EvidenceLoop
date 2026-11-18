"""Cached, DataFrame-shaped reads for the UI.

Snapshot files are immutable, but analysis rewrites statuses and facts, so every cache key
includes the latest analysis run ID. The database path is part of the key too, so tests and
different data dirs never share entries.
"""

import pandas as pd
import streamlit as st

from core import config
from storage.database import (
    get_coverage,
    get_coverage_summary,
    get_file_stages,
    get_latest_analysis,
    list_imports,
    list_snapshots,
    list_symbols,
)

STAGE_COLUMNS = {"python_ast": "Python AST", "js_shallow": "JS/TS imports", "fts": "Search index"}


def _db_key() -> str:
    return str(config.DATABASE_PATH)


def analysis_run_key(snapshot_id: str) -> int:
    """0 before the first analysis; changes every time analysis is rerun."""
    latest = get_latest_analysis(snapshot_id)
    return latest["run_id"] if latest else 0


@st.cache_data(show_spinner=False, max_entries=32)
def _coverage_frame(db_key: str, snapshot_id: str, run_key: int) -> pd.DataFrame:
    rows = [dict(row) for row in get_coverage(snapshot_id)]
    frame = pd.DataFrame(rows, columns=["relative_path", "status", "skip_reason", "size_bytes"])
    frame["extension"] = frame["relative_path"].map(
        lambda path: path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    )
    frame["folder"] = frame["relative_path"].map(
        lambda path: path.split("/", 1)[0] if "/" in path else "(root)"
    )
    return frame


def coverage_frame(snapshot_id: str) -> pd.DataFrame:
    return _coverage_frame(_db_key(), snapshot_id, analysis_run_key(snapshot_id))


@st.cache_data(show_spinner=False, max_entries=32)
def _stage_frame(db_key: str, snapshot_id: str, run_key: int) -> pd.DataFrame:
    """One row per file, one column per analysis stage (status, or empty if not applicable)."""
    rows = [dict(row) for row in get_file_stages(snapshot_id)]
    if not rows:
        return pd.DataFrame(columns=["relative_path", *STAGE_COLUMNS])

    frame = pd.DataFrame(rows).pivot(index="relative_path", columns="stage", values="status")
    for stage in STAGE_COLUMNS:
        if stage not in frame.columns:
            frame[stage] = None
    return frame[list(STAGE_COLUMNS)].reset_index()


def stage_frame(snapshot_id: str) -> pd.DataFrame:
    return _stage_frame(_db_key(), snapshot_id, analysis_run_key(snapshot_id))


@st.cache_data(show_spinner=False, max_entries=32)
def _symbols_frame(db_key: str, snapshot_id: str, run_key: int) -> pd.DataFrame:
    rows = [dict(row) for row in list_symbols(snapshot_id)]
    columns = ["symbol_id", "relative_path", "module", "kind", "name", "qualified_name",
               "parent", "start_line", "end_line", "decorators", "bases", "has_docstring",
               "is_async"]
    return pd.DataFrame(rows, columns=columns)


def symbols_frame(snapshot_id: str) -> pd.DataFrame:
    return _symbols_frame(_db_key(), snapshot_id, analysis_run_key(snapshot_id))


@st.cache_data(show_spinner=False, max_entries=32)
def _imports_frame(db_key: str, snapshot_id: str, run_key: int) -> pd.DataFrame:
    rows = [dict(row) for row in list_imports(snapshot_id)]
    columns = ["relative_path", "kind", "module", "name", "alias", "level", "line"]
    return pd.DataFrame(rows, columns=columns)


def imports_frame(snapshot_id: str) -> pd.DataFrame:
    return _imports_frame(_db_key(), snapshot_id, analysis_run_key(snapshot_id))


def coverage_summary_frame(snapshot_id: str) -> pd.DataFrame:
    rows = [dict(row) for row in get_coverage_summary(snapshot_id)]
    return pd.DataFrame(rows, columns=["status", "skip_reason", "files", "bytes"])


def snapshots_frame() -> pd.DataFrame:
    from app.state import snapshot_label

    rows = list_snapshots()
    return pd.DataFrame(
        [
            {
                "Snapshot": snapshot_label(row),
                "Source": row["source_type"],
                "Files": row["total_files"],
                "Eligible": row["eligible_files"],
                "Skipped": row["skipped_files"],
                "Ingested (UTC)": row["created_at"],
                "Snapshot ID": row["snapshot_id"],
            }
            for row in rows
        ]
    )
