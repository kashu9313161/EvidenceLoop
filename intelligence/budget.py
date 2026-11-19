"""Token and cost limits for one run (one question), with list prices to estimate cost."""

from __future__ import annotations

import os

# US dollars per million tokens (input, output), standard list prices. A model not listed
# here still runs; its cost shows as unknown and only the token cap applies.
PRICES_PER_MTOK = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (3.00, 15.00),
    "claude-opus-5-5": (5.00, 25.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.8-flash": (0.75, 3.75),
}
DEFAULT_MAX_TOKENS = 60_000
DEFAULT_MAX_COST = 0.05


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = PRICES_PER_MTOK.get(model)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def estimate_tokens(text_chars: int) -> int:
    """A cautious token estimate for text of this length (code runs about 3-4 characters a
    token)."""
    return text_chars // 3 + 1


class BudgetExceeded(Exception):
    """The next call could take the run past its limit."""


class Budget:
    def __init__(self, max_tokens: int = DEFAULT_MAX_TOKENS,
                 max_cost: float = DEFAULT_MAX_COST) -> None:
        self.max_tokens = max_tokens
        self.max_cost = max_cost
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0
        self.cost_known = True
        self.calls = 0

    @classmethod
    def from_env(cls) -> Budget:
        def number(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, "") or default)
            except ValueError:
                return default

        return cls(int(number("EL_MAX_TOKENS_PER_ASK", DEFAULT_MAX_TOKENS)),
                   number("EL_MAX_COST_PER_ASK", DEFAULT_MAX_COST))

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def check(self, model: str, prompt_chars: int, max_output: int) -> None:
        """Raise BudgetExceeded if a call this size could take the run past its limits."""
        input_estimate = estimate_tokens(prompt_chars)
        if self.tokens + input_estimate + max_output > self.max_tokens:
            raise BudgetExceeded(f"the limit of {self.max_tokens:,} tokens for one question "
                                 f"(EL_MAX_TOKENS_PER_ASK) was reached")
        cost = cost_usd(model, input_estimate, max_output)
        if cost is not None and self.cost + cost > self.max_cost:
            raise BudgetExceeded(f"the limit of ${self.max_cost:g} for one question "
                                 f"(EL_MAX_COST_PER_ASK) was reached")

    def record(self, model: str, input_tokens: int, output_tokens: int) -> float | None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        cost = cost_usd(model, input_tokens, output_tokens)
        if cost is None:
            self.cost_known = False
        else:
            self.cost += cost
        return cost

    def summary(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cost_usd": round(self.cost, 6) if self.cost_known else None,
                "max_tokens": self.max_tokens, "max_cost_usd": self.max_cost}
