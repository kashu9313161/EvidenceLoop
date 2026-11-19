"""Gate A metrics. Pure functions over result rows, so they are easy to test.

A row is one judgment: {"label": human label, "support", "contradiction", "sufficiency"
(floats, None when the evaluator failed), "latency_s", "cost_usd", "error"}.

"Verified" is what Ask would show: support >= t_support, sufficiency >= t_sufficiency and
contradiction < t_contradiction. Only claims labelled "supports" should be verified; every other
label (not_supported, related_not_supporting, ambiguous) should not.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass

LABELS = ("supports", "not_supported", "related_not_supporting", "ambiguous")
WRONG = ("not_supported", "related_not_supporting")  # verifying these is the costly mistake
GRID = [round(0.05 * i, 2) for i in range(1, 20)]  # 0.05 .. 0.95


@dataclass(frozen=True)
class Thresholds:
    support: float = 0.80
    sufficiency: float = 0.70
    contradiction: float = 0.50

    def to_dict(self) -> dict:
        return asdict(self)


def judged(rows: list[dict]) -> list[dict]:
    """Rows where the evaluator gave scores."""
    return [r for r in rows if r.get("error") is None and r.get("support") is not None]


def is_verified(row: dict, t: Thresholds) -> bool:
    return (row["support"] >= t.support and row["sufficiency"] >= t.sufficiency
            and row["contradiction"] < t.contradiction)


def should_verify(row: dict) -> bool:
    return row["label"] == "supports"


def accuracy(rows: list[dict], t: Thresholds) -> float | None:
    scored = judged(rows)
    if not scored:
        return None
    return sum(is_verified(r, t) == should_verify(r) for r in scored) / len(scored)


def false_verified_rate(rows: list[dict], t: Thresholds,
                        labels: tuple[str, ...] = WRONG) -> float | None:
    """Share of claims with these labels that would wrongly show as verified."""
    scored = [r for r in judged(rows) if r["label"] in labels]
    if not scored:
        return None
    return sum(is_verified(r, t) for r in scored) / len(scored)


def missed_rate(rows: list[dict], t: Thresholds) -> float | None:
    """Share of truly supported claims that would not show as verified."""
    scored = [r for r in judged(rows) if r["label"] == "supports"]
    if not scored:
        return None
    return sum(not is_verified(r, t) for r in scored) / len(scored)


def targets(row: dict) -> dict[str, float]:
    """What a perfectly calibrated evaluator would say for each field, from the human label:
    support: the evidence shows the claim; contradiction: it shows the claim is false;
    sufficiency: the evidence is enough to decide either way."""
    label = row["label"]
    return {
        "support": 1.0 if label == "supports" else 0.0,
        "contradiction": 1.0 if label == "not_supported" else 0.0,
        "sufficiency": 1.0 if label in ("supports", "not_supported") else 0.0,
    }


def brier(rows: list[dict]) -> dict[str, float | None]:
    """Mean squared error of each probability against its target (0 is perfect, 0.25 is a coin
    that always says 0.5)."""
    scored = judged(rows)
    out: dict[str, float | None] = {}
    for field in ("support", "contradiction", "sufficiency"):
        out[field] = (sum((r[field] - targets(r)[field]) ** 2 for r in scored) / len(scored)
                      if scored else None)
    return out


def _rank(rows: list[dict], t: Thresholds, current: Thresholds) -> tuple:
    """Higher is better: accuracy, then fewer false verifications, then closest to current."""
    distance = (abs(t.support - current.support) + abs(t.sufficiency - current.sufficiency)
                + abs(t.contradiction - current.contradiction))
    return (accuracy(rows, t) or 0.0, -(false_verified_rate(rows, t) or 0.0), -distance)


def best_per_field(rows: list[dict], current: Thresholds) -> dict[str, dict]:
    """For each field alone, the threshold that does best with the other two kept as they are."""
    out = {}
    for field in ("support", "sufficiency", "contradiction"):
        options = [Thresholds(**{**current.to_dict(), field: value}) for value in GRID]
        best = max(options, key=lambda t: _rank(rows, t, current))
        out[field] = {"threshold": getattr(best, field), "accuracy": accuracy(rows, best)}
    return out


def best_joint(rows: list[dict], current: Thresholds) -> Thresholds:
    """The best combination of all three thresholds on the grid."""
    options = (Thresholds(s, f, c) for s, f, c in itertools.product(GRID, GRID, GRID))
    return max(options, key=lambda t: _rank(rows, t, current))


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (p from 0 to 100)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(rows: list[dict], current: Thresholds) -> dict:
    scored = judged(rows)
    latencies = [r["latency_s"] for r in rows if r.get("latency_s") is not None]
    costs = [r["cost_usd"] for r in scored if r.get("cost_usd") is not None]
    calibrated = best_joint(rows, current) if scored else None
    return {
        "judgments": len(rows),
        "failed": len(rows) - len(scored),
        "accuracy": accuracy(rows, current),
        "false_verified_rate": false_verified_rate(rows, current),
        "false_verified_by_label": {
            label: false_verified_rate(rows, current, (label,)) for label in WRONG + ("ambiguous",)
        },
        "missed_rate": missed_rate(rows, current),
        "brier": brier(rows),
        "best_per_field": best_per_field(rows, current) if scored else None,
        "calibrated_thresholds": calibrated.to_dict() if calibrated else None,
        "calibrated_accuracy": accuracy(rows, calibrated) if calibrated else None,
        "calibrated_false_verified_rate": false_verified_rate(rows, calibrated)
        if calibrated else None,
        "latency_p50_s": percentile(latencies, 50),
        "latency_p95_s": percentile(latencies, 95),
        "cost_per_judgment_usd": sum(costs) / len(costs) if costs and len(costs) == len(scored)
        else None,
    }


def beats(jev: dict, llm: dict) -> bool:
    """The plan's Gate A rule, made explicit: Jev beats the LLM judge when, each at its own
    calibrated thresholds, it is more accurate and doesn't verify more wrong claims; or is as
    accurate and verifies fewer."""
    ja, la = jev.get("calibrated_accuracy"), llm.get("calibrated_accuracy")
    jf, lf = jev.get("calibrated_false_verified_rate"), llm.get("calibrated_false_verified_rate")
    if ja is None:
        return False
    if la is None:
        return True
    jf = jf if jf is not None else 0.0
    lf = lf if lf is not None else 0.0
    return (ja > la and jf <= lf) or (ja == la and jf < lf)
