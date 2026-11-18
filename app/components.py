"""Reusable UI pieces. Later milestones plug real data into these."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

# status -> (label, badge colour, icon)
STATUS_STYLES: dict[str, tuple[str, str, str]] = {
    # coverage ledger
    "pending": ("Pending analysis", "gray", ":material/schedule:"),
    "skipped": ("Skipped", "orange", ":material/block:"),
    "analyzed": ("Analyzed", "green", ":material/check_circle:"),
    "failed": ("Analysis failed", "red", ":material/error:"),
    # claim verification
    "verified": ("Verified", "green", ":material/verified:"),
    "insufficient_evidence": ("Insufficient evidence", "orange", ":material/help:"),
    "rejected": ("Rejected", "gray", ":material/cancel:"),
}


@dataclass(frozen=True)
class EvidenceRef:
    """One piece of evidence behind a claim, pinned to a snapshot."""

    path: str
    start_line: int
    end_line: int
    detector: str
    snapshot_id: str


def status_pill(status: str) -> None:
    label, color, icon = STATUS_STYLES.get(status, (status, "gray", ":material/circle:"))
    st.badge(label, color=color, icon=icon)


def read_source_range(
    snapshot_root: str | Path, path: str, start: int, end: int, context: int = 2
) -> tuple[int, list[str]] | None:
    """Return (first_line_number, lines) from a snapshot file, or None if unavailable.

    The path is resolved inside the snapshot root so evidence can never point
    outside the analyzed files.
    """
    root = Path(snapshot_root).resolve()
    target = (root / path).resolve()

    if not target.is_relative_to(root) or not target.is_file():
        return None

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    first = max(start - context, 1)
    last = min(end + context, len(lines))
    return first, lines[first - 1 : last]


def code_view(snapshot_root: str | Path, path: str, start: int, end: int) -> None:
    excerpt = read_source_range(snapshot_root, path, start, end)

    if excerpt is None:
        st.warning(f"`{path}` is not available in this snapshot.", icon=":material/warning:")
        return

    first, lines = excerpt
    width = len(str(first + len(lines)))
    numbered = "\n".join(
        f"{'>' if start <= first + i <= end else ' '}{first + i:>{width}} │ {line}"
        for i, line in enumerate(lines)
    )
    st.code(numbered, language="python" if path.endswith(".py") else None)


def evidence_card(
    claim: str,
    status: str,
    evidence: Sequence[EvidenceRef],
    snapshot_root: str | Path | None = None,
) -> None:
    """A claim with its verification status and the evidence behind it."""
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            status_pill(status)
            st.markdown(f"**{claim}**")

        if not evidence:
            st.caption("No evidence attached, so this claim cannot be verified.")
            return

        for ref in evidence:
            title = f"`{ref.path}:{ref.start_line}–{ref.end_line}` · {ref.detector}"
            with st.expander(title, icon=":material/description:"):
                st.caption(f"Snapshot `{ref.snapshot_id}`")
                if snapshot_root is not None:
                    code_view(snapshot_root, ref.path, ref.start_line, ref.end_line)


def coming_soon(milestone: str, summary: str, planned: Sequence[str]) -> None:
    """Honest placeholder: says what the page will show and when."""
    st.info(summary, title=f"Arrives in {milestone}", icon=":material/upcoming:")
    with st.container(border=True):
        st.markdown("**What this page will show**")
        st.markdown("\n".join(f"- {item}" for item in planned))
