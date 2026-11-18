import streamlit as st

from app.components import coming_soon
from app.state import require_snapshot

require_snapshot()

coming_soon(
    "Milestone 5",
    "Improve turns verified findings into recommendations that help you decide. It never "
    "prescribes a change without showing the evidence behind it.",
    [
        "Finding and the evidence behind it",
        "Location in the snapshot",
        "Potential impact",
        "Suggested change, plus one alternative",
        "Regression risk: what could break",
        "Tests to write before changing anything",
    ],
)

st.caption("There is no overall health score. Measurements are shown side by side instead.")
