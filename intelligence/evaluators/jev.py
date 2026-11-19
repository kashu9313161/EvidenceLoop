"""Jev (TypeSafe) as an evaluator, through the official typesafe-sdk.

One System One call per claim asks three noul (yes/no probability) questions about the claim
and its cited code; the probabilities become the Judgment's support, contradiction and
sufficiency. The model version is pinned (EL_JEV_VERSION) because thresholds are tuned
against one version. Long-context mode is never turned on. The state sent is small: the claim
and the cited code, trimmed. Retries honour Retry-After; an outage, a bad key or an unusable
answer becomes EvaluatorUnavailable, which leaves the claim "not enough evidence".
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import json
import os
from typing import Any

from intelligence.evaluators import EvaluatorUnavailable, Judgment
from intelligence.sendlog import Metered, Send
from retrieval.evidence import EvidenceItem

JEV_VERSIONS = ("jev-1.13.0", "jev-1.14.0")
DEFAULT_JEV_VERSION = "jev-1.14.0"
DEFAULT_TIMEOUT = 20.0  # seconds per HTTP operation
RETRY_BUDGET = 60.0  # seconds for one judgment, retries and waits included
MAX_ITEM_CHARS = 4_000
MAX_STATE_CHARS = 12_000

QUESTIONS = {
    "support": "Does this code directly show the claim, not just mention related names?",
    "contradiction": "Does this code show the claim is false or already handled differently?",
    "sufficiency": "Is this evidence enough to decide?",
}


def jev_version() -> str:
    return os.environ.get("EL_JEV_VERSION", "").strip() or DEFAULT_JEV_VERSION


def jev_status() -> tuple[bool, str]:
    """Whether Jev can run, and if not, why."""
    if importlib.util.find_spec("typesafe_sdk") is None:
        return False, "Install the TypeSafe SDK (pip install typesafe-sdk) to use Jev."
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        return False, "Set TYPESAFE_API_KEY to use Jev."
    return True, ""


def build_state(claim: str, evidence: list[EvidenceItem]) -> dict:
    """The claim and the cited code, trimmed so a judgment stays small and cheap."""
    items: list[dict] = []
    room = MAX_STATE_CHARS
    for item in evidence:
        if room <= 0:
            break
        code = item.text[: min(MAX_ITEM_CHARS, room)]
        room -= len(code)
        entry = {"id": item.id, "path": item.path, "lines": f"{item.start}-{item.end}",
                 "code": code}
        if len(code) < len(item.text):
            entry["trimmed"] = True
        items.append(entry)
    return {"claim": claim, "evidence": items}


def _run(coro: Any) -> Any:
    """Run a coroutine from synchronous code, even when an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class JevEvaluator:
    name = "jev"

    def __init__(self, version: str | None = None, metered: Metered | None = None,
                 api_key: str | None = None, timeout: float = DEFAULT_TIMEOUT,
                 retry: Any = None, transport: Any = None, compression: Any = None) -> None:
        self.version = version or jev_version()
        self.metered = metered or Metered()
        self.api_key = api_key
        self.timeout = timeout
        self.retry = retry
        self.transport = transport  # an httpx2 transport, for tests
        self.compression = compression

    async def _ask(self, state: dict) -> Any:
        from typesafe_sdk import AsyncTypeSafeClient, Noul, RetryPolicy

        retry = self.retry or RetryPolicy(max_retries=2, respect_retry_after=True,
                                          timeout=RETRY_BUDGET)
        questions = {name: Noul(instructions=text) for name, text in QUESTIONS.items()}
        async with AsyncTypeSafeClient(api_key=self.api_key, model=self.version, retry=retry,
                                       timeout=self.timeout, transport=self.transport,
                                       compression=self.compression) as client:
            return await client.system_one(state, questions, model=self.version)

    def judge(self, claim: str, evidence: list[EvidenceItem]) -> Judgment:
        import typesafe_sdk as ts

        state = build_state(claim, evidence)
        sent = json.dumps({"state": state, "questions": QUESTIONS}, ensure_ascii=False)
        self.metered.budget.check(self.version, len(sent), 0)  # BudgetExceeded: nothing sent
        send = Send("judge", "typesafe", self.version, sent)
        try:
            response = _run(self._ask(state))
        except ts.TypeSafeAuthenticationError as exc:
            raise self._failed(send, "The TypeSafe API key was rejected. Check TYPESAFE_API_KEY.",
                               exc) from exc
        except ts.TypeSafePermissionDeniedError as exc:
            raise self._failed(send, f"This TypeSafe key can't use {self.version}.", exc) from exc
        except ts.TypeSafeNotFoundError as exc:
            raise self._failed(send, f"The Jev model {self.version} wasn't found. Check "
                                     f"EL_JEV_VERSION.", exc) from exc
        except ts.TypeSafeRateLimitError as exc:
            raise self._failed(send, "TypeSafe is rate limiting requests. Try again shortly.",
                               exc) from exc
        except ts.TypeSafeAPITimeoutError as exc:
            raise self._failed(send, "TypeSafe took too long to answer.", exc) from exc
        except ts.TypeSafeAPIConnectionError as exc:
            raise self._failed(send, "Couldn't reach TypeSafe. Check your internet connection.",
                               exc) from exc
        except ts.TypeSafeAPIError as exc:
            raise self._failed(send, f"TypeSafe returned an error ({exc.status}).", exc) from exc
        except ts.TypeSafeError as exc:
            raise self._failed(send, f"TypeSafe failed: {exc}", exc) from exc

        usage = response.usage
        send.input_tokens = usage.input_tokens or 0
        send.output_tokens = usage.output_tokens or 0
        send.cost_usd = self.metered.budget.record(self.version, send.input_tokens,
                                                   send.output_tokens)
        self.metered.sends.append(send)

        nouls = response.nouls
        missing = [name for name in QUESTIONS if name not in nouls]
        if missing:
            raise EvaluatorUnavailable(f"Jev didn't answer: {', '.join(missing)}.")
        return Judgment(
            support=_probability(nouls["support"].noul),
            contradiction=_probability(nouls["contradiction"].noul),
            sufficiency=_probability(nouls["sufficiency"].noul),
            evaluator=self.name,
            model_version=f"typesafe:{response.model}",
            reason="Jev's probabilities for the support, contradiction and sufficiency questions.",
        )

    def _failed(self, send: Send, message: str, exc: Exception) -> EvaluatorUnavailable:
        send.error = f"{message} ({type(exc).__name__})"
        self.metered.sends.append(send)
        return EvaluatorUnavailable(message)


def _probability(value: Any) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError) as exc:
        raise EvaluatorUnavailable("Jev returned a probability that isn't a number.") from exc
