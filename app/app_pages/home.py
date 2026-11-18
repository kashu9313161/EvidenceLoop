import streamlit as st

from app.components import analysis_metrics, run_analysis
from app.data import snapshots_frame
from app.state import set_active_snapshot
from ingestion.service import IngestionError, ingest_github_repository, ingest_zip_bytes
from storage.database import get_latest_analysis, get_snapshot

st.caption(
    "EvidenceLoop turns a repository into an immutable snapshot pinned to an exact commit. "
    "Nothing from the repository is executed or installed."
)

source = st.segmented_control(
    "Source",
    ["GitHub", "ZIP upload"],
    default="GitHub",
    required=True,
    key="home_source",
)

with st.form("ingest_form"):
    if source == "GitHub":
        url = st.text_input(
            "Public GitHub repository URL",
            placeholder="https://github.com/owner/repository",
            key="home_url",
        )
        upload = None
    else:
        url = ""
        upload = st.file_uploader("Repository ZIP", type=["zip"], key="home_zip")

    submitted = st.form_submit_button(
        "Ingest repository", type="primary", icon=":material/download:", key="home_submit"
    )

if submitted:
    if source == "GitHub" and not url.strip():
        st.warning("Enter a GitHub repository URL.", icon=":material/warning:")
    elif source == "ZIP upload" and upload is None:
        st.warning("Choose a ZIP file to upload.", icon=":material/warning:")
    else:
        with st.status("Ingesting repository…", expanded=True) as status:
            try:
                if source == "GitHub":
                    status.write("Resolving the default branch to a commit and downloading…")
                    result = ingest_github_repository(url.strip())
                else:
                    status.write("Validating the archive…")
                    name = upload.name.rsplit(".", 1)[0]
                    result = ingest_zip_bytes(upload.getvalue(), name)
            except IngestionError as exc:
                status.update(label="Ingestion rejected", state="error")
                st.session_state.pop("last_ingest", None)
                st.error(str(exc), title="Could not ingest", icon=":material/gpp_bad:")
            else:
                status.update(label="Analyzing code…")
                analysis = run_analysis(result["snapshot_id"], status)
                if analysis is None:
                    status.update(label="Snapshot ready, analysis failed", state="error")
                else:
                    status.update(label="Snapshot analyzed", state="complete", expanded=False)
                set_active_snapshot(result["snapshot_id"])
                st.session_state["last_ingest"] = result
                st.rerun()

result = st.session_state.get("last_ingest")
if result:
    with st.container(border=True, key="ingest_result"):
        if result.get("already_downloaded"):
            st.info(
                "This exact snapshot was ingested before, so the stored copy is reused.",
                title="Already ingested",
                icon=":material/history:",
            )
        st.markdown("**Snapshot**")
        st.code(result["snapshot_id"], language=None)

        # Re-ingests return no counts; read them from the stored snapshot.
        counts = result if "total_files" in result else get_snapshot(result["snapshot_id"])
        if counts is not None:
            with st.container(horizontal=True):
                st.metric("Files", counts["total_files"], border=True)
                st.metric("Eligible for analysis", counts["eligible_files"], border=True)
                st.metric("Skipped", counts["skipped_files"], border=True)

        analysis = get_latest_analysis(result["snapshot_id"])
        if analysis is not None:
            analysis_metrics(analysis)

        with st.container(horizontal=True):
            st.page_link(
                "app_pages/overview.py", label="Open overview", icon=":material/inventory_2:"
            )
            st.page_link(
                "app_pages/coverage.py", label="Open coverage ledger", icon=":material/checklist:"
            )

st.subheader("Snapshots", anchor=False)
snapshots = snapshots_frame()

if snapshots.empty:
    st.caption("Nothing ingested yet.")
else:
    st.dataframe(
        snapshots,
        hide_index=True,
        column_config={
            "Files": st.column_config.NumberColumn(format="%d"),
            "Snapshot ID": st.column_config.TextColumn(width="medium"),
        },
        alt="Table of all ingested snapshots with file counts",
    )

with st.expander("How ingestion keeps you safe", icon=":material/shield:"):
    st.markdown(
        "- GitHub URLs resolve to an exact **commit SHA**, so results never drift as the "
        "repo changes.\n"
        "- Every archive entry is validated **before** anything is written: path traversal, "
        "symlinks, encrypted entries, duplicate paths, size, file count and "
        "compression-ratio limits.\n"
        "- Extraction is **atomic**: a failure leaves no partial snapshot.\n"
        "- Repository code is **never executed** and dependencies are never installed."
    )
