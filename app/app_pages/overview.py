import streamlit as st

from app.components import analysis_metrics, code_view, run_analysis
from app.data import coverage_frame, imports_frame, symbols_frame
from app.state import require_snapshot
from storage.database import get_connection, get_latest_analysis

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

st.subheader("Code analysis", anchor=False)
analysis = get_latest_analysis(snapshot_id)

if analysis is None:
    st.info(
        "This snapshot hasn't been analyzed yet. Analysis reads every eligible file and "
        "finds its classes, functions, imports and calls, with exact line ranges.",
        icon=":material/pending:",
    )
else:
    analysis_metrics(analysis)
    st.caption(
        f"Analyzer `{analysis['analyzer_version']}` · finished {analysis['finished_at']} UTC · "
        f"{analysis['seconds']} s"
    )

if st.button(
    "Analyze code" if analysis is None else "Re-run analysis",
    type="primary" if analysis is None else "secondary",
    icon=":material/play_arrow:" if analysis is None else ":material/refresh:",
    key="overview_analyze",
):
    with st.status("Analyzing code…", expanded=True) as status:
        if run_analysis(snapshot_id, status) is not None:
            status.update(label="Analysis complete", state="complete", expanded=False)
            st.rerun()
        status.update(label="Analysis failed", state="error")

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
        .agg(
            files=("relative_path", "size"),
            eligible=("status", lambda s: (s != "skipped").sum()),
        )
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

if analysis is not None:
    st.subheader("Code structure", anchor=False)
    symbols = symbols_frame(snapshot_id)
    code_files = sorted(symbols["relative_path"].unique())

    if not code_files:
        st.caption("No Python, JavaScript or TypeScript files in this snapshot.")
    else:
        per_file = (
            symbols[symbols["kind"] != "module"]
            .groupby("relative_path")
            .size()
            .reindex(code_files, fill_value=0)
        )
        selected = st.selectbox(
            "File",
            code_files,
            # Open on the richest file rather than e.g. docs/conf.py.
            index=code_files.index(per_file.idxmax()),
            format_func=lambda path: f"{path}  ({per_file[path]} symbols)",
            key=f"overview_file_{snapshot_id}",
        )
        file_symbols = symbols[
            (symbols["relative_path"] == selected) & (symbols["kind"] != "module")
        ]
        file_imports = imports_frame(snapshot_id)
        file_imports = file_imports[file_imports["relative_path"] == selected]

        left, right = st.columns([3, 2])
        with left:
            st.markdown("**Symbols**")
            event = st.dataframe(
                file_symbols.assign(lines=file_symbols["start_line"].astype(str) + "–"
                                    + file_symbols["end_line"].astype(str)),
                hide_index=True,
                column_order=["kind", "lines", "qualified_name", "has_docstring"],
                column_config={
                    "kind": st.column_config.TextColumn("Kind", width="small"),
                    "qualified_name": st.column_config.TextColumn("Name", width="large"),
                    "lines": st.column_config.TextColumn("Lines", width="small"),
                    "has_docstring": st.column_config.CheckboxColumn("Docstring"),
                },
                on_select="rerun",
                selection_mode="single-row",
                key=f"overview_symbols_{snapshot_id}_{selected}",
                alt="Classes, functions and methods in the selected file with line ranges",
            )
        with right:
            st.markdown("**Imports**")
            if file_imports.empty:
                st.caption("No imports.")
            else:
                st.dataframe(
                    file_imports.assign(
                        target=file_imports["level"].map(lambda n: "." * n)
                        + file_imports["module"],
                        name=file_imports["name"].fillna(""),
                        alias=file_imports["alias"].fillna(""),
                    ),
                    hide_index=True,
                    column_order=["line", "target", "name", "alias"],
                    column_config={
                        "line": st.column_config.NumberColumn("Line", format="%d"),
                        "target": "Module",
                        "name": "Name",
                        "alias": "As",
                    },
                    alt="Imports in the selected file with line numbers",
                )

        rows = event.selection.rows
        if rows:
            picked = file_symbols.iloc[rows[0]]
            st.markdown(f"**`{picked['qualified_name']}`** · lines "
                        f"{picked['start_line']}–{picked['end_line']}")
            code_view(snapshot["root_path"], selected, int(picked["start_line"]),
                      int(picked["end_line"]))
        else:
            st.caption("Select a symbol to see its code.")
