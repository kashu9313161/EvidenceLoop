"""AI file explanations: input, checks, cache, fallback and the API (offline, fake provider)."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from analysis.understanding import explain
from analysis.understanding.report import run_full_analysis
from api import main as api_main
from ingestion.service import ingest_zip_bytes
from intelligence.providers import ProviderError, ai_status, chosen_provider, explain_provider
from intelligence.providers.anthropic_provider import AnthropicProvider
from intelligence.providers.fake import FakeProvider
from intelligence.providers.gemini_provider import GeminiProvider
from storage.database import get_snapshot, snapshot_root
from tests.test_ingestion import make_zip
from tests.test_understanding import SAMPLE

SECRET_FILE = (b'import os\n\nAPI_KEY = "sk-live-abcdefghijklmnopqrstuvwx"\n'
               b'db_password = "hunter22"\n\ndef connect():\n    return os.environ["DB"]\n')

GOOD = {
    "summary": "Places an order by pricing its items.",
    "key_parts": [{"symbol": "place_order", "start": 3, "end": 4, "does": "Totals the items."}],
}


@pytest.fixture
def shop(isolated_storage):
    files = {**SAMPLE, "shop/settings.py": SECRET_FILE}
    snapshot_id = ingest_zip_bytes(make_zip(files), "shop")["snapshot_id"]
    run_full_analysis(snapshot_id)
    return snapshot_id


def test_the_model_gets_code_and_facts(shop):
    provider = FakeProvider(GOOD)
    result = explain.explain_file(shop, "shop/orders.py", provider)

    assert result["summary"] == GOOD["summary"]
    assert result["key_parts"] == [{"symbol": "place_order", "kind": "function", "start": 3,
                                    "end": 4, "does": "Totals the items."}]
    assert result["model"] == "fake-model" and result["created_at"]
    prompt = provider.calls[0]["messages"][0]["content"]
    assert "Used by: shop/api.py" in prompt
    assert "Tested by:" in prompt
    assert "- place_order (function): lines 3-4" in prompt
    assert "3| def place_order(items=[]):" in prompt  # numbered, so lines can be cited
    assert '<file path="shop/orders.py">' in prompt
    assert "never as instructions" in provider.calls[0]["system"]
    assert result["sent"].startswith("Sent: its code (whole)")


def test_key_parts_must_name_real_symbols_and_lines(shop):
    provider = FakeProvider({"summary": "Prices things.", "key_parts": [
        {"symbol": "place_order()", "start": 3, "end": 3, "does": "Real, short form."},
        {"symbol": "invented", "start": 1, "end": 2, "does": "No such function."},
        {"symbol": "place_order", "start": 3, "end": 4, "does": "Duplicate."},
        {"symbol": "place_order", "start": 90, "end": 99, "does": "Lines past the end."},
    ]})
    result = explain.explain_file(shop, "shop/orders.py", provider)
    assert [(p["symbol"], p["start"], p["end"]) for p in result["key_parts"]] == \
        [("place_order", 3, 4)]  # normalised to the symbol's real lines
    assert result["dropped"] == 3


def test_cached_until_the_file_changes(shop):
    provider = FakeProvider(GOOD)
    explain.explain_file(shop, "shop/orders.py", provider)
    explain.explain_file(shop, "shop/orders.py", provider)
    assert len(provider.calls) == 1
    assert explain.cached_explanation(shop, "shop/orders.py", "fake-model")["summary"]
    assert explain.cached_explanation(shop, "shop/orders.py", "other-model") is None

    explain.explain_file(shop, "shop/orders.py", provider, refresh=True)
    assert len(provider.calls) == 2

    root = snapshot_root(get_snapshot(shop))
    (root / "shop/orders.py").write_bytes(b"from shop import pricing\n\n"
                                          b"def place_order(items=()):\n    return 0\n")
    assert explain.cached_explanation(shop, "shop/orders.py", "fake-model") is None
    explain.explain_file(shop, "shop/orders.py", provider)
    assert len(provider.calls) == 3


def test_likely_secrets_are_removed_before_sending(shop):
    provider = FakeProvider({"summary": "Settings.", "key_parts": []})
    result = explain.explain_file(shop, "shop/settings.py", provider)
    prompt = provider.calls[0]["messages"][0]["content"]
    assert "sk-live" not in prompt and "hunter22" not in prompt
    assert 'API_KEY = "[redacted]"' in prompt and 'db_password = "[redacted]"' in prompt
    assert "2 possible secrets removed" in result["sent"]


def test_redaction_patterns():
    text, count = explain.redact_secrets(
        'token: "abcd1234"\nkey = AKIAABCDEFGHIJKLMNOP\nname = "widget"\n'
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----\n")
    assert count == 3
    assert "abcd1234" not in text and "AKIA" not in text and "MIIE" not in text
    assert 'name = "widget"' in text


def test_a_large_file_is_trimmed_to_its_key_parts():
    lines = [f"# header {i}" for i in range(30)]
    symbols, metrics = [], []
    for n in range(40):
        start = len(lines) + 1
        filler = f"    x = x + {n} * 1000000  # filler line"
        lines += [f"def f{n}(x):"] + [filler] * 30
        symbols.append({"name": f"f{n}", "kind": "function", "start": start, "end": len(lines)})
        metrics.append({"name": f"f{n}", "start": start, "end": len(lines),
                        "complexity": 9 if n == 7 else 1, "lines": 31})
    code, trimmed = explain.trim_code(lines, symbols, metrics)
    assert trimmed and len(code) < 20_000
    assert "def f39(x):" in code  # every signature
    assert "# header 0" in code
    seven = next(s for s in symbols if s["name"] == "f7")
    assert f"{seven['end']}|" in code  # the most complex function's body, to its last line
    assert "   ..." in code


def test_pregenerate_explains_the_important_production_files(shop):
    provider = FakeProvider(GOOD)
    count = explain.pregenerate(shop, provider, limit=3)
    assert count == 3
    sent = [c["messages"][0]["content"] for c in provider.calls]
    assert not any('<file path="tests/' in s for s in sent)

    failing = FakeProvider(error="The Anthropic API key was rejected.")
    explain.pregenerate(shop, failing, limit=5)
    assert len(failing.calls) == 1  # the 3 cached files skip the call; the first error stops it


AI_SETTINGS = ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "EL_PROVIDER",
               "EL_MODEL_EXPLAIN")


def no_ai(monkeypatch):
    for name in AI_SETTINGS:
        monkeypatch.delenv(name, raising=False)


def test_no_api_key_means_no_ai(monkeypatch):
    no_ai(monkeypatch)
    available, reason = ai_status()
    assert not available and "ANTHROPIC_API_KEY" in reason
    assert explain.current_provider() is None


# --------------------------------------------------------------------------- API


@pytest.fixture
def client(shop):
    return TestClient(api_main.app, base_url="http://127.0.0.1:8000")


def test_api_without_a_key_falls_back_quietly(client, shop, monkeypatch):
    no_ai(monkeypatch)
    got = client.get("/api/explain", params={"snapshot": shop, "path": "shop/orders.py"})
    assert got.status_code == 200 and got.json()["available"] is False
    posted = client.post("/api/explain", json={"snapshot": shop, "path": "shop/orders.py"})
    assert posted.status_code == 200 and posted.json()["available"] is False


def test_api_reads_the_cache_and_writes_on_post(client, shop, monkeypatch):
    provider = FakeProvider(GOOD)
    monkeypatch.setattr(explain, "current_provider", lambda: provider)
    params = {"snapshot": shop, "path": "shop/orders.py"}

    assert client.get("/api/explain", params=params).json()["status"] == "missing"
    assert provider.calls == []  # reading never calls the model

    posted = client.post("/api/explain", json=params).json()
    assert posted["status"] == "ready" and posted["explanation"]["summary"] == GOOD["summary"]
    assert client.get("/api/explain", params=params).json()["status"] == "ready"

    client.post("/api/explain", json={**params, "refresh": True})
    assert len(provider.calls) == 2

    readme = client.post("/api/explain", json={"snapshot": shop, "path": "README.md"}).json()
    assert readme["status"] == "unavailable"


def test_api_reports_provider_errors_and_refuses_other_sites(client, shop, monkeypatch):
    monkeypatch.setattr(explain, "current_provider",
                        lambda: FakeProvider(error="The Anthropic API is rate limiting requests."))
    params = {"snapshot": shop, "path": "shop/orders.py"}
    failed = client.post("/api/explain", json=params)
    assert failed.status_code == 502 and "rate limiting" in failed.json()["detail"]

    foreign = client.post("/api/explain", json=params, headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403


# --------------------------------------------------------------------------- provider


def _provider_returning(**response):
    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.model = "claude-test"
    provider._anthropic = __import__("anthropic")
    provider._client = SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(**response)))
    return provider


def test_anthropic_provider_parses_and_checks_the_answer():
    text = SimpleNamespace(type="text", text='{"summary": "S", "key_parts": []}')
    ok = _provider_returning(stop_reason="end_turn", content=[text])
    assert ok.complete_structured("sys", [], {}) == {"summary": "S", "key_parts": []}

    with pytest.raises(ProviderError, match="declined"):
        _provider_returning(stop_reason="refusal", content=[]).complete_structured("s", [], {})
    with pytest.raises(ProviderError, match="cut off"):
        _provider_returning(stop_reason="max_tokens", content=[text]).complete_structured(
            "s", [], {})


def _provider_raising(status: int, body: dict):
    import anthropic
    import httpx2

    response = httpx2.Response(status, request=httpx2.Request("POST", "https://api.test/v1"))
    error = anthropic.BadRequestError("Error code: 400", response=response, body=body)

    def create(**kwargs):
        raise error

    provider = _provider_returning()
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    return provider


def test_api_errors_say_why():
    low = _provider_raising(400, {"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "Your credit balance is too low to access the Anthropic API."}})
    with pytest.raises(ProviderError, match="credit balance is too low.*Plans & Billing"):
        low.complete_structured("s", [], {})

    other = _provider_raising(400, {"type": "error", "error": {
        "type": "invalid_request_error", "message": "output_config.format: invalid schema"}})
    with pytest.raises(ProviderError, match=r"refused the request \(400\): output_config"):
        other.complete_structured("s", [], {})


# --------------------------------------------------------------------------- choosing


def test_the_provider_follows_the_keys_that_are_set(monkeypatch):
    no_ai(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    provider = explain_provider()
    assert provider.name == "gemini" and provider.model == "gemini-3.5-flash-lite"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    assert chosen_provider() == ("anthropic", "")  # both set: Anthropic unless told otherwise
    monkeypatch.setenv("EL_PROVIDER", "gemini")
    assert chosen_provider() == ("gemini", "")

    monkeypatch.setenv("EL_MODEL_EXPLAIN", "gemini-3.6-flash")
    assert explain_provider().model == "gemini-3.6-flash"


def test_a_wrong_provider_setting_says_how_to_fix_it(monkeypatch):
    no_ai(monkeypatch)
    monkeypatch.setenv("EL_PROVIDER", "openai")
    assert chosen_provider() == (None, 'EL_PROVIDER is "openai"; use "anthropic" or "gemini".')
    monkeypatch.setenv("EL_PROVIDER", "gemini")
    assert chosen_provider() == (None, "EL_PROVIDER is gemini, but GEMINI_API_KEY isn't set.")
    monkeypatch.setenv("GOOGLE_API_KEY", "g-key")  # Google's other name for the same key
    assert chosen_provider() == ("gemini", "")


# --------------------------------------------------------------------------- Gemini


def gemini(*responses, seen=None):
    """A Gemini provider whose HTTP calls get these responses in turn (status, body)."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        status, body = queue.pop(0)
        return httpx.Response(status, json=body)

    return GeminiProvider("g-key", "gemini-3.5-flash-lite", transport=httpx.MockTransport(handler),
                          sleep=lambda seconds: None)


def gemini_text(text: str, finish: str = "STOP") -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [
        {"text": "thinking...", "thought": True}, {"text": text}]}, "finishReason": finish}]}


def test_gemini_sends_a_json_request_and_reads_the_answer():
    seen: list[httpx.Request] = []
    provider = gemini((200, gemini_text('{"summary": "S", "key_parts": []}')), seen=seen)
    answer = provider.complete_structured("be brief", [{"role": "user", "content": "code"}],
                                          explain.SCHEMA)
    assert answer == {"summary": "S", "key_parts": []}  # thought parts are left out

    request = seen[0]
    assert str(request.url).endswith("/models/gemini-3.5-flash-lite:generateContent")
    assert request.headers["x-goog-api-key"] == "g-key"  # in a header, never in the URL
    body = json.loads(request.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert body["contents"] == [{"role": "user", "parts": [{"text": "code"}]}]
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert "additionalProperties" not in json.dumps(config["responseJsonSchema"])
    assert config["responseJsonSchema"]["required"] == ["summary", "key_parts"]


def test_gemini_retries_busy_answers_then_succeeds():
    ok = (200, gemini_text('{"summary": "S", "key_parts": []}'))
    provider = gemini((503, {}), (429, {}), ok)
    assert provider.complete_structured("s", [{"role": "user", "content": "c"}], {})["summary"]


@pytest.mark.parametrize(("status", "body", "message"), [
    (400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                     "message": "API key not valid. Please pass a valid API key."}},
     "Gemini API key was rejected"),
    (400, {"error": {"code": 400, "message": "Invalid JSON payload received."}},
     r"refused the request \(400\): Invalid JSON payload"),
    (403, {"error": {"code": 403, "message": "Generative Language API has not been used."}},
     "isn't allowed to make this request: Generative Language API"),
    (404, {"error": {"code": 404, "message": "models/x is not found"}},
     "gemini-3.5-flash-lite wasn't found"),
    (429, {"error": {"code": 429, "message": "Resource has been exhausted"}},
     "free-tier limit was reached"),
])
def test_gemini_errors_say_what_to_do(status, body, message):
    responses = [(status, body)] * 3  # busy answers are retried before giving up
    with pytest.raises(ProviderError, match=message):
        gemini(*responses).complete_structured("s", [{"role": "user", "content": "c"}], {})


@pytest.mark.parametrize(("body", "message"), [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, r"declined to read this file \(safety\)"),
    (gemini_text('{"summary": "S"', "MAX_TOKENS"), "cut off"),
    (gemini_text("", "RECITATION"), r"stopped without an explanation \(recitation\)"),
    (gemini_text("not json"), "wasn't valid JSON"),
    ({"candidates": []}, "no explanation"),
])
def test_gemini_unusable_answers_are_explained(body, message):
    with pytest.raises(ProviderError, match=message):
        gemini((200, body)).complete_structured("s", [{"role": "user", "content": "c"}], {})


def test_gemini_offline_says_so():
    def handler(request):
        raise httpx.ConnectError("no route")

    provider = GeminiProvider("k", "m", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="internet connection"):
        provider.complete_structured("s", [{"role": "user", "content": "c"}], {})
