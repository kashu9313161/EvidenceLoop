"""Language-model providers behind one interface, chosen from the environment.

- ANTHROPIC_API_KEY or GEMINI_API_KEY (or GOOGLE_API_KEY): turns AI features on. With no key
  there is no AI, and the app still works.
- EL_PROVIDER: "anthropic" or "gemini", to choose when both keys are set (Anthropic otherwise).
- EL_MODEL_EXPLAIN: the model for file explanations (default: each provider's small, fast one).
"""

from __future__ import annotations

import importlib.util
import os
from functools import lru_cache

from intelligence.providers.base import Completion, Provider, ProviderError

DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "gemini": "gemini-3.5-flash-lite"}
DEFAULT_EXPLAIN_MODEL = DEFAULT_MODELS["anthropic"]
KEY_NAMES = {"anthropic": ("ANTHROPIC_API_KEY",), "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}

__all__ = ["DEFAULT_EXPLAIN_MODEL", "DEFAULT_MODELS", "Completion", "Provider", "ProviderError",
           "ai_status", "chosen_provider", "explain_provider", "provider_for"]


def _key(provider: str) -> str:
    for name in KEY_NAMES[provider]:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def chosen_provider() -> tuple[str | None, str]:
    """Which provider to use, or None and why not (in words for the person using the app)."""
    wanted = os.environ.get("EL_PROVIDER", "").strip().lower()
    if wanted and wanted not in KEY_NAMES:
        return None, f'EL_PROVIDER is "{wanted}"; use "anthropic" or "gemini".'
    if wanted:
        if not _key(wanted):
            return None, f"EL_PROVIDER is {wanted}, but {KEY_NAMES[wanted][0]} isn't set."
        name = wanted
    elif _key("anthropic"):
        name = "anthropic"
    elif _key("gemini"):
        name = "gemini"
    else:
        return None, "Set GEMINI_API_KEY or ANTHROPIC_API_KEY to add AI-written explanations."
    if name == "anthropic" and importlib.util.find_spec("anthropic") is None:
        return None, "Install the Anthropic SDK (pip install anthropic) to add AI explanations."
    return name, ""


def ai_status() -> tuple[bool, str]:
    """Whether AI features can run, and if not, why."""
    name, reason = chosen_provider()
    return name is not None, reason


def provider_for(purpose: str) -> Provider | None:
    """The provider for one job ("explain", "ask" or "judge"), or None without a key.
    EL_MODEL_<JOB> picks its model."""
    name, _ = chosen_provider()
    if name is None:
        return None
    model = os.environ.get(f"EL_MODEL_{purpose.upper()}", "").strip() or DEFAULT_MODELS[name]
    return _build(name, _key(name), model)


def explain_provider() -> Provider | None:
    return provider_for("explain")


@lru_cache(maxsize=4)
def _build(name: str, api_key: str, model: str) -> Provider:
    if name == "gemini":
        from intelligence.providers.gemini_provider import GeminiProvider

        return GeminiProvider(api_key, model)
    from intelligence.providers.anthropic_provider import AnthropicProvider

    return AnthropicProvider(api_key, model)
