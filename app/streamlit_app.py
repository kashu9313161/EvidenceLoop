 
import streamlit as st

st.set_page_config(
    page_title="EvidenceLoop",
    page_icon="🔍",
    layout="wide",
)

st.title("EvidenceLoop")
st.caption("Evidence-backed Codebase Intelligence")

st.sidebar.title("Navigation")

mode = st.sidebar.radio(
    "Select Mode",
    ["Understand", "Investigate", "Improve"],
)

st.subheader("Repository Input")

repo_url = st.text_input(
    "Public GitHub Repository URL",
    placeholder="https://github.com/owner/repository",
)

uploaded_file = st.file_uploader(
    "Or upload a repository ZIP",
    type=["zip"],
)

if st.button("Analyze Repository", type="primary"):
    if repo_url or uploaded_file:
        st.info(
            "Repository ingestion will be implemented "
            "in the next milestone."
        )
    else:
        st.warning("Provide a repository URL or ZIP file.")

st.divider()
st.subheader(mode)

st.info(
    "EvidenceLoop is running locally. "
    "Analysis features are under development."
)
