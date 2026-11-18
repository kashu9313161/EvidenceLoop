import streamlit as st

from app.data import coverage_frame
from app.state import require_snapshot
from storage.database import get_connection

snapshot = require_snapshot()
snapshot_id = snapshot["snapshot_id"]

with get_connection() as connection:
    repo = connection.execute(
        "SELECT * FROM repositories WHERE repository_id = ?", (snapshot["repository_id"],)
    ).fetchone()

with st.container(horizontal=True):
    st.metric("Files", snapshot["total_files"], border=True)
    st.metric("Eligible for analysis", snapshot["eligible_files"], border=True)
    st.metric("Skipped", snapshot["skipped_files"], border=True)

st.subheader("Identity", anchor=False)

commit = snapshot["commit_sha"]
if repo["source_type"] == "github" and commit:
    commit_value = (
        f"[`{commit}`](https://github.com/{repo['owner']}/{repo['name']}/tree/{commit})"
    )
    source_value = f"[{repo['owner']}/{repo['name']}]({repo['source_url']})"
else:
    commit_value = "Not applicable (ZIP upload)"
    source_value = f"ZIP upload `{repo['name']}`"

st.table(
    {
        "Field": [
            "Source",
            "Snapshot ID",
            "Commit",
            "Archive SHA-256",
            "Ingested (UTC)",
            "Local path",
        ],
        "Value": [
            source_value,
            f"`{snapshot_id}`",
            commit_value,
            f"`{snapshot['archive_sha256']}`",
            snapshot["created_at"],
            f"`{snapshot['root_path']}`",
        ],
    },
    hide_index=True,
    hide_header=True,
)

files = coverage_frame(snapshot_id)
left, right = st.columns(2)

with left:
    st.subheader("Files by extension", anchor=False)
    by_extension = (
        files.assign(extension=files["extension"].replace("", "(none)"))
        .groupby("extension")
        .size()
        .sort_values(ascending=False)
        .head(12)
        .rename("files")
        .reset_index()
    )
    st.bar_chart(
        by_extension,
        x="extension",
        y="files",
        horizontal=True,
        sort="-files",
        alt="Bar chart of file counts by extension",
    )

with right:
    st.subheader("Files by top-level folder", anchor=False)
    by_folder = (
        files.groupby("folder")
        .agg(files=("relative_path", "size"), eligible=("status", lambda s: (s == "pending").sum()))
        .sort_values("files", ascending=False)
        .reset_index()
    )
    st.dataframe(
        by_folder,
        hide_index=True,
        column_config={
            "folder": "Folder",
            "files": st.column_config.NumberColumn("Files"),
            "eligible": st.column_config.NumberColumn("Eligible"),
        },
        alt="Table of file counts per top-level folder",
    )
