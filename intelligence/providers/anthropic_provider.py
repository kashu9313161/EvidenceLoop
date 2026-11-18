"""Claude through the official Anthropic SDK, with structured (JSON schema) output."""

from __future__ import annotations

import json

from intelligence.providers.base import Completion, ProviderError


def api_message(body: object) -> str:
    """The reason the API gave, from its error body: {"error": {"message": "..."}}."""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"].strip()
        if isinstance(body.get("message"), str):
            return body["message"].strip()
    return ""


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: float = 60.0) -> None:
        import anthropic  # imported here so the app runs without the package installed

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.model = model

    def complete_structured(self, system: str, messages: list[dict], schema: dict,
                            max_tokens: int = 4000) -> dict:
        return self.complete(system, messages, schema, max_tokens).data

    def complete(self, system: str, messages: list[dict], schema: dict,
                 max_tokens: int = 4000) -> Completion:
        errors = self._anthropic
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": schema}},
            )
        except errors.AuthenticationError as exc:
            raise ProviderError("The Anthropic API key was rejected. "
                                "Check ANTHROPIC_API_KEY.") from exc
        except errors.PermissionDeniedError as exc:
            raise ProviderError(f"This API key can't use {self.model}. "
                                "Set EL_MODEL_EXPLAIN to a model it can use.") from exc
        except errors.NotFoundError as exc:
            raise ProviderError(f"The model {self.model} wasn't found. "
                                "Check EL_MODEL_EXPLAIN.") from exc
        except errors.RateLimitError as exc:
            raise ProviderError("The Anthropic API is rate limiting requests. "
                                "Try again in a minute.") from exc
        except (errors.APIConnectionError, errors.APITimeoutError) as exc:
            raise ProviderError("Couldn't reach the Anthropic API. "
                                "Check your internet connection.") from exc
        except errors.APIStatusError as exc:
            reason = api_message(exc.body)
            if "credit balance" in reason.lower():
                raise ProviderError("Your Anthropic credit balance is too low. Add credits in "
                                    "the Anthropic Console (Plans & Billing).") from exc
            raise ProviderError(f"The Anthropic API refused the request ({exc.status_code})"
                                + (f": {reason}" if reason else ".")) from exc

        if response.stop_reason == "refusal":
            raise ProviderError("The model declined to explain this file.")
        if response.stop_reason == "max_tokens":
            raise ProviderError("The explanation was cut off before it finished. Try again.")
        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise ProviderError("The model returned no explanation. Try again.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError("The model's answer wasn't valid JSON. Try again.") from exc
        if not isinstance(data, dict):
            raise ProviderError("The model's answer had the wrong shape. Try again.")
        usage = getattr(response, "usage", None)
        return Completion(data, input_tokens=getattr(usage, "input_tokens", 0) or 0,
                          output_tokens=getattr(usage, "output_tokens", 0) or 0)
