"""Decide whether a question is a lookup (answered from the index, no AI) or analytical.

Lookups name one thing and ask where it is, what calls it, or what uses it:
  "where is load_data", "what calls place_order", "which files use pandas".
Anything else, including "where is the config loaded", is analytical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

IDENTIFIER = re.compile(r"^[A-Za-z_][\w.\-/]*$")

_THING = r"(?:the\s+)?(?:function|method|class|module|file|package|library|symbol)?\s*"

# (lookup, pattern). The target is the named group "t"; order matters (callers before where).
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("callers", re.compile(
        rf"^(?:what|who|which\s+(?:functions?|methods?|files?|code))\s+(?:calls?|invokes?)\s+"
        rf"{_THING}(?P<t>\S+)$", re.I)),
    ("callers", re.compile(
        rf"^(?:where\s+is|where's|where\s+are)\s+{_THING}(?P<t>\S+)\s+(?:called|invoked)$", re.I)),
    ("callers", re.compile(rf"^(?:callers|call\s+sites)\s+(?:of|for)\s+{_THING}(?P<t>\S+)$",
                           re.I)),
    ("users", re.compile(
        rf"^(?:which|what)\s+(?:files?|modules?|code)\s+(?:use|uses|import|imports|"
        rf"depends?\s+on|needs?|requires?)\s+{_THING}(?P<t>\S+)$", re.I)),
    ("users", re.compile(
        rf"^(?:what|who)\s+(?:uses|imports|depends\s+on|requires)\s+{_THING}(?P<t>\S+)$", re.I)),
    ("users", re.compile(
        rf"^(?:where\s+is|where's|where\s+are)\s+{_THING}(?P<t>\S+)\s+(?:used|imported)$", re.I)),
    ("where", re.compile(
        rf"^(?:where\s+is|where's|where\s+are|where\s+can\s+i\s+find|find|locate|show\s+me|"
        rf"go\s+to)\s+(?:the\s+)?(?:definition\s+of\s+)?{_THING}(?P<t>\S+)"
        rf"(?:\s+(?:defined|declared|implemented|located))?$", re.I)),
    ("where", re.compile(rf"^(?:definition\s+of|define)\s+{_THING}(?P<t>\S+)$", re.I)),
]

LOOKUP_LABELS = {
    "where": "where it is defined",
    "callers": "what calls it",
    "users": "what uses it",
}


@dataclass(frozen=True)
class Route:
    kind: str  # "lookup" or "analytical"
    lookup: str | None = None  # "where", "callers" or "users"
    target: str | None = None
    reason: str = ""


def clean_target(raw: str) -> str:
    target = raw.strip().strip("`'\"").strip()
    target = target.removesuffix("()")
    return target.rstrip(",;:")


def route_question(question: str) -> Route:
    text = " ".join(question.strip().split()).rstrip("?.! ")
    for lookup, pattern in PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        target = clean_target(match.group("t"))
        if target and IDENTIFIER.match(target):
            return Route("lookup", lookup, target,
                         f"A lookup ({LOOKUP_LABELS[lookup]}), answered from the index.")
    return Route("analytical", reason="An open question, answered by AI from cited code.")
