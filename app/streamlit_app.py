"""EvidenceLoop Streamlit entry point.

Run from the repository root:  streamlit run app/streamlit_app.py
"""

import sys
from pathlib import Path

# `streamlit run app/streamlit_app.py` puts app/ on sys.path, not the repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app.state import snapshot_picker, theme_hint  # noqa: E402

st.set_page_config(
    page_title="EvidenceLoop",
    page_icon=":material/fact_check:",
    layout="wide",
)

page = st.navigation(
    {
        "Repository": [
            st.Page(
                "app_pages/home.py",
                title="Add repository",
                icon=":material/add_circle:",
                default=True,
                help="Ingest a public GitHub repository or a ZIP upload into an immutable "
                "snapshot. Lists every snapshot ingested so far.",
            ),
            st.Page(
                "app_pages/overview.py",
                title="Snapshot overview",
                icon=":material/inventory_2:",
                help="Identity of the active snapshot: repository, commit SHA, archive hash "
                "and file breakdown.",
            ),
            st.Page(
                "app_pages/coverage.py",
                title="Coverage ledger",
                icon=":material/checklist:",
                help="Every file in the snapshot and whether it is waiting for analysis or "
                "was skipped, and why. Not analyzed is not the same as no issues.",
            ),
        ],
        "Analyze": [
            st.Page(
                "app_pages/understand.py",
                title="Understand",
                icon=":material/travel_explore:",
                help="Find where things live in the code: symbols, routes and their "
                "relationships, each with file and line evidence. Coming in M2–M5.",
            ),
            st.Page(
                "app_pages/investigate.py",
                title="Investigate",
                icon=":material/search_insights:",
                help="Security, complexity, dependency and history signals organised into "
                "verified findings. Coming in M4–M5.",
            ),
            st.Page(
                "app_pages/improve.py",
                title="Improve",
                icon=":material/construction:",
                help="Recommendations built only from verified findings, with regression "
                "risk and suggested tests. Coming in M5.",
            ),
            st.Page(
                "app_pages/module_map.py",
                title="Graph",
                icon=":material/hub:",
                help="Obsidian-style graph of every file and connection, opened as soon as "
                "analysis finishes. Coming in M4.",
            ),
        ],
        "Share": [
            st.Page(
                "app_pages/export.py",
                title="Export",
                icon=":material/ios_share:",
                help="Download the snapshot's knowledge as an Obsidian vault tied to its "
                "commit SHA. Coming in M8.",
            ),
        ],
    },
    expanded=True,
)

with st.sidebar:
    snapshot_picker()
    st.divider()
    theme_hint()

st.title(page.title, anchor=False, icon=page.icon)
page.run()
