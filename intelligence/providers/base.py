"""The interface every language-model provider implements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(Exception):
    """A provider call failed. The message is written for the person using the app."""


@dataclass
class Completion:
    """A structured answer and what it cost in tokens."""

    data: dict
    input_tokens: int
    output_tokens: int


class Provider(Protocol):
    name: str
    model: str

    def complete(self, system: str, messages: list[dict], schema: dict,
                 max_tokens: int = 4000) -> Completion:
        """Return a JSON object matching `schema` with token usage, or raise ProviderError."""
        ...

    def complete_structured(self, system: str, messages: list[dict], schema: dict,
                            max_tokens: int = 4000) -> dict:
        """complete() without the usage."""
        ...
