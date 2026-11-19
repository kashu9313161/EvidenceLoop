"""JevEvaluator through the official typesafe-sdk, with a mocked transport (offline)."""

import json
import time

import httpx2
import pytest
from typesafe_sdk import RetryPolicy

from intelligence.budget import Budget, BudgetExceeded, cost_usd
from intelligence.evaluators import EvaluatorUnavailable, Thresholds
from intelligence.evaluators.choice import judge_description, make_evaluator
from intelligence.evaluators.jev import (
    MAX_ITEM_CHARS,
    MAX_STATE_CHARS,
    QUESTIONS,
    JevEvaluator,
    build_state,
)
from intelligence.evaluators.llm_judge import LlmJudgeEvaluator
from intelligence.providers.fake import FakeProvider
from intelligence.sendlog import Metered
from retrieval.evidence import EvidenceItem
from retrieval.source import range_sha256
from verification.checks import INSUFFICIENT, verify_claim

QUICK = RetryPolicy(max_retries=1, backoff_initial=0.01, backoff_max=0.01, backoff_jitter=0.0)


def item(text: str = "def place_order(items):\n    return pricing.total(items)", n: int = 1):
    return EvidenceItem(f"E{n}", "shop/orders.py", 3, 4, "code", "place_order", text, "0" * 64)


def answer(support=0.91, contradiction=0.04, sufficiency=0.83, model="jev-1.14.0",
           tokens=180, drop=()):
    nouls = {"support": support, "contradiction": contradiction, "sufficiency": sufficiency}
    return {"model": model, "usage": {"input_tokens": tokens, "output_tokens": 3},
            "answers": {k: {"type": "noul", "noul": v} for k, v in nouls.items() if k not in drop}}


def transport(*replies, seen=None):
    """Replies in turn: a dict (200 JSON), an (status, body, headers) tuple, or an exception."""
    queue = list(replies)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, tuple):
            status, body, headers = reply
            return httpx2.Response(status, json=body, headers=headers)
        return httpx2.Response(200, json=reply)

    return httpx2.MockTransport(handler)


def jev(*replies, seen=None, **kwargs):
    kwargs.setdefault("retry", QUICK)
    return JevEvaluator(api_key="ts-test", transport=transport(*replies, seen=seen),
                        compression=False, **kwargs)


def test_one_call_with_three_nouls_mapped_to_the_judgment():
    seen: list[httpx2.Request] = []
    metered = Metered(Budget())
    judgment = jev(answer(), seen=seen, version="jev-1.13.0", metered=metered).judge(
        "place_order totals the items", [item()])

    assert (judgment.support, judgment.contradiction, judgment.sufficiency) == (0.91, 0.04, 0.83)
    assert judgment.evaluator == "jev" and judgment.model_version == "typesafe:jev-1.14.0"

    request = seen[0]
    assert str(request.url).endswith("/v1/systemone")
    assert request.headers["authorization"] == "Bearer ts-test"
    assert not any("long-context" in h.lower() for h in request.headers)  # never long context
    body = json.loads(request.content)
    assert body["model"] == "jev-1.13.0"  # pinned, not an alias
    assert body["questions"] == {name: {"type": "noul", "instructions": text}
                                 for name, text in QUESTIONS.items()}
    assert body["state"] == {"claim": "place_order totals the items", "evidence": [
        {"id": "E1", "path": "shop/orders.py", "lines": "3-4",
         "code": "def place_order(items):\n    return pricing.total(items)"}]}

    assert metered.budget.input_tokens == 180
    assert metered.budget.cost == pytest.approx(cost_usd("jev-1.13.0", 180, 3))
    send = metered.sends[0]
    assert (send.purpose, send.provider, send.model, send.error) == \
        ("judge", "typesafe", "jev-1.13.0", None)


def test_the_questions_say_what_each_field_means():
    assert "not just mention related names" in QUESTIONS["support"]
    assert "already handled differently" in QUESTIONS["contradiction"]
    assert "enough to decide" in QUESTIONS["sufficiency"]


def test_the_version_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("EL_JEV_VERSION", "jev-1.13.0")
    assert JevEvaluator(api_key="k").version == "jev-1.13.0"
    monkeypatch.delenv("EL_JEV_VERSION")
    assert JevEvaluator(api_key="k").version == "jev-1.14.0"


def test_the_state_stays_small():
    long = "x = 1\n" * 5_000
    state = build_state("a claim", [item(long, n) for n in range(1, 6)])
    codes = [e["code"] for e in state["evidence"]]
    assert all(len(c) <= MAX_ITEM_CHARS for c in codes)
    assert sum(len(c) for c in codes) <= MAX_STATE_CHARS
    assert state["evidence"][0]["trimmed"] is True


def test_retry_after_is_honoured():
    seen: list[httpx2.Request] = []
    busy = (429, {"error": {"message": "slow down"}}, {"Retry-After": "1"})
    evaluator = jev(busy, answer(), seen=seen, retry=None)  # the evaluator's own retry policy
    started = time.perf_counter()
    judgment = evaluator.judge("claim", [item()])
    assert judgment.support == 0.91 and len(seen) == 2
    assert time.perf_counter() - started >= 0.9  # waited as asked


@pytest.mark.parametrize(("reply", "message"), [
    ((503, {"error": "down"}, {}), r"TypeSafe returned an error \(503\)"),
    ((401, {"error": "bad key"}, {}), "TypeSafe API key was rejected"),
    ((404, {"error": "no model"}, {}), "wasn't found. Check EL_JEV_VERSION"),
    ((429, {"error": "busy"}, {}), "rate limiting"),
    (httpx2.ConnectError("no route"), "Couldn't reach TypeSafe"),
    (httpx2.ReadTimeout("slow"), "took too long"),
])
def test_an_outage_makes_the_evaluator_unavailable(reply, message):
    metered = Metered(Budget())
    with pytest.raises(EvaluatorUnavailable, match=message):
        jev(reply, metered=metered).judge("claim", [item()])
    assert metered.sends[0].error is not None and metered.budget.calls == 0


def test_an_answer_missing_a_question_is_unusable():
    with pytest.raises(EvaluatorUnavailable, match="didn't answer: sufficiency"):
        jev(answer(drop=("sufficiency",))).judge("claim", [item()])


def test_the_budget_is_checked_before_sending():
    seen: list[httpx2.Request] = []
    with pytest.raises(BudgetExceeded):
        jev(answer(), seen=seen, metered=Metered(Budget(max_tokens=10))).judge("claim", [item()])
    assert seen == []


class OneFile:
    """Stands in for SnapshotFiles: shop/orders.py with place_order on lines 3-4."""

    lines_of = ["from shop import pricing", "", "def place_order(items):",
                "    return pricing.total(items)"]

    def has(self, path: str) -> bool:
        return path == "shop/orders.py"

    def lines(self, path: str) -> list[str]:
        return self.lines_of


def test_an_outage_leaves_the_claim_not_enough_evidence():
    files = OneFile()
    good = EvidenceItem("E1", "shop/orders.py", 3, 4, "code", "place_order",
                        "\n".join(files.lines_of[2:4]), range_sha256(files.lines_of, 3, 4))
    result = verify_claim("place_order totals the items", ["E1"], {"E1": good}, files,
                          jev((503, {}, {})), Thresholds())
    assert result.status == INSUFFICIENT and "couldn't run" in result.reason


def test_ask_uses_the_configured_evaluator(monkeypatch):
    metered = Metered(Budget())
    judge = FakeProvider({}, model="judge-model")
    monkeypatch.delenv("EL_EVALUATOR", raising=False)
    assert isinstance(make_evaluator(judge, metered), LlmJudgeEvaluator)
    assert make_evaluator(None, metered) is None

    monkeypatch.setenv("EL_EVALUATOR", "jev")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert make_evaluator(judge, metered) is None  # chosen but no key: no evaluator
    assert judge_description(judge)["available"] is False
    assert "TYPESAFE_API_KEY" in judge_description(judge)["reason"]

    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    monkeypatch.setenv("EL_JEV_VERSION", "jev-1.13.0")
    evaluator = make_evaluator(judge, metered)
    assert isinstance(evaluator, JevEvaluator) and evaluator.version == "jev-1.13.0"
    assert judge_description(judge) == {"kind": "jev", "provider": "typesafe",
                                        "model": "jev-1.13.0", "available": True, "reason": ""}

    monkeypatch.setenv("EL_EVALUATOR", "something-else")
    assert isinstance(make_evaluator(judge, metered), LlmJudgeEvaluator)
