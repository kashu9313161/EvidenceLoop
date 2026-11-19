"""Lookups answered from the index with no AI: where X is defined, what calls X, what uses X.

Each hit becomes a claim citing one evidence item, so lookups and AI answers look the same in
the UI and go through the same citation checks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from retrieval.evidence import EvidenceItem, make_item
from retrieval.source import SnapshotFiles
from storage.database import get_connection, like_literal

MAX_HITS = 20


@dataclass
class LookupResult:
    summary: str
    claims: list[dict] = field(default_factory=list)  # {"text", "evidence_ids"}
    evidence: list[EvidenceItem] = field(default_factory=list)
    more: int = 0

    def add(self, item: EvidenceItem | None, text: str) -> None:
        if item is None:
            return
        self.evidence.append(item)
        self.claims.append({"text": text, "evidence_ids": [item.id]})


def _lines(start: int, end: int) -> str:
    return f"line {start}" if start == end else f"lines {start}–{end}"


def _module_names(path: str) -> set[str]:
    """Names a file is imported by: its stem, and dotted module paths for Python."""
    pure = PurePosixPath(path)
    names = {pure.stem, pure.name}
    parts = list(pure.with_suffix("").parts)
    if parts and parts[-1] in {"__init__", "index"}:
        parts = parts[:-1]
    for i in range(len(parts)):
        names.add(".".join(parts[i:]))
    return {n for n in names if n}


def find_files(report: dict, target: str) -> list[str]:
    target = target.strip("/")
    exact = [f["path"] for f in report["files"] if f["path"] == target]
    if exact:
        return exact
    return sorted(f["path"] for f in report["files"]
                  if f["path"].endswith("/" + target) or target in _module_names(f["path"]))


def where(snapshot_id: str, target: str, report: dict, files: SnapshotFiles) -> LookupResult:
    short = target.rsplit(".", 1)[-1]
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT relative_path, kind, qualified_name, parent, start_line, end_line, decorators "
            "FROM symbols WHERE snapshot_id = ? AND kind != 'module' AND (name = ? OR "
            "qualified_name = ?) ORDER BY relative_path, start_line",
            (snapshot_id, short, target)).fetchall()
    rows = [r for r in rows if r["qualified_name"] == target or r["qualified_name"]
            .endswith(target) or "." not in target]
    # `@overload` stubs are type signatures, not code: leave them out when the real definition
    # is there, so the hits lead to the implementation.
    real = {(r["relative_path"], r["qualified_name"]) for r in rows if not _is_overload(r)}
    stubs = [r for r in rows
             if _is_overload(r) and (r["relative_path"], r["qualified_name"]) in real]
    rows = [r for r in rows if r not in stubs]
    tests = {f["path"] for f in report["files"] if f["is_test"]}
    rows.sort(key=lambda r: (r["relative_path"] in tests, r["relative_path"], r["start_line"]))
    result = LookupResult("")
    for row in rows[:MAX_HITS]:
        kind = row["kind"]
        what = f"{kind} of {row['parent']}" if row["parent"] and kind == "method" else kind
        item = make_item(files, f"E{len(result.evidence) + 1}", row["relative_path"],
                         row["start_line"], row["end_line"], "code", row["qualified_name"])
        result.add(item, f"`{row['qualified_name']}` ({what}) is defined in "
                         f"{row['relative_path']}, "
                         f"{_lines(row['start_line'], row['end_line'])}.")
    for path in find_files(report, target)[:MAX_HITS - len(result.claims)]:
        item = make_item(files, f"E{len(result.evidence) + 1}", path, 1, 12, "code", path)
        result.add(item, f"The file {path} matches `{target}`.")
    result.more = max(len(rows) - MAX_HITS, 0)
    file_hits = len(result.claims) - min(len(rows), MAX_HITS)
    n = len(rows) + file_hits  # every definition (not only those shown) plus matching files
    overloads = f" (plus {len(stubs)} type overload{'s' if len(stubs) != 1 else ''})" \
        if stubs else ""
    result.summary = (f"`{target}` is defined in {n} place{'s' if n != 1 else ''}{overloads}."
                      if rows
                      else f"`{target}` matches {n} file{'s' if n != 1 else ''}." if n
                      else f"Nothing named `{target}` was found in this repository.")
    return result


def _is_overload(row) -> bool:
    try:
        decorators = json.loads(row["decorators"] or "[]")
    except (TypeError, ValueError):
        return False
    return any(str(d).rsplit(".", 1)[-1] == "overload" for d in decorators)


def callers(snapshot_id: str, target: str, report: dict, files: SnapshotFiles) -> LookupResult:
    short = target.rsplit(".", 1)[-1]
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT relative_path, callee, line, enclosing_symbol FROM calls WHERE snapshot_id = ? "
            "AND (callee = ? OR callee = ? OR callee LIKE ? ESCAPE '\\') "
            "ORDER BY relative_path, line",
            (snapshot_id, target, short, f"%.{like_literal(short)}")).fetchall()
    result = LookupResult("")
    for row in rows[:MAX_HITS]:
        where_from = (f"`{row['enclosing_symbol']}`" if row["enclosing_symbol"]
                      else "module level")
        item = make_item(files, f"E{len(result.evidence) + 1}", row["relative_path"],
                         row["line"], row["line"], "code",
                         f"call to {row['callee']}")
        result.add(item, f"{row['relative_path']} calls `{row['callee']}` from {where_from}, "
                         f"line {row['line']}.")
    result.more = max(len(rows) - MAX_HITS, 0)
    callers_count = len({r["relative_path"] for r in rows})
    result.summary = (
        f"`{target}` is called {len(rows)} time{'s' if len(rows) != 1 else ''} in "
        f"{callers_count} file{'s' if callers_count != 1 else ''}." if rows
        else f"No call to `{target}` was found. Calls made dynamically (through variables or "
             f"callbacks) aren't indexed.")
    return result


def users(snapshot_id: str, target: str, report: dict, files: SnapshotFiles) -> LookupResult:
    result = LookupResult("")
    matched = find_files(report, target)
    if matched:
        chosen = set(matched)
        edges = [e for e in report["graph"]["edges"]
                 if e["target"] in chosen and not e.get("type_only") and e["lines"]]
        for edge in sorted(edges, key=lambda e: (e["source"], min(e["lines"])))[:MAX_HITS]:
            line = min(edge["lines"])
            item = make_item(files, f"E{len(result.evidence) + 1}", edge["source"], line,
                             max(edge["lines"]), "import",
                             f"{edge['source']} imports {edge['target']}")
            result.add(item, f"{edge['source']} imports {edge['target']} "
                             f"({_lines(line, max(edge['lines']))}).")
        result.more = max(len(edges) - MAX_HITS, 0)
        n = len({e["source"] for e in edges})
        result.summary = (f"{n} file{'s' if n != 1 else ''} use{'s' if n == 1 else ''} "
                          f"{', '.join(matched[:3])}." if edges
                          else f"No other file imports {', '.join(matched[:3])}.")
        return result

    with get_connection() as connection:
        rows = connection.execute(
            "SELECT relative_path, module, name, line FROM imports WHERE snapshot_id = ? AND "
            "(module = ? OR module LIKE ? ESCAPE '\\' OR name = ?) ORDER BY relative_path, line",
            (snapshot_id, target, f"{like_literal(target)}.%", target)).fetchall()
    seen: set[str] = set()
    for row in rows:
        if row["relative_path"] in seen or len(result.claims) >= MAX_HITS:
            continue
        seen.add(row["relative_path"])
        imported = f"{row['module']}.{row['name']}" if row["name"] else row["module"]
        item = make_item(files, f"E{len(result.evidence) + 1}", row["relative_path"],
                         row["line"], row["line"], "import", f"imports {imported}")
        result.add(item, f"{row['relative_path']} imports `{imported}` (line {row['line']}).")
    total = len({r["relative_path"] for r in rows})
    result.more = max(total - len(result.claims), 0)
    result.summary = (f"{total} file{'s' if total != 1 else ''} import{'s' if total == 1 else ''} "
                      f"`{target}`." if rows
                      else f"No file imports anything named `{target}`.")
    return result


LOOKUPS = {"where": where, "callers": callers, "users": users}
