"""Build the Gate A ground-truth set: 40 claim/evidence pairs to label by hand.

    python -m eval.gate_a.build_cases

Writes eval/gate_a/cases.json and eval/gate_a/LABEL_SHEET.md. Every snippet is read off the
snapshot on disk (data/repositories, found through the DB), never quoted from memory.

Buckets (the plan's Gate A mix), named with the labels run.py scores:
- supports (20): curated.SUPPORTED, topped up from stored reports, but only from rules whose
  cited lines contain the proof (PROOF_RULES) and confirmed import edges, with spans of 2+ lines;
- not_supported (10): a curated.MOVED claim with its evidence moved to an unrelated function
  body, rotating the evidence across repositories;
- related_not_supporting (5) and ambiguous (5): curated, each with a note saying why.

No two cases share or overlap a snippet. Rebuilding keeps any labels already in cases.json for
cases that come out identical (same snapshot, claim and span).
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from core.textio import decode_text
from eval.gate_a import curated
from evaluation.gate_a.metrics import LABELS
from storage import database

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "cases.json"
SHEET_PATH = HERE / "LABEL_SHEET.md"
SEED = 20261119
TARGET = {"supports": 20, "not_supported": 10, "related_not_supporting": 5, "ambiguous": 5}
assert set(TARGET) == set(LABELS)

# Report rules whose cited lines are the proof. Everything else is left out on purpose:
# untested_hub, unpinned_dependencies, utf16_manifest and god_class cite one line that locates
# a finding without evidencing it ("3 files depend on this" isn't visible in the depended-on
# file), so a "supports" case built from them would be mislabelled by construction.
PROOF_RULES = ("swallowed_exception", "complex_function")
MIN_SPAN = 2
# Function bodies used as unrelated evidence: long enough to read, short enough to label.
MOVED_MIN_LINES, MOVED_MAX_LINES = 4, 20


class BuildError(SystemExit):
    """A build that can't produce the exact set exits non-zero with the reason."""


@dataclass(frozen=True)
class Span:
    snapshot: str
    path: str
    start: int
    end: int

    def overlaps(self, other: Span) -> bool:
        return (self.snapshot == other.snapshot and self.path == other.path
                and self.start <= other.end and other.start <= self.end)


# --- reading snippets -------------------------------------------------------------------------

def snapshot_roots(snapshots: Iterable[str] = curated.SNAPSHOTS) -> dict[str, Path]:
    """Each snapshot's directory on disk, via the DB and storage.database.snapshot_root."""
    roots: dict[str, Path] = {}
    with database.get_connection() as conn:
        for sid in snapshots:
            row = conn.execute("SELECT * FROM snapshots WHERE snapshot_id = ?", (sid,)).fetchone()
            if row is None:
                raise BuildError(f"Snapshot {sid} is not in the database. Analyse it first.")
            root = database.snapshot_root(row)
            if root is None:
                raise BuildError(f"Snapshot {sid} has no files on disk.")
            roots[sid] = root
    return roots


def file_lines(root: Path, path: str) -> list[str]:
    root = root.resolve()
    full = (root / path).resolve()
    if not full.is_relative_to(root) or not full.is_file():
        raise BuildError(f"{path} is not a file in {root}")
    text = decode_text(full.read_bytes())
    if text is None:
        raise BuildError(f"{path} is binary")
    return text.splitlines()


def read_span(root: Path, path: str, start: int, end: int) -> str:
    """Lines start..end (1-based, inclusive) exactly as they are on disk."""
    lines = file_lines(root, path)
    if not 1 <= start <= end <= len(lines):
        raise BuildError(f"{path}:{start}-{end} is outside the file ({len(lines)} lines)")
    return "\n".join(lines[start - 1:end])


def repo_name(snapshot: str) -> str:
    return snapshot.split(":", 1)[1].split("@", 1)[0]


# --- report top-ups ---------------------------------------------------------------------------

def _module_of(path: str) -> str:
    """src/itsdangerous/signer.py -> itsdangerous.signer; src/markupsafe/__init__.py ->
    markupsafe."""
    parts = path.removesuffix(".py").split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def report_candidates(snapshot: str, root: Path) -> list[tuple[Span, str, str]]:
    """(span, claim, note) for findings whose cited lines prove them, insights first."""
    out: list[tuple[Span, str, str]] = []
    with database.get_connection() as conn:
        row = conn.execute("SELECT report FROM analysis_reports WHERE snapshot_id = ?",
                           (snapshot,)).fetchone()
        edges = conn.execute(
            "SELECT source_path, target_node_id, relationship, start_line, end_line, "
            "extraction_method FROM graph_edges WHERE snapshot_id = ? AND evidence_status = "
            "'confirmed' AND relationship IN ('imports', 'tests') AND start_line IS NOT NULL "
            "ORDER BY source_path, start_line", (snapshot,)).fetchall()
    report = json.loads(row["report"]) if row else {"insights": []}
    for insight in report.get("insights", []):
        if insight["rule"] not in PROOF_RULES:
            continue
        for ev in insight.get("evidence", []):
            start, end = ev["start"], ev["end"]
            if end - start + 1 < MIN_SPAN:
                continue
            name = ev["path"].rsplit("/", 1)[-1]
            note = (f"From the stored report: insight {insight['id']} ({insight['rule']}, "
                    f"{insight['confidence']}), which cites exactly these lines.")
            if insight["rule"] == "swallowed_exception":
                claim = f"In {name}, a handler catches Exception and silently does nothing."
            else:  # complex_function: only the length, which the span itself shows
                lines = end - start + 1
                if f"{lines} lines" not in insight["summary"]:
                    continue
                claim = f"{ev['label']} in {name} is {lines} lines long."
            out.append((Span(snapshot, ev["path"], start, end), claim, note))
    for edge in edges:
        start, end = edge["start_line"], edge["end_line"]
        if end - start + 1 < MIN_SPAN:
            continue
        module = _module_of(edge["target_node_id"].split("::", 1)[1])
        text = read_span(root, edge["source_path"], start, end)
        last = module.rsplit(".", 1)[-1]
        # Every line must be an import naming the module, or the lines don't show the edge.
        if not all(ln.lstrip().startswith(("from ", "import ")) and last in ln
                   for ln in text.splitlines() if ln.strip()):
            continue
        claim = f"{edge['source_path']} imports names from {module}."
        note = (f"From a confirmed '{edge['relationship']}' graph edge "
                f"({edge['extraction_method']}) whose lines are the import statements.")
        out.append((Span(snapshot, edge["source_path"], start, end), claim, note))
    return out


# --- unrelated function bodies ----------------------------------------------------------------

def _is_test_or_docs(path: str) -> bool:
    parts = path.lower().split("/")
    return (parts[0] == "docs" or any(p in ("tests", "test") for p in parts)
            or parts[-1].startswith("test_") or parts[-1] == "conftest.py")


def function_bodies(snapshot: str, root: Path) -> list[tuple[Span, str]]:
    """(span, qualified name) of every short, real function in non-test, non-docs files."""
    out: list[tuple[Span, str]] = []
    for file in sorted(root.rglob("*.py")):
        path = file.relative_to(root).as_posix()
        if _is_test_or_docs(path) or any(p.startswith(".") for p in path.split("/")):
            continue
        text = decode_text(file.read_bytes())
        try:
            tree = ast.parse(text or "")
        except SyntaxError:
            continue

        def visit(node: ast.AST, prefix: str, path: str = path) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    visit(child, f"{prefix}{child.name}.")
                elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    size = child.end_lineno - child.lineno + 1
                    body = [s for s in child.body
                            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
                    if body and MOVED_MIN_LINES <= size <= MOVED_MAX_LINES:
                        out.append((Span(snapshot, path, child.lineno, child.end_lineno),
                                    f"{prefix}{child.name}"))

        visit(tree, "")
    return out


# --- assembling ---------------------------------------------------------------------------------

class Builder:
    def __init__(self, roots: dict[str, Path]):
        self.roots = roots
        self.used: list[Span] = []
        self.cases: list[dict] = []

    def free(self, span: Span) -> bool:
        return not any(span.overlaps(u) for u in self.used)

    def add(self, span: Span, claim: str, kind: str, note: str) -> None:
        if not self.free(span):
            raise BuildError(f"{span.path}:{span.start}-{span.end} overlaps another case")
        self.used.append(span)
        self.cases.append({
            "id": None,
            "repo": repo_name(span.snapshot),
            "snapshot": span.snapshot,
            "claim": claim,
            "evidence": {"path": span.path, "start": span.start, "end": span.end,
                         "text": read_span(self.roots[span.snapshot], span.path, span.start,
                                           span.end)},
            "constructed_as": kind,
            "construction_note": note,
            "label": None,
            "labeller_note": "",
        })

    def count(self, kind: str) -> int:
        return sum(c["constructed_as"] == kind for c in self.cases)


def _span(c: curated.Claim) -> Span:
    return Span(c.snapshot, c.path, c.start, c.end)


def build(roots: dict[str, Path] | None = None, seed: int = SEED) -> list[dict]:
    roots = roots or snapshot_roots()
    b = Builder(roots)
    rng = random.Random(seed)

    # Curated groups first, so report and moved evidence can't collide with them.
    for c in curated.SUPPORTED:
        b.add(_span(c), c.claim, "supports",
              "Hand-written (curated.SUPPORTED): the lines alone show the claim.")
    for c in curated.RELATED_BUT_NOT:
        b.add(_span(c), c.claim, "related_not_supporting", c.note)
    for c in curated.AMBIGUOUS:
        b.add(_span(c), c.claim, "ambiguous", c.note)
    for c in curated.MOVED:  # check the real evidence still reads back, though it isn't used
        read_span(roots[c.snapshot], c.path, c.start, c.end)

    # Top up "supports" from the reports, round-robin over the repositories, preferring a file
    # no earlier top-up came from so the set doesn't fill with one file's imports.
    pools = [report_candidates(sid, roots[sid]) for sid in curated.SNAPSHOTS]
    topped: set[tuple[str, str]] = set()
    while b.count("supports") < TARGET["supports"] and any(pools):
        for pool in pools:
            pool[:] = [item for item in pool if b.free(item[0])]
            if not pool or b.count("supports") >= TARGET["supports"]:
                continue
            fresh = [item for item in pool if (item[0].snapshot, item[0].path) not in topped]
            span, claim, note = (fresh or pool)[0]
            pool.remove((span, claim, note))
            topped.add((span.snapshot, span.path))
            b.add(span, claim, "supports", note)

    # not_supported: keep the claim, move the evidence to an unrelated function body. The
    # evidence repository rotates; within the claim's own repository its file is excluded.
    bodies = {sid: function_bodies(sid, roots[sid]) for sid in curated.SNAPSHOTS}
    for sid in curated.SNAPSHOTS:
        rng.shuffle(bodies[sid])
    for i, c in enumerate(curated.MOVED):
        sid = curated.SNAPSHOTS[i % len(curated.SNAPSHOTS)]
        pick = next(((span, name) for span, name in bodies[sid]
                     if b.free(span) and not (sid == c.snapshot and span.path == c.path)), None)
        if pick is None:
            raise BuildError(f"No unused function body left in {repo_name(sid)} for: {c.claim}")
        span, name = pick
        where = "" if sid == c.snapshot else f" in {repo_name(c.snapshot)}"
        b.add(span, c.claim, "not_supported",
              f"Claim from curated.MOVED; its real evidence is {c.path}:{c.start}-{c.end}{where}. "
              f"Evidence moved to {name}() in {repo_name(sid)}, an unrelated function.")

    counts = {kind: b.count(kind) for kind in TARGET}
    if counts != TARGET:
        raise BuildError(f"Bucket counts are off: got {counts}, need {TARGET}.")

    rng.shuffle(b.cases)
    for n, case in enumerate(b.cases, 1):
        case["id"] = f"ga-{n:02d}"
    return b.cases


def _key(case: dict) -> tuple:
    e = case["evidence"]
    return case["snapshot"], case["claim"], e["path"], e["start"], e["end"]


def carry_labels(cases: list[dict], previous: Path) -> int:
    """Copy label and labeller_note from an earlier cases.json for identical cases."""
    if not previous.is_file():
        return 0
    old = {_key(c): c for c in json.loads(previous.read_text(encoding="utf-8"))["cases"]}
    carried = 0
    for case in cases:
        prior = old.get(_key(case))
        if prior and (prior.get("label") is not None or prior.get("labeller_note")):
            case["label"] = prior.get("label")
            case["labeller_note"] = prior.get("labeller_note", "")
            carried += 1
    return carried


# --- writing ------------------------------------------------------------------------------------

def _fence(text: str) -> str:
    fence = "```"
    while fence in text:
        fence += "`"
    return fence


def label_sheet(cases: list[dict]) -> str:
    out = [
        "# Gate A label sheet",
        "",
        "Generated by `python -m eval.gate_a.build_cases`; don't edit this file. Read each claim "
        "and its evidence, then set `label` (and optionally `labeller_note`) for that id in "
        "`cases.json`. Judge only from the lines shown: anything outside them doesn't count.",
        "",
        "Labels: `supports` · `not_supported` · `related_not_supporting` · `ambiguous` "
        "(see README.md). How each case was constructed is left out on purpose, so it can't "
        "steer you; `verify_cases.py` compares afterwards.",
        "",
    ]
    for case in cases:
        e = case["evidence"]
        width = len(str(e["end"]))
        numbered = "\n".join(f"{n:>{width}} | {line}" for n, line in
                             enumerate(e["text"].split("\n"), e["start"]))
        fence = _fence(numbered)
        lang = "python" if e["path"].endswith(".py") else ""
        out += [
            f"## {case['id']} · {case['repo']}",
            "",
            f"**Claim:** {case['claim']}",
            "",
            f"**Evidence:** `{e['path']}` lines {e['start']}–{e['end']}",
            "",
            f"{fence}{lang}",
            numbered,
            fence,
            "",
            "Label: ______",
            "",
        ]
    return "\n".join(out)


def write(cases: list[dict], cases_path: Path = CASES_PATH, sheet_path: Path = SHEET_PATH) -> None:
    payload = {
        "version": 1,
        "seed": SEED,
        "labels": list(LABELS),
        "target_counts": TARGET,
        "cases": cases,
    }
    cases_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    sheet_path.write_text(label_sheet(cases), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=CASES_PATH)
    parser.add_argument("--sheet", type=Path, default=SHEET_PATH)
    args = parser.parse_args(argv)
    cases = build()
    carried = carry_labels(cases, args.out)
    write(cases, args.out, args.sheet)
    counts = {kind: sum(c["constructed_as"] == kind for c in cases) for kind in TARGET}
    print(f"Wrote {len(cases)} cases to {args.out}: {counts}")
    print(f"Label sheet: {args.sheet}")
    if carried:
        print(f"Kept {carried} existing labels.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
