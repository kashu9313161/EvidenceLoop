"""Verify one claim against the evidence it cites.

1. Deterministic checks, for every cited id: it exists in the evidence package, its path is in
   the snapshot, its lines are inside the file, and the file's text there still hashes to the
   value recorded when the evidence was built. Any failure: rejected (the evaluator isn't asked).
2. The evaluator scores support, contradiction and sufficiency. Below the thresholds, or no
   evaluator, or the run's budget spent: insufficient. Otherwise: verified.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from intelligence.budget import BudgetExceeded
from intelligence.evaluators import Evaluator, EvaluatorUnavailable, Judgment, Thresholds
from retrieval.evidence import EvidenceItem
from retrieval.source import SnapshotFiles, range_sha256

VERIFIED, INSUFFICIENT, REJECTED = "verified", "insufficient", "rejected"


@dataclass
class Check:
    check: str  # "cited", "known_id", "path", "lines", "hash"
    ok: bool
    detail: str


@dataclass
class ClaimResult:
    text: str
    evidence_ids: list[str]
    status: str
    reason: str
    checks: list[Check] = field(default_factory=list)
    judgment: Judgment | None = None

    def to_dict(self) -> dict:
        out = asdict(self)
        out["judgment"] = self.judgment.to_dict() if self.judgment else None
        return out


def citation_checks(evidence_ids: list[str], package: dict[str, EvidenceItem],
                    files: SnapshotFiles) -> list[Check]:
    if not evidence_ids:
        return [Check("cited", False, "It cites no evidence.")]
    checks: list[Check] = []
    for eid in evidence_ids:
        item = package.get(eid)
        if item is None:
            checks.append(Check("known_id", False, f"It cites {eid}, which isn't in the evidence."))
            continue
        checks.append(Check("known_id", True, f"{eid} is in the evidence."))
        if not files.has(item.path):
            checks.append(Check("path", False, f"{item.path} isn't in this snapshot."))
            continue
        checks.append(Check("path", True, f"{item.path} is in the snapshot."))
        lines = files.lines(item.path)
        if lines is None or not (1 <= item.start <= item.end <= len(lines)):
            size = "unreadable" if lines is None else f"{len(lines)} lines long"
            checks.append(Check("lines", False, f"Lines {item.start}-{item.end} aren't in "
                                                f"{item.path} ({size})."))
            continue
        checks.append(Check("lines", True, f"Lines {item.start}-{item.end} exist."))
        if range_sha256(lines, item.start, item.end) != item.sha256:
            checks.append(Check("hash", False, f"{item.path} lines {item.start}-{item.end} "
                                               f"changed since the evidence was read."))
        else:
            checks.append(Check("hash", True, "The cited text matches the file."))
    return checks


def _scores(judgment: Judgment) -> str:
    return (f"support {judgment.support:.2f}, sufficiency {judgment.sufficiency:.2f}, "
            f"contradiction {judgment.contradiction:.2f}")


def verify_claim(text: str, evidence_ids: list[str], package: dict[str, EvidenceItem],
                 files: SnapshotFiles, evaluator: Evaluator | None,
                 thresholds: Thresholds) -> ClaimResult:
    ids = list(dict.fromkeys(evidence_ids))
    checks = citation_checks(ids, package, files)
    failed = [c for c in checks if not c.ok]
    if failed:
        return ClaimResult(text, ids, REJECTED, failed[0].detail, checks)
    if evaluator is None:
        return ClaimResult(text, ids, INSUFFICIENT,
                           "The citations check out, but no evaluator is available to judge "
                           "whether they support the claim.", checks)
    try:
        judgment = evaluator.judge(text, [package[i] for i in ids])
    except BudgetExceeded as exc:
        return ClaimResult(text, ids, INSUFFICIENT, f"Not judged: {exc}.", checks)
    except EvaluatorUnavailable as exc:
        return ClaimResult(text, ids, INSUFFICIENT, f"The evaluator couldn't run: {exc}", checks)

    short: list[str] = []
    if judgment.support < thresholds.support:
        short.append(f"support {judgment.support:.2f} is below {thresholds.support:.2f}")
    if judgment.sufficiency < thresholds.sufficiency:
        short.append(f"sufficiency {judgment.sufficiency:.2f} is below "
                     f"{thresholds.sufficiency:.2f}")
    if judgment.contradiction >= thresholds.contradiction:
        short.append(f"contradiction {judgment.contradiction:.2f} is at or above "
                     f"{thresholds.contradiction:.2f}")
    why = f" {judgment.reason}" if judgment.reason else ""
    if short:
        return ClaimResult(text, ids, INSUFFICIENT,
                           f"Not enough support: {'; '.join(short)}.{why}", checks, judgment)
    return ClaimResult(text, ids, VERIFIED,
                       f"The cited code supports it ({_scores(judgment)}).{why}", checks,
                       judgment)
