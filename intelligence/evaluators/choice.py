"""Which evaluator judges Ask's claims: EL_EVALUATOR = "llm" (default) or "jev"."""

from __future__ import annotations

import os

from intelligence.evaluators import Evaluator
from intelligence.evaluators.jev import JevEvaluator, jev_status, jev_version
from intelligence.evaluators.llm_judge import LlmJudgeEvaluator
from intelligence.providers import Provider
from intelligence.sendlog import Metered

EVALUATORS = ("llm", "jev")


def evaluator_kind() -> str:
    kind = os.environ.get("EL_EVALUATOR", "").strip().lower()
    return kind if kind in EVALUATORS else "llm"


def make_evaluator(judge_provider: Provider | None, metered: Metered) -> Evaluator | None:
    """The configured evaluator, or None when it can't run (claims then stay 'not enough
    evidence', with the reason)."""
    if evaluator_kind() == "jev":
        return JevEvaluator(metered=metered) if jev_status()[0] else None
    return LlmJudgeEvaluator(judge_provider, metered) if judge_provider is not None else None


def judge_description(judge_provider: Provider | None) -> dict:
    """For the chat header: which judge checks claims."""
    if evaluator_kind() == "jev":
        available, reason = jev_status()
        return {"kind": "jev", "provider": "typesafe", "model": jev_version(),
                "available": available, "reason": reason}
    return {"kind": "llm", "provider": judge_provider.name if judge_provider else None,
            "model": judge_provider.model if judge_provider else None,
            "available": judge_provider is not None, "reason": ""}
