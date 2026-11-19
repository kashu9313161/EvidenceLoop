"""Google Gemini through its REST API (generateContent) with JSON output, using httpx."""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import httpx

from intelligence.providers.anthropic_provider import api_message
from intelligence.providers.base import Completion, ProviderError

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_STATUSES = {429, 500, 503, 504}
RETRIES = 2


def gemini_schema(schema: object) -> object:
    """The JSON schema without `additionalProperties`, which Gemini's schema subset may not
    accept. The answer's shape is checked after it arrives either way."""
    if isinstance(schema, dict):
        return {k: gemini_schema(v) for k, v in schema.items() if k != "additionalProperties"}
    if isinstance(schema, list):
        return [gemini_schema(v) for v in schema]
    return schema


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.model = model
        self._key = api_key
        self._timeout = timeout
        self._transport = transport
        self._sleep = sleep

    def complete_structured(self, system: str, messages: list[dict], schema: dict,
                            max_tokens: int = 4000) -> dict:
        return self.complete(system, messages, schema, max_tokens).data

    def complete(self, system: str, messages: list[dict], schema: dict,
                 max_tokens: int = 4000) -> Completion:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "user" if m["role"] == "user" else "model",
                 "parts": [{"text": m["content"]}]}
                for m in messages
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": gemini_schema(schema),
                "maxOutputTokens": max_tokens,
            },
        }
        response = self._post(body)
        data = self._answer(response)
        usage = response.json().get("usageMetadata") or {}
        return Completion(
            data,
            input_tokens=int(usage.get("promptTokenCount") or 0),
            # Thinking is billed as output.
            output_tokens=int(usage.get("candidatesTokenCount") or 0)
            + int(usage.get("thoughtsTokenCount") or 0),
        )

    def _post(self, body: dict) -> httpx.Response:
        url = GEMINI_URL.format(model=self.model)
        headers = {"x-goog-api-key": self._key}
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            for attempt in range(RETRIES + 1):
                try:
                    response = client.post(url, headers=headers, json=body)
                except httpx.TimeoutException as exc:
                    raise ProviderError("The Gemini API took too long to answer. "
                                        "Try again.") from exc
                except httpx.TransportError as exc:
                    raise ProviderError("Couldn't reach the Gemini API. "
                                        "Check your internet connection.") from exc
                if response.status_code not in RETRY_STATUSES or attempt == RETRIES:
                    break
                self._sleep(self._retry_after(response, attempt))
        if response.status_code != 200:
            raise ProviderError(self._refusal(response))
        return response

    @staticmethod
    def _retry_after(response: httpx.Response, attempt: int) -> float:
        try:
            wait = float(response.headers.get("retry-after", ""))
        except ValueError:
            wait = 2.0 * (attempt + 1)
        return min(max(wait, 0.5), 10.0)

    def _refusal(self, response: httpx.Response) -> str:
        try:
            reason = api_message(response.json())
        except ValueError:
            reason = ""
        status = response.status_code
        lowered = reason.lower()
        if "api key" in lowered and ("not valid" in lowered or "invalid" in lowered) \
                or status == 401:
            return "The Gemini API key was rejected. Check GEMINI_API_KEY."
        if status == 403:
            return ("This Gemini API key isn't allowed to make this request"
                    + (f": {reason}" if reason else ". Check the key in Google AI Studio."))
        if status == 404:
            return f"The model {self.model} wasn't found. Check EL_MODEL_EXPLAIN."
        if status == 429:
            return ("The Gemini free-tier limit was reached. Wait a minute and try again "
                    "(the daily limit resets at midnight Pacific time).")
        if status >= 500:
            return f"The Gemini API is having problems ({status}). Try again shortly."
        return (f"The Gemini API refused the request ({status})"
                + (f": {reason}" if reason else "."))

    @staticmethod
    def _answer(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("The Gemini API sent an unreadable answer. Try again.") from exc
        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            raise ProviderError(f"Gemini declined to read this file ({blocked.lower()}).")
        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderError("Gemini returned no explanation. Try again.")
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        if finish == "MAX_TOKENS":
            raise ProviderError("The explanation was cut off before it finished. Try again.")
        if finish not in (None, "STOP", "FINISH_REASON_UNSPECIFIED"):
            raise ProviderError(f"Gemini stopped without an explanation ({finish.lower()}).")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise ProviderError("Gemini returned no explanation. Try again.")
        try:
            answer = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError("Gemini's answer wasn't valid JSON. Try again.") from exc
        if not isinstance(answer, dict):
            raise ProviderError("Gemini's answer had the wrong shape. Try again.")
        return answer
