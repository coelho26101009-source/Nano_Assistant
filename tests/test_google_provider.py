"""Google/Gemini as a Nano provider: wire format, secrets, routing, failover.

Every test here drives real code. The Google transport is exercised against a
fake HTTP layer that speaks the REAL ``:streamGenerateContent?alt=sse`` shape
in both directions, and the Brain tests drive the actual ``Brain.chat``
generator rather than a mock of it. Nothing asserts on source text, and no test
uses, needs or fabricates an API key.

The fake is deliberately STRICTER than a permissive stub. A fake that accepts
anything cannot fail where the server does, which is how a whole class of bugs
survived in this repository before (see FakeOllamaClient in
test_provider_fallback.py). So ``FakeGoogleTransport`` rejects the request
shapes the real API rejects: a JSON-Schema key Google's Schema type does not
accept, a ``functionResponse`` whose payload is not an object, and a
conversation that does not open on a user turn.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core import google_provider, provider_failures, providers
from core.google_provider import GoogleAPIError, GoogleChat
from core.provider_failures import FailureType, classify


# --------------------------------------------------------------------------
#  A fake that enforces what the real API enforces
# --------------------------------------------------------------------------


class GoogleRejectedRequest(AssertionError):
    """The real API would have answered 400 for this body."""


#: Keys Google's Schema type accepts. Anything else is a 400 on every request.
_ALLOWED_SCHEMA_KEYS = {
    "type", "format", "description", "nullable", "enum", "items", "properties",
    "required", "minimum", "maximum", "minLength", "maxLength", "pattern", "anyOf",
}
_ALLOWED_TYPES = {"STRING", "NUMBER", "INTEGER", "BOOLEAN", "ARRAY", "OBJECT"}


def _validate_schema(schema, path="parameters"):
    if isinstance(schema, list):
        for index, entry in enumerate(schema):
            _validate_schema(entry, f"{path}[{index}]")
        return
    if not isinstance(schema, dict):
        return
    for key, value in schema.items():
        if key not in _ALLOWED_SCHEMA_KEYS:
            raise GoogleRejectedRequest(
                f"HTTP 400: unknown Schema field {path}.{key}")
        if key == "type" and value not in _ALLOWED_TYPES:
            raise GoogleRejectedRequest(
                f"HTTP 400: {path}.type must be one of {sorted(_ALLOWED_TYPES)}, got {value!r}")
        if key == "properties":
            for name, sub in (value or {}).items():
                _validate_schema(sub, f"{path}.properties.{name}")
        if key in ("items", "anyOf"):
            _validate_schema(value, f"{path}.{key}")


def validate_body(body: dict) -> None:
    contents = body.get("contents") or []
    if contents and contents[0].get("role") != "user":
        raise GoogleRejectedRequest("HTTP 400: contents must begin with a user turn")
    for index, content in enumerate(contents):
        if content.get("role") not in ("user", "model"):
            raise GoogleRejectedRequest(
                f"HTTP 400: contents[{index}].role must be 'user' or 'model'")
        for part in content.get("parts") or []:
            response = (part.get("functionResponse") or {}).get("response")
            if "functionResponse" in part and not isinstance(response, dict):
                raise GoogleRejectedRequest(
                    "HTTP 400: functionResponse.response must be an object, got "
                    f"{type(response).__name__}")
    for tool in body.get("tools") or []:
        for declaration in tool.get("functionDeclarations") or []:
            if "parameters" in declaration:
                _validate_schema(declaration["parameters"])


def sse(frames: list[dict]) -> list[str]:
    return [f"data: {json.dumps(frame, ensure_ascii=False)}" for frame in frames]


def text_frame(text: str) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}


def call_frame(name: str, args: dict) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [
        {"functionCall": {"name": name, "args": args}}]}}]}


def usage_frame(prompt: int, completion: int) -> dict:
    return {"usageMetadata": {"promptTokenCount": prompt,
                              "candidatesTokenCount": completion,
                              "totalTokenCount": prompt + completion}}


class _FakeStream:
    def __init__(self, status: int, lines: list[str], body: bytes = b""):
        self.status_code = status
        self._lines = lines
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def aread(self):
        return self._body

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeGoogleTransport:
    """Stands in for httpx.AsyncClient for one scripted exchange."""

    def __init__(self, *, lines=None, status=200, error_body=b"", capture=None):
        self.lines = lines or []
        self.status = status
        self.error_body = error_body
        self.capture = capture if capture is not None else []
        self.headers_seen: list[dict] = []
        self.params_seen: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def stream(self, method, url, headers=None, params=None, json=None, **_kw):
        validate_body(json or {})
        self.capture.append({"url": url, "body": json})
        self.headers_seen.append(dict(headers or {}))
        self.params_seen.append(dict(params or {}))
        return _FakeStream(self.status, self.lines, self.error_body)


def run(coro):
    return asyncio.run(coro)


async def drain(chat: GoogleChat, body: dict, collector: dict) -> str:
    return "".join([piece async for piece in chat.stream("m", body, collector)])


# --------------------------------------------------------------------------
#  Configuration and credentials
# --------------------------------------------------------------------------


def test_google_and_groq_use_separate_credential_identifiers():
    """One provider's key must never satisfy or overwrite another's."""
    assert google_provider.GOOGLE_SECRET_NAME != providers.GROQ_SECRET_NAME


def test_the_key_travels_in_a_header_and_never_in_the_url(monkeypatch):
    """A ?key= URL lands in httpx exception text, proxy logs and tracebacks."""
    transport = FakeGoogleTransport(lines=sse([text_frame("olá")]))
    monkeypatch.setattr(google_provider.httpx, "AsyncClient", lambda **kw: transport)

    chat = GoogleChat("secret-value-not-a-real-key")
    run(drain(chat, {"contents": [{"role": "user", "parts": [{"text": "oi"}]}]}, {}))

    assert transport.capture[0]["url"].count("key=") == 0
    assert transport.headers_seen[0]["x-goog-api-key"] == "secret-value-not-a-real-key"
    assert transport.params_seen[0] == {"alt": "sse"}


def test_model_discovery_also_keeps_the_key_out_of_the_url(monkeypatch):
    """The listing endpoint is a second place the key could leak, and it is the
    one Settings calls on every status refresh."""
    seen: dict = {}

    class _Response:
        status_code = 200
        is_success = True

        @staticmethod
        def json():
            return {"models": []}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, url, headers=None, params=None, **_kw):
            seen["url"] = url
            seen["headers"] = dict(headers or {})
            seen["params"] = dict(params or {})
            return _Response()

    monkeypatch.setattr(google_provider.httpx, "Client", lambda **kw: _Client())
    google_provider.list_google_models("secret-listing-key")

    assert "key" not in seen["params"], "the API key was put in the query string"
    assert "key=" not in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "secret-listing-key"


def test_an_unconfigured_google_is_reported_without_contacting_anything(monkeypatch):
    monkeypatch.setattr(google_provider.secret_store, "get_secret", lambda name: "")

    def _explode(*_a, **_k):
        raise AssertionError("describe_google contacted the API with no key")

    monkeypatch.setattr(google_provider, "list_google_models", _explode)
    payload = google_provider.describe_google("", "")
    assert payload["state"] == providers.ProviderState.SETUP_REQUIRED.value
    assert payload["secret"]["configured"] is False


def test_the_status_payload_never_carries_the_secret(monkeypatch):
    # Deliberately NOT shaped like a real credential. A key-shaped literal in a
    # test is what teaches people to ignore their own secret scanner, and the
    # property under test -- "this exact string must not appear in the payload"
    # -- holds for any string at all.
    key = "FIXTURE-CREDENTIAL-VALUE-0000"
    monkeypatch.setattr(google_provider.secret_store, "get_secret",
                        lambda name: key if name == google_provider.GOOGLE_SECRET_NAME else "")
    monkeypatch.setattr(google_provider, "list_google_models",
                        lambda *a, **k: ([{"id": "m-fast", "display_name": "M"}], None))

    payload = google_provider.describe_google("m-fast", "m-fast")
    assert key not in json.dumps(payload, ensure_ascii=False)
    assert payload["secret"]["masked"] and key not in payload["secret"]["masked"]


def test_a_transport_failure_message_never_quotes_the_request(monkeypatch):
    """httpx puts the full URL in its message; nothing derived from the request
    may reach a user-facing string."""
    import httpx

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def stream(self, *_a, **_kw):
            raise httpx.ConnectError("failed connecting to https://host/path?key=LEAK")

    monkeypatch.setattr(google_provider.httpx, "AsyncClient", lambda **kw: _Boom())
    chat = GoogleChat("k")
    with pytest.raises(GoogleAPIError) as excinfo:
        run(drain(chat, {"contents": []}, {}))
    assert "LEAK" not in str(excinfo.value)


# --------------------------------------------------------------------------
#  Request shaping
# --------------------------------------------------------------------------


def test_system_instruction_is_sent_separately_for_a_family_that_takes_one():
    body = google_provider.build_request(
        "gemini-x-flash",
        [{"role": "system", "content": "REGRAS"}, {"role": "user", "content": "olá"}],
        None)
    validate_body(body)
    assert body["systemInstruction"]["parts"][0]["text"] == "REGRAS"
    assert body["contents"][0]["parts"][0]["text"] == "olá"


def test_system_instruction_is_folded_in_for_a_family_that_takes_none(monkeypatch):
    """A family that takes no separate system instruction still RECEIVES the
    rules -- moved into the first user turn, never dropped.

    The family list is patched rather than assumed. This test previously named
    Gemma, which turned an unverified guess into an assertion; asked live, both
    Gemma 4 models accept systemInstruction and demonstrably receive its
    contents. The MECHANISM is what has to work, so the mechanism is what is
    tested, and the real Gemma behaviour is asserted separately below.
    """
    monkeypatch.setattr(google_provider, "_NO_SYSTEM_INSTRUCTION_FAMILIES", ("legacy",))
    body = google_provider.build_request(
        "legacy-x-it",
        [{"role": "system", "content": "REGRAS"}, {"role": "user", "content": "olá"}],
        None)
    validate_body(body)
    assert "systemInstruction" not in body
    texts = [part["text"] for part in body["contents"][0]["parts"]]
    assert texts == ["REGRAS", "olá"]


def test_tools_are_withheld_from_a_family_that_cannot_call_them(monkeypatch):
    """Sending them is a 400, and BAD_REQUEST never falls back -- so an
    unsupported family would break the turn instead of degrading."""
    tools = [{"type": "function", "function": {
        "name": "pc_volume_get", "description": "v",
        "parameters": {"type": "object", "properties": {"x": {"type": "string"}}}}}]
    assert "tools" in google_provider.build_request("gemini-x-flash", [], tools)
    monkeypatch.setattr(google_provider, "_NO_TOOL_CALLING_FAMILIES", ("legacy",))
    assert "tools" not in google_provider.build_request("legacy-x-it", [], tools)


# --------------------------------------------------------------------------
#  Gemma, as measured rather than as assumed
# --------------------------------------------------------------------------
#
# Every assertion below was confirmed against the live account during the
# Google-versus-Groq benchmark. They are recorded here because the previous
# conservative guesses were not merely cautious -- they were wrong in a way
# that made Nano fabricate results, and only a live call could show it.

def test_gemma_is_offered_the_tools_it_can_actually_call():
    """Asked live, gemma-4-31b-it and gemma-4-26b-a4b-it both return a correct
    native functionCall with structured arguments.

    Withholding the tools was not the safe option. With nothing to call, Gemma
    answered "Volume definido para 30%" -- reporting an action that never
    happened, which is the exact failure Nano must never produce.
    """
    tools = [{"type": "function", "function": {
        "name": "pc_volume_set", "description": "v",
        "parameters": {"type": "object",
                       "properties": {"level": {"type": "integer"}}}}}]
    body = google_provider.build_request("gemma-4-31b-it", [], tools)
    assert "tools" in body
    declarations = body["tools"][0]["functionDeclarations"]
    assert [d["name"] for d in declarations] == ["pc_volume_set"]
    assert body["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"


def test_gemma_receives_a_real_system_instruction():
    body = google_provider.build_request(
        "gemma-4-31b-it",
        [{"role": "system", "content": "REGRAS"}, {"role": "user", "content": "olá"}],
        None)
    validate_body(body)
    assert body["systemInstruction"]["parts"][0]["text"] == "REGRAS"
    assert body["contents"][0]["parts"][0]["text"] == "olá"


def test_gemma_is_never_sent_a_reasoning_budget_despite_claiming_to_think():
    """ListModels reports "thinking": true for both Gemma 4 models, and the
    endpoint answers 400 "Thinking budget is not supported for this model."

    Metadata describes what a model does, not which knobs the endpoint accepts,
    so the metadata alone must not be allowed to compose the request.
    """
    assert not google_provider.supports_thinking("gemma-4-31b-it",
                                                 metadata={"thinking": True})
    assert not google_provider.supports_thinking("gemma-4-26b-a4b-it",
                                                 metadata={"thinking": True})
    for task in ("SMALL_TALK", "QUESTION", "ACTION", "COMPLEX"):
        body = google_provider.build_request("gemma-4-31b-it", [], None, task=task,
                                             metadata={"thinking": True})
        assert "thinkingConfig" not in body["generationConfig"], task

    # The guard is family-scoped and must not disarm reasoning elsewhere.
    assert google_provider.supports_thinking("gemini-3.5-flash-lite",
                                             metadata={"thinking": True})


def test_real_nano_tool_schemas_survive_translation():
    """The whole live registry, against the rules the real API enforces."""
    from core.plugin_loader import get_all_tools, load_all_plugins

    load_all_plugins()
    body = google_provider.build_request("gemini-x-flash", [], get_all_tools())
    validate_body(body)                       # raises if any schema is rejected
    declared = {d["name"] for d in body["tools"][0]["functionDeclarations"]}
    advertised = {(t.get("function") or {}).get("name") for t in get_all_tools()}
    assert declared == advertised, "a tool was lost or renamed in translation"


def test_a_schema_default_becomes_a_description_rather_than_being_dropped():
    tools = [{"type": "function", "function": {
        "name": "t", "description": "d",
        "parameters": {"type": "object", "properties": {
            "days": {"type": "integer", "default": 7}}}}}]
    body = google_provider.build_request("gemini-x-flash", [], tools)
    validate_body(body)
    days = body["tools"][0]["functionDeclarations"][0]["parameters"]["properties"]["days"]
    assert "default" not in days
    assert "7" in days["description"]


def test_a_tool_result_is_matched_back_to_its_call_by_name():
    """Google identifies a function response by NAME. Guessing the wrong one
    attaches a result to the wrong call."""
    messages = [
        {"role": "user", "content": "sobe o volume"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_7", "type": "function",
             "function": {"name": "pc_volume_set", "arguments": '{"level": 50}'}}]},
        {"role": "tool", "tool_call_id": "call_7",
         "content": json.dumps({"success": True, "output": {"level": 50}})},
    ]
    contents, _ = google_provider.to_contents(messages)
    validate_body({"contents": contents})
    assert contents[1]["parts"][0]["functionCall"] == {
        "name": "pc_volume_set", "args": {"level": 50}}
    response = contents[2]["parts"][0]["functionResponse"]
    assert response["name"] == "pc_volume_set"
    assert response["response"]["success"] is True


def test_a_non_json_tool_result_is_carried_rather_than_rejected():
    """Untrusted external content arrives as JSON plus a fenced block. Google
    needs an object; the fence must survive rather than be discarded."""
    messages = [
        {"role": "user", "content": "lê a página"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c0", "type": "function",
             "function": {"name": "web_fetch", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c0",
         "content": '{"success": true}\n<<<UNTRUSTED>>> texto externo'},
    ]
    contents, _ = google_provider.to_contents(messages)
    validate_body({"contents": contents})
    payload = contents[2]["parts"][0]["functionResponse"]["response"]
    assert isinstance(payload, dict)
    assert "texto externo" in json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize("raw,expected", [
    ('{"success": true}\n<<<UNTRUSTED>>> externo', "unparseable"),
    ('["a", "b"]', "valid JSON that is not an object"),
    ('"apenas texto"', "a bare JSON string"),
    ("42", "a bare number"),
])
def test_every_tool_result_shape_reaches_google_as_an_object(raw, expected):
    """Google rejects a functionResponse whose response is not an object, and a
    400 is BAD_REQUEST, which never falls back. Three of these four shapes are
    produced by real tool results."""
    messages = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c0", "type": "function", "function": {"name": "t", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c0", "content": raw},
    ]
    contents, _ = google_provider.to_contents(messages)
    validate_body({"contents": contents})       # raises if it is not an object
    payload = contents[2]["parts"][0]["functionResponse"]["response"]
    assert isinstance(payload, dict), f"{expected} did not survive as an object"


def test_a_tool_call_with_unparseable_arguments_is_dropped_not_emptied():
    """An empty argument map invites the model to re-issue the call with
    arguments of its own invention -- a different call the per-turn ledger
    would not recognise as a repeat."""
    messages = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "a", "tool_calls": [
            {"id": "c0", "type": "function",
             "function": {"name": "t", "arguments": "{not json"}}]},
    ]
    contents, _ = google_provider.to_contents(messages)
    parts = contents[1]["parts"]
    assert all("functionCall" not in part for part in parts)


def test_a_reasoning_budget_is_only_sent_for_a_model_the_account_says_supports_it():
    """A guess here is a 400 on every message, and BAD_REQUEST never falls back."""
    unknown = google_provider.build_request("gemini-x-flash", [], None, task="COMPLEX")
    assert "thinkingConfig" not in unknown["generationConfig"]

    known = google_provider.build_request(
        "gemini-x-flash", [], None, task="COMPLEX", metadata={"thinking": True})
    assert known["generationConfig"]["thinkingConfig"]["thinkingBudget"] > 0


def test_ordinary_conversation_buys_no_reasoning_budget():
    """No extra reasoning is bought -- and, crucially, none is ASKED FOR as a zero.

    The previous version of this check asserted ``thinkingBudget == 0``, which
    passed in the test suite and 400-ed against the real API: measured on the
    live account, gemini-3.5-flash-lite rejects a zero budget with
    INVALID_ARGUMENT while accepting a positive one, a thinkingLevel, or no
    thinkingConfig at all. Because SMALL_TALK, QUESTION and ACTION cover
    essentially all of Nano's traffic, and BAD_REQUEST is the one failure class
    that never falls back, every single message to that model failed.

    So the contract is now guarded more tightly than before: ordinary
    conversation must buy no reasoning budget AND must not send a budget field
    the model can reject.
    """
    for task in ("SMALL_TALK", "QUESTION", "ACTION"):
        body = google_provider.build_request(
            "gemini-x-flash", [], None, task=task, metadata={"thinking": True})
        assert "thinkingConfig" not in body["generationConfig"]


def test_a_zero_reasoning_budget_is_never_sent_to_any_model():
    """The exact request shape that broke every turn, guarded on its own.

    Kept separate from the task-class test above so that a future change to the
    budget TABLE cannot quietly reintroduce the invalid argument: whatever the
    table says, a budget of zero is expressed by omission and never on the wire.
    """
    from core import google_provider as gp

    for task, budget in gp._THINKING_BUDGET.items():
        body = gp.build_request("gemini-x-flash", [], None, task=task,
                                metadata={"thinking": True})
        config = body["generationConfig"].get("thinkingConfig")
        if budget <= 0:
            assert config is None, f"{task} sent {config}"
        else:
            assert config == {"thinkingBudget": budget}

    unknown_task = gp.build_request("gemini-x-flash", [], None, task="NOT_A_TASK",
                                    metadata={"thinking": True})
    assert "thinkingConfig" not in unknown_task["generationConfig"]


# --------------------------------------------------------------------------
#  Streaming and response shaping
# --------------------------------------------------------------------------


def test_streaming_yields_text_as_it_arrives(monkeypatch):
    transport = FakeGoogleTransport(
        lines=sse([text_frame("Olá"), text_frame(", tudo"), text_frame(" bem?")]))
    monkeypatch.setattr(google_provider.httpx, "AsyncClient", lambda **kw: transport)

    collector: dict = {}
    body = google_provider.build_request("gemini-x-flash", [{"role": "user", "content": "oi"}], None)
    answer = run(drain(GoogleChat("k"), body, collector))
    assert answer == "Olá, tudo bem?"
    assert collector["first_token_at"] is not None


def test_a_thinking_part_is_never_shown(monkeypatch):
    """Leaking the model's private reasoning into the answer is a defect this
    project has already shipped once (qwen3's raw <think> text)."""
    frame = {"candidates": [{"content": {"role": "model", "parts": [
        {"text": "raciocínio interno", "thought": True},
        {"text": "resposta"}]}}]}
    transport = FakeGoogleTransport(lines=sse([frame]))
    monkeypatch.setattr(google_provider.httpx, "AsyncClient", lambda **kw: transport)
    answer = run(drain(GoogleChat("k"), {"contents": []}, {}))
    assert answer == "resposta"


def test_a_tool_call_is_normalised_into_nano_s_wire_shape(monkeypatch):
    """The rest of Nano stores arguments as a JSON STRING; Google hands back a
    decoded map."""
    transport = FakeGoogleTransport(
        lines=sse([call_frame("pc_volume_set", {"level": 30}), usage_frame(120, 8)]))
    monkeypatch.setattr(google_provider.httpx, "AsyncClient", lambda **kw: transport)

    collector: dict = {}
    run(drain(GoogleChat("k"), {"contents": []}, collector))
    assert collector["tool_calls"] == [
        {"id": "call_0", "name": "pc_volume_set", "args": '{"level": 30}'}]
    assert json.loads(collector["tool_calls"][0]["args"]) == {"level": 30}
    assert collector["usage"]["prompt_tokens"] == 120
    assert collector["usage"]["completion_tokens"] == 8


def test_cancellation_propagates_instead_of_being_swallowed(monkeypatch):
    class _Cancelling(_FakeStream):
        async def aiter_lines(self):
            yield "data: " + json.dumps(text_frame("parcial"))
            raise asyncio.CancelledError()

    class _Transport(FakeGoogleTransport):
        def stream(self, *a, **kw):
            return _Cancelling(200, [])

    monkeypatch.setattr(google_provider.httpx, "AsyncClient", lambda **kw: _Transport())
    with pytest.raises(asyncio.CancelledError):
        run(drain(GoogleChat("k"), {"contents": []}, {}))


# --------------------------------------------------------------------------
#  Failure taxonomy
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status,expected", [
    (429, FailureType.RATE_LIMIT),
    (401, FailureType.AUTH_ERROR),
    (403, FailureType.AUTH_ERROR),
    (404, FailureType.MODEL_UNAVAILABLE),
    (400, FailureType.BAD_REQUEST),
    (500, FailureType.SERVER_ERROR),
    (503, FailureType.SERVER_ERROR),
])
def test_google_http_statuses_land_in_the_shared_taxonomy(monkeypatch, status, expected):
    body = json.dumps({"error": {"code": status, "status": "X", "message": "m"}}).encode()
    transport = FakeGoogleTransport(status=status, error_body=body)
    monkeypatch.setattr(google_provider.httpx, "AsyncClient", lambda **kw: transport)

    with pytest.raises(GoogleAPIError) as excinfo:
        run(drain(GoogleChat("k"), {"contents": []}, {}))
    assert classify(excinfo.value, provider="google").type is expected


def test_a_google_429_carries_its_retry_delay_into_the_cooldown(monkeypatch):
    """Google puts the wait in error.details[].retryDelay, not in a header."""
    body = json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                 "message": "quota", "details": [
                                     {"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                      "retryDelay": "27s"}]}}).encode()
    transport = FakeGoogleTransport(status=429, error_body=body)
    monkeypatch.setattr(google_provider.httpx, "AsyncClient", lambda **kw: transport)

    with pytest.raises(GoogleAPIError) as excinfo:
        run(drain(GoogleChat("k"), {"contents": []}, {}))
    failure = classify(excinfo.value, provider="google")
    assert failure.type is FailureType.RATE_LIMIT
    assert failure.rate_limit["retry_after_seconds"] == 27.0
    assert failure.cooldown_seconds() == 27.0


def test_a_timeout_is_a_timeout_not_an_unknown_error(monkeypatch):
    import httpx

    class _Timeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def stream(self, *_a, **_kw):
            raise httpx.ReadTimeout("too slow")

    monkeypatch.setattr(google_provider.httpx, "AsyncClient", lambda **kw: _Timeout())
    with pytest.raises(GoogleAPIError) as excinfo:
        run(drain(GoogleChat("k"), {"contents": []}, {}))
    assert classify(excinfo.value, provider="google").type is FailureType.TIMEOUT


def test_the_failure_sentence_names_the_provider_that_actually_failed():
    """Telling somebody their Groq key was refused when Gemini's was sends them
    to fix the wrong credential."""
    google = provider_failures.ProviderFailure(FailureType.AUTH_ERROR, "google").user_message()
    groq = provider_failures.ProviderFailure(FailureType.AUTH_ERROR, "groq").user_message()
    assert "Google" in google and "Groq" not in google
    assert "Groq" in groq and "Google" not in groq


def test_each_provider_cools_down_on_its_own_clock():
    """A rate-limited Gemini must not stop Nano asking Groq -- routing to the
    next provider is precisely what a second provider is for."""
    provider_failures.reset_all_cooldowns()
    try:
        failure = provider_failures.ProviderFailure(
            FailureType.RATE_LIMIT, "google", 429, "", {"retry_after_seconds": 30.0})
        provider_failures.cooldown_for("google").note_failure(failure)
        assert provider_failures.cooldown_for("google").is_cooling_down() is True
        assert provider_failures.cooldown_for("groq").is_cooling_down() is False
    finally:
        provider_failures.reset_all_cooldowns()


def test_the_groq_breaker_is_the_registry_entry_not_a_copy():
    assert provider_failures.cooldown_for("groq") is provider_failures.GROQ_COOLDOWN
    assert provider_failures.cooldown_for("google") is provider_failures.GOOGLE_COOLDOWN


# --------------------------------------------------------------------------
#  Model discovery
# --------------------------------------------------------------------------


def test_only_chat_capable_models_are_offered():
    listing = [
        {"name": "models/gemini-x-flash", "displayName": "F",
         "supportedGenerationMethods": ["generateContent", "streamGenerateContent"]},
        {"name": "models/text-embedding-004", "displayName": "E",
         "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/imagen-x", "displayName": "I",
         "supportedGenerationMethods": ["predict"]},
    ]
    records = [r for r in (google_provider._model_record(item) for item in listing) if r]
    assert google_provider.model_ids(records) == ["gemini-x-flash"]


def test_streaming_is_reported_from_generate_content_support():
    """ListModels does not enumerate the streaming variant, and reading it as
    an absent capability made Nano deny a capability it has.

    MEASURED, NOT ASSUMED. Not one of the 40 chat models on the account lists
    ``streamGenerateContent`` -- while ``GoogleChat.stream``, Nano's only
    Google code path, POSTs to ``:streamGenerateContent`` and has streamed
    answers from those same models since it shipped. gemini-3.8-flash
    advertises ["generateContent", "countTokens", "createCachedContent",
    "batchGenerateContent"] and answered a live SSE request with text and a
    native functionCall.

    The old derivation therefore reported ``streaming: false`` for 100% of an
    account that streams 100% of the time -- a limitation Nano does not have,
    stated to the user in a field the Settings page is entitled to render.
    """
    record = google_provider._model_record({
        "name": "models/gemini-3-x-flash", "displayName": "X",
        "supportedGenerationMethods": ["generateContent", "countTokens",
                                       "createCachedContent", "batchGenerateContent"]})
    assert record["streaming"] is True


def test_a_listing_that_does_name_the_streaming_method_is_still_streaming():
    """The other API surface must not regress: naming it explicitly still counts."""
    record = google_provider._model_record({
        "name": "models/gemini-3-x-flash",
        "supportedGenerationMethods": ["generateContent", "streamGenerateContent"]})
    assert record["streaming"] is True


def test_a_model_that_cannot_generate_content_is_not_offered_as_streaming():
    """Prove the flag can still be False -- a check that cannot fail is not one.

    A listing naming neither method is dropped from the catalogue entirely, so
    the only way to observe a False is to ask the helper directly.
    """
    assert google_provider._supports_generate_content(["embedContent"]) is False
    assert google_provider._supports_generate_content(["predict"]) is False
    # Said nothing at all: an unstated capability is not a denied one.
    assert google_provider._supports_generate_content([]) is True


def test_a_model_id_loses_its_models_prefix():
    record = google_provider._model_record(
        {"name": "models/gemini-x-flash", "supportedGenerationMethods": ["generateContent"]})
    assert record["id"] == "gemini-x-flash"


def test_the_module_pins_no_concrete_model_id():
    """Concrete ids are DISCOVERED. Pinning one is what left this project
    calling a decommissioned model and 404-ing on every message."""
    import ast
    from pathlib import Path

    source = Path(google_provider.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    # Strip docstrings: they explain the hazard and quote example ids.
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not body or not isinstance(node, (ast.Module, ast.ClassDef,
                                             ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            node.body = body[1:] or [ast.Pass()]
    stripped = ast.unparse(tree)

    for assignment in ("DEFAULT_MODEL", "DEFAULT_FAST_MODEL", "DEFAULT_COMPLEX_MODEL"):
        assert f"{assignment} =" not in stripped, (
            f"google_provider declares {assignment}; ids must come from the account")


def test_settings_yaml_ships_no_google_model_id():
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    config = yaml.safe_load((root / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert config.get("google_fast_model") == ""
    assert config.get("google_complex_model") == ""
