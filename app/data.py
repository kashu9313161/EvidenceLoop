"""Cached, DataFrame-shaped reads for the UI.

Snapshots are immutable, so cached results stay valid; the database path is
part of the cache key so tests and different data dirs never share entries.
"""

import pandas as pd
import streamlit as st

from core import config
from storage.database import get_coverage, get_coverage_summary, list_snapshots


def _db_key() -> str:
    return str(config.DATABASE_PATH)


@st.cache_data(show_spinner=False, max_entries=32)
def _coverage_frame(db_key: str, snapshot_id: str) -> pd.DataFrame:
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
    return _coverage_frame(_db_key(), snapshot_id)


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
