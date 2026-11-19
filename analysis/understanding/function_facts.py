"""Pure helpers for function cards: call-site snippets, the example, and the "why" check.

No I/O here: callers pass in parsed trees and lines, so the rules are easy to test.
"""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass

EXAMPLE_MAX_LINES = 6
SNIPPET_MAX_LINES = 6


@dataclass
class CallSite:
    caller_id: str
    caller_name: str  # qualified name of the calling function, "<module>" for top level
    path: str
    line: int
    tier: str
    is_test: bool
    start: int  # the statement around the call
    end: int
    text: str  # that statement, dedented (at most SNIPPET_MAX_LINES lines)

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def statement_span(tree: ast.AST, line: int) -> tuple[int, int]:
    """Lines of the smallest statement around `line`. For a compound statement (`if f(x):`,
    `with open(p) as h:`) that is only its header, not the whole block."""
    best: tuple[int, int] | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        start, end = node.lineno, node.end_lineno or node.lineno
        if not start <= line <= end:
            continue
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.AST):
            header_end = body[0].lineno - 1
            if line > header_end:
                continue  # the call is inside the block: a statement there is closer
            end = max(start, header_end)
        if best is None or end - start < best[1] - best[0]:
            best = (start, end)
    return best or (line, line)


def snippet(lines: list[str], start: int, end: int) -> str:
    stop = min(end, start + SNIPPET_MAX_LINES - 1, len(lines))
    return textwrap.dedent("\n".join(lines[start - 1 : stop])).strip("\n")


def choose_example(sites: list[CallSite]) -> CallSite | None:
    """A call site to show as an example: from a test when there is one (tests show how a
    function is meant to be used), otherwise any caller; the shortest that fits in
    EXAMPLE_MAX_LINES lines."""
    fitting = [s for s in sites if s.length <= EXAMPLE_MAX_LINES and s.text.strip()]
    if not fitting:
        return None
    return min(fitting, key=lambda s: (not s.is_test, s.length, s.path, s.line))


_NAMED = re.compile(r"`([A-Za-z_][\w.]*)(\(\))?`|\b([A-Za-z_][\w.]*)(\(\))")


def named_functions(text: str) -> list[tuple[str, bool]]:
    """Names a sentence mentions in code style: (`name` or name(), written as a call?)."""
    return [(a or c, bool(b or d)) for a, b, c, d in _NAMED.findall(text)]


def check_why(text: str, allowed: set[str], known: set[str]) -> bool:
    """False when the text names a function that isn't a real caller (or the function itself,
    its callees or its file): the model invented a caller. A name counts as a function when
    it is written as a call (`checkout()`) or is a function or class in the repository, so a
    mention of `dict` or `config.py` passes."""
    allowed_short = {name.rsplit(".", 1)[-1] for name in allowed} | allowed
    for name, called in named_functions(text):
        short = name.rsplit(".", 1)[-1]
        if name in allowed_short or short in allowed_short:
            continue
        if called or short in known:
            return False
    return True
