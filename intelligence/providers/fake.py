"""A provider for tests: returns canned answers and records what it was sent."""

from __future__ import annotations

import json
from collections.abc import Callable

from intelligence.providers.base import Completion, ProviderError

Answer = dict | Callable[[str, list[dict]], dict]


class FakeProvider:
    name = "fake"

    def __init__(self, answer: Answer | None = None, model: str = "fake-model",
                 error: str | None = None) -> None:
        self.model = model
        self._answer = answer if answer is not None else {"summary": "A file.", "key_parts": []}
        self._error = error
        self.calls: list[dict] = []

    def complete(self, system: str, messages: list[dict], schema: dict,
                 max_tokens: int = 4000) -> Completion:
        self.calls.append({"system": system, "messages": messages, "schema": schema,
                           "max_tokens": max_tokens})
        if self._error:
            raise ProviderError(self._error)
        data = self._answer(system, messages) if callable(self._answer) else dict(self._answer)
        sent = len(system) + sum(len(str(m["content"])) for m in messages)
        return Completion(data, input_tokens=sent // 4, output_tokens=len(json.dumps(data)) // 4)

    def complete_structured(self, system: str, messages: list[dict], schema: dict,
                            max_tokens: int = 4000) -> dict:
        return self.complete(system, messages, schema, max_tokens).data
