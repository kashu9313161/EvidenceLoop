import streamlit as st

from app.components import EvidenceRef, coming_soon, evidence_card
from app.state import require_snapshot

snapshot = require_snapshot()

coming_soon(
    "Milestones 2–5",
    "Understand answers “where is X?” questions by deterministic lookup over the snapshot's "
    "full-text index and code graph. No LLM is needed to find code.",
    [
        "A search box over symbols, routes and file contents (SQLite FTS5)",
        "Results as `file:start–end` with the enclosing function or class",
        "A read-only code viewer with the matched lines highlighted",
        "Callers, callees and imports for the selected symbol, each marked confirmed or "
        "inferred",
    ],
)

st.text_input(
    "Ask where something lives",
    placeholder="Where is the login endpoint?",
    disabled=True,
    key="understand_query",
)

st.caption("Example of how an answer will look (illustrative, not from your repository):")
evidence_card(
    "The login endpoint is defined in login_user()",
    "verified",
    [EvidenceRef("src/example/routes/auth.py", 25, 48, "python-ast", snapshot["snapshot_id"])],
)
