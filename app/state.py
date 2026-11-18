"""Active-snapshot state shared by every page.

The active snapshot lives in the sidebar selectbox, bound to the `?snapshot=`
query parameter, so reloads and shared links keep pointing at the same
immutable snapshot.
"""

import sqlite3

import streamlit as st

from storage.database import get_snapshot, list_snapshots

SNAPSHOT_KEY = "snapshot"  # widget key and URL query-parameter name
PENDING_KEY = "pending_snapshot"


def get_active_snapshot_id() -> str | None:
    return st.session_state.get(SNAPSHOT_KEY)


def set_active_snapshot(snapshot_id: str) -> None:
    """Select a snapshot on the next run.

    The picker widget has usually rendered already this run, so its state
    can't be changed directly; the picker applies this before it renders.
    Call st.rerun() afterwards.
    """
    st.session_state[PENDING_KEY] = snapshot_id


def snapshot_label(row: sqlite3.Row) -> str:
    if row["source_type"] == "github":
        return f"{row['owner']}/{row['name']} @ {row['commit_sha'][:7]}"
    return f"{row['name']} (ZIP {row['snapshot_id'][4:11]})"


def snapshot_picker() -> None:
    """Sidebar selector for the snapshot every page is scoped to."""
    rows = list_snapshots()

    if not rows:
        st.caption("No snapshots yet. Add a repository to start.")
        return

    by_id = {row["snapshot_id"]: row for row in rows}

    pending = st.session_state.pop(PENDING_KEY, None)
    if pending in by_id:
        st.session_state[SNAPSHOT_KEY] = pending

    chosen = st.selectbox(
        "Active snapshot",
        list(by_id),
        format_func=lambda snapshot_id: snapshot_label(by_id[snapshot_id]),
        help="Every page shows results for this one immutable snapshot.",
        key=SNAPSHOT_KEY,
        bind="query-params",
    )

    row = by_id[chosen]
    if row["commit_sha"]:
        st.caption(f"Commit `{row['commit_sha'][:12]}`")
    st.caption(
        f"{row['total_files']} files · {row['eligible_files']} eligible · "
        f"{row['skipped_files']} skipped"
    )


def theme_hint() -> None:
    """Point users to Streamlit's built-in appearance setting."""
    st.caption(":material/contrast: Theme: ⋮ menu → Settings → Light / Dark / System")


def require_snapshot() -> sqlite3.Row:
    """Return the active snapshot row, or explain how to get one and stop the page."""
    snapshot_id = get_active_snapshot_id()
    row = get_snapshot(snapshot_id) if snapshot_id else None

    if row is None:
        st.info(
            "Ingest a GitHub repository or ZIP first. Every result in EvidenceLoop is tied "
            "to one snapshot.",
            title="No snapshot selected",
            icon=":material/info:",
        )
        st.page_link("app_pages/home.py", label="Add a repository", icon=":material/add:")
        st.stop()

    return row
