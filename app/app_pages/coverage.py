import streamlit as st

from app.components import STATUS_STYLES
from app.data import coverage_frame, coverage_summary_frame
from app.state import require_snapshot

snapshot = require_snapshot()
snapshot_id = snapshot["snapshot_id"]

st.info(
    "**Pending** files are eligible and waiting for analysis stages (Python AST, security, "
    "complexity). **Skipped** files will not be analyzed, and the reason is shown. A clean "
    "report only means something for files that were actually analyzed.",
    title="Not analyzed is not the same as no issues",
    icon=":material/info:",
)

files = coverage_frame(snapshot_id)
summary = coverage_summary_frame(snapshot_id)

with st.container(horizontal=True):
    for status in ("pending", "skipped", "analyzed"):
        label = STATUS_STYLES[status][0]
        count = int(summary.loc[summary["status"] == status, "files"].sum())
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
    column_order=["relative_path", "status", "skip_reason", "size_bytes"],
    column_config={
        "relative_path": st.column_config.TextColumn("Path", width="large"),
        "status": st.column_config.TextColumn("Status"),
        "skip_reason": st.column_config.TextColumn("Skip reason"),
        "size_bytes": st.column_config.NumberColumn("Size", format="bytes"),
    },
    key="coverage_table",
    alt="Coverage ledger: every file with its analysis status and skip reason",
)

st.caption(
    "Per-stage columns (Python AST, security, complexity) will appear here as each "
    "analysis stage lands in Milestones 2–4."
)
