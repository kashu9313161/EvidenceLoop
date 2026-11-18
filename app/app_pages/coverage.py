import streamlit as st

from app.components import STATUS_STYLES
from app.data import STAGE_COLUMNS, coverage_frame, coverage_summary_frame, stage_frame
from app.state import require_snapshot

snapshot = require_snapshot()
snapshot_id = snapshot["snapshot_id"]

st.info(
    "**Analyzed** code files went through the code stages (Python syntax tree, or JS/TS "
    "imports). **Indexed** files (docs, config) are searchable but have no code analysis. "
    "**Pending** files are waiting for analysis, **failed** ones show why, and **skipped** "
    "files are never analyzed. A clean report only means something for files that were "
    "actually analyzed.",
    title="Not analyzed is not the same as no issues",
    icon=":material/info:",
)

files = coverage_frame(snapshot_id)
summary = coverage_summary_frame(snapshot_id)
stages = stage_frame(snapshot_id)
files = files.merge(stages, on="relative_path", how="left")
files[list(STAGE_COLUMNS)] = files[list(STAGE_COLUMNS)].fillna("")

with st.container(horizontal=True):
    for status in ("analyzed", "indexed", "failed", "pending", "skipped"):
        label = STATUS_STYLES[status][0]
        count = int(summary.loc[summary["status"] == status, "files"].sum())
        if count or status in {"analyzed", "skipped"}:
            st.metric(label, count, border=True)

skipped = summary[summary["status"] == "skipped"]
if not skipped.empty:
    st.subheader("Why files were skipped", anchor=False)
    st.bar_chart(
        skipped.rename(columns={"skip_reason": "reason"}),
        x="reason",
        y="files",
        horizontal=True,
        sort="-files",
        alt="Bar chart of skipped file counts by skip reason",
    )

st.subheader("Files", anchor=False)

with st.container(horizontal=True, vertical_alignment="bottom"):
    statuses = st.pills(
        "Status",
        sorted(files["status"].unique()),
        selection_mode="multi",
        format_func=lambda s: STATUS_STYLES.get(s, (s,))[0],
        key="coverage_status",
    )
    extensions = st.multiselect(
        "Extension",
        sorted(e for e in files["extension"].unique() if e),
        key="coverage_ext",
        placeholder="All extensions",
    )
    query = st.text_input(
        "Path contains", key="coverage_query", placeholder="e.g. src/ or serializer"
    )

view = files
if statuses:
    view = view[view["status"].isin(statuses)]
if extensions:
    view = view[view["extension"].isin(extensions)]
if query:
    view = view[view["relative_path"].str.contains(query, case=False, regex=False)]

st.caption(f"Showing {len(view)} of {len(files)} files")

st.dataframe(
    view.assign(status=view["status"].map(lambda s: STATUS_STYLES.get(s, (s,))[0])),
    hide_index=True,
    column_order=["relative_path", "status", *STAGE_COLUMNS, "skip_reason", "size_bytes"],
    column_config={
        "relative_path": st.column_config.TextColumn("Path", width="medium"),
        "status": st.column_config.TextColumn("Status"),
        **{stage: st.column_config.TextColumn(label) for stage, label in STAGE_COLUMNS.items()},
        "skip_reason": st.column_config.TextColumn("Reason"),
        "size_bytes": st.column_config.NumberColumn("Size", format="bytes"),
    },
    key="coverage_table",
    alt="Coverage ledger: every file with its status, per-stage results and reason",
)

st.caption(
    "Stage columns show each analysis step per file; an empty cell means the step doesn't "
    "apply. Security, complexity and history stages arrive in Milestone 7."
)
