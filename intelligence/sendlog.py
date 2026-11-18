"""What left the machine: every call to a provider, metered against the run's budget and
recorded (purpose, model, the exact prompt, its hash, tokens and cost)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from intelligence.budget import Budget
from intelligence.providers import Provider, ProviderError
from storage.database import get_connection, initialize_database


@dataclass
class Send:
    purpose: str
    provider: str
    model: str
    prompt: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    error: str | None = None
    sent_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()


class Metered:
    """Provider calls for one run: checked against the budget before sending, logged after."""

    def __init__(self, budget: Budget | None = None) -> None:
        self.budget = budget or Budget.from_env()
        self.sends: list[Send] = []

    def call(self, provider: Provider, purpose: str, system: str, prompt: str, schema: dict,
             max_tokens: int) -> dict:
        """Raises BudgetExceeded (nothing sent) or ProviderError (logged)."""
        self.budget.check(provider.model, len(system) + len(prompt), max_tokens)
        send = Send(purpose, provider.name, provider.model, f"{system}\n\n{prompt}")
        try:
            completion = provider.complete(system, [{"role": "user", "content": prompt}], schema,
                                           max_tokens)
        except ProviderError as exc:
            send.error = str(exc)
            self.sends.append(send)
            raise
        send.input_tokens, send.output_tokens = completion.input_tokens, completion.output_tokens
        send.cost_usd = self.budget.record(provider.model, completion.input_tokens,
                                           completion.output_tokens)
        self.sends.append(send)
        return completion.data


def record_sends(snapshot_id: str, ask_id: str | None, sends: list[Send]) -> None:
    if not sends:
        return
    initialize_database()
    with get_connection() as connection:
        connection.executemany(
            "INSERT INTO provider_sends (snapshot_id, ask_id, purpose, provider, model, sent_at, "
            "prompt_sha256, prompt_chars, prompt, input_tokens, output_tokens, cost_usd, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(snapshot_id, ask_id, s.purpose, s.provider, s.model, s.sent_at, s.prompt_sha256,
              len(s.prompt), s.prompt, s.input_tokens, s.output_tokens, s.cost_usd, s.error)
             for s in sends],
        )
