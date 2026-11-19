"""Evaluators judge whether cited evidence supports a claim.

One protocol, several implementations: an LLM judge now, Jev later. Scores are 0..1 and are
compared with thresholds that have not been calibrated yet (Gate A will calibrate them).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Protocol

from retrieval.evidence import EvidenceItem


@dataclass
class Judgment:
    support: float
    contradiction: float
    sufficiency: float
    evaluator: str
    model_version: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


class EvaluatorUnavailable(Exception):
    """The evaluator couldn't give a judgment (outage, bad key, unusable answer)."""


class Evaluator(Protocol):
    name: str

    def judge(self, claim: str, evidence: list[EvidenceItem]) -> Judgment:
        """Raise EvaluatorUnavailable when no judgment can be made."""
        ...


@dataclass(frozen=True)
class Thresholds:
    support: float = 0.80
    sufficiency: float = 0.70
    contradiction: float = 0.50
    calibrated: bool = False

    @classmethod
    def from_env(cls) -> Thresholds:
        def value(name: str, default: float) -> float:
            try:
                return min(max(float(os.environ.get(name, "") or default), 0.0), 1.0)
            except ValueError:
                return default

        return cls(value("EL_T_SUPPORT", 0.80), value("EL_T_SUFFICIENCY", 0.70),
                   value("EL_T_CONTRADICTION", 0.50))

    def to_dict(self) -> dict:
        return asdict(self)
