import streamlit as st

from app.components import EvidenceRef, coming_soon, evidence_card
from app.state import require_snapshot

snapshot = require_snapshot()

coming_soon(
    "Milestones 4–5",
    "Investigate collects engineering signals and organises them into findings. Each "
    "finding is checked against the snapshot before it is shown.",
    [
        "Security observations from Bandit, with secrets from Gitleaks always redacted",
        "Complexity measurements from Radon, reported as raw numbers, not verdicts",
        "Known-vulnerable dependencies from OSV, with package, version and advisory",
        "Change history for the files involved",
        "Finding cards that separate observation, evidence, potential impact and "
        "verification status",
    ],
)

st.caption("Example finding (illustrative, not from your repository):")
evidence_card(
    "check_user() has cyclomatic complexity 14",
    "insufficient_evidence",
    [EvidenceRef("src/example/auth/service.py", 40, 92, "radon cc", snapshot["snapshot_id"])],
)
