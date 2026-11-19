"""Check cases.json before anything is scored against it.

    python -m eval.gate_a.verify_cases [path/to/cases.json]

- Re-reads every evidence snippet off the snapshot on disk and compares it with the stored
  text, so a stale line range can't silently turn a case into nonsense.
- Checks the fields, unique ids, the bucket counts and the spelling of every label.
- Reports labelling progress, and lists cases where your label differs from constructed_as
  (the generator's aim; your label is what scores the evaluators, so a difference is
  information, not an error).

Exits 1 on any problem. evaluation/gate_a/run.py calls problems() and refuses a broken set.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from eval.gate_a.build_cases import CASES_PATH, TARGET, read_span, snapshot_roots
from evaluation.gate_a.metrics import LABELS

FIELDS = ("id", "repo", "snapshot", "claim", "evidence", "constructed_as", "construction_note",
          "label", "labeller_note")
EVIDENCE_FIELDS = ("path", "start", "end", "text")


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    labelled: int = 0
    total: int = 0
    label_counts: Counter = field(default_factory=Counter)
    disagreements: list[str] = field(default_factory=list)


def check(path: Path = CASES_PATH, roots: dict[str, Path] | None = None) -> Report:
    """Everything verify reports. `roots` maps snapshot id -> directory (default: the DB)."""
    report = Report()
    try:
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report.problems.append(f"Can't read cases from {path}: {exc}")
        return report
    report.total = len(cases)

    snapshots = sorted({c.get("snapshot") for c in cases if isinstance(c.get("snapshot"), str)})
    if roots is None:
        try:
            roots = snapshot_roots(snapshots)
        except SystemExit as exc:
            report.problems.append(str(exc))
            return report

    ids = Counter(c.get("id") for c in cases)
    report.problems += [f"Duplicate id {i!r} ({n} cases)" for i, n in ids.items() if n > 1]

    kinds = Counter(c.get("constructed_as") for c in cases)
    if dict(kinds) != TARGET:
        report.problems.append(f"Bucket counts are {dict(kinds)}, expected {TARGET}")

    for case in cases:
        cid = case.get("id", "?")
        missing = [f for f in FIELDS if f not in case]
        evidence = case.get("evidence") if isinstance(case.get("evidence"), dict) else {}
        missing += [f"evidence.{f}" for f in EVIDENCE_FIELDS if f not in evidence]
        if missing:
            report.problems.append(f"{cid}: missing {', '.join(missing)}")
            continue
        if case["constructed_as"] not in LABELS:
            report.problems.append(f"{cid}: constructed_as {case['constructed_as']!r} is not one "
                                   f"of {', '.join(LABELS)}")
        label = case["label"]
        if label is not None and label not in LABELS:
            report.problems.append(f"{cid}: label {label!r} is not one of {', '.join(LABELS)} "
                                   "(or null for not labelled yet)")
        elif label is not None:
            report.labelled += 1
            report.label_counts[label] += 1
            if label != case["constructed_as"]:
                report.disagreements.append(
                    f"{cid}: labelled {label}, constructed as {case['constructed_as']}. "
                    f"Claim: {case['claim']} Construction note: {case['construction_note']}")

        root = roots.get(case["snapshot"])
        if root is None:
            report.problems.append(f"{cid}: snapshot {case['snapshot']} is not on disk")
            continue
        try:
            on_disk = read_span(root, evidence["path"], evidence["start"], evidence["end"])
        except SystemExit as exc:
            report.problems.append(f"{cid}: {exc}")
            continue
        if on_disk != evidence["text"]:
            report.problems.append(
                f"{cid}: evidence text no longer matches {evidence['path']}:"
                f"{evidence['start']}-{evidence['end']} on disk (stale range or edited text)")
    return report


def problems(path: Path = CASES_PATH, roots: dict[str, Path] | None = None) -> list[str]:
    return check(path, roots).problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else CASES_PATH
    report = check(path)
    print(f"{path}: {report.total} cases, {report.labelled} labelled")
    if report.label_counts:
        print("Labels so far: " + ", ".join(f"{k} {report.label_counts[k]}" for k in LABELS))
    if report.disagreements:
        print(f"\n{len(report.disagreements)} labels differ from constructed_as "
              "(your label is what counts; check these were deliberate):")
        for line in report.disagreements:
            print(f"  - {line}")
    if report.problems:
        print(f"\n{len(report.problems)} problem(s):")
        for line in report.problems:
            print(f"  - {line}")
        return 1
    print("\nOK: every snippet matches the snapshot on disk.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
