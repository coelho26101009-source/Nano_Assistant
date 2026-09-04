"""Mistral as a Nano provider: wire format, secrets, routing, failover.

Every test here drives real code. The transport is exercised against a fake
HTTP layer that speaks the REAL ``/v1/chat/completions`` SSE shape in both
directions, and the Brain tests drive the actual ``Brain.chat`` generator rather
than a mock of it. Nothing asserts on source text, and no test uses, needs or
fabricates an API key.

The fake is deliberately STRICTER than a permissive stub, for the reason
``FakeGoogleTransport`` is: a fake that accepts anything cannot fail where the
server does. ``FakeMistralTransport`` therefore rejects the request shapes the
real API rejects -- a tool call id that is not exactly nine alphanumeric
characters, a null ``tool_choice``, a message with neither content nor tool
calls -- because each of those is a 422, a 422 classifies as BAD_REQUEST, and
BAD_REQUEST is the one failure class Nano never fails over on. A wire-format
mistake there does not degrade the provider, it removes it.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from core import mistral_provider, provider_failures, providers, secret_store
from core.mistral_provider import MistralAPIError, MistralChat
from core.provider_failures import FailureType, classify


# --------------------------------------------------------------------------
#  A fake that enforces what the real API enforces
# --------------------------------------------------------------------------


class MistralRejectedRequest(AssertionError):
    """The real API would have answered 422 for this body."""


def _validate_tool_call_id(value: str, where: str) -> None:
    if len(value) != 9 or not value.isalnum():
        raise MistralRejectedRequest(
            f"HTTP 422: {where} must be exactly 9 alphanumeric characters, got {value!r}")


def validate_body(body: dict) -> None:
    if "tool_choice" in body and body["tool_choice"] is None:
        raise MistralRejectedRequest("HTTP 422: tool_choice may not be null")
    if "tools" in body and not body["tools"]:
        raise MistralRejectedRequest("HTTP 422: tools may not be an empty list")

    for index, message in enumerate(body.get("messages") or []):
        role = message.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            raise MistralRejectedRequest(f"HTTP 422: messages[{index}].role={role!r}")
        if role == "tool":
            _validate_tool_call_id(str(message.get("tool_call_id") or ""),
                                   f"messages[{index}].tool_call_id")
        if role == "assistant":
            calls = message.get("tool_calls") or []
            for position, call in enumerate(calls):
                _validate_tool_call_id(
                    str(call.get("id") or ""),
                    f"messages[{index}].tool_calls[{position}].id")
                arguments = (call.get("function") or {}).get("arguments")
                if not isinstance(arguments, str):
                    raise MistralRejectedRequest(
                        f"HTTP 422: messages[{index}].tool_calls[{position}]"
                        ".function.arguments must be a string")
            if not calls and not message.get("content"):
                raise MistralRejectedRequest(
                    f"HTTP 422: messages[{index}] carries neither content nor tool calls")


def sse(frames: list[dict]) -> list[str]:
    return [f"data: {json.dumps(frame, ensure_ascii=False)}" for frame in frames] + ["data: [DONE]"]


def text_frame(text: str) -> dict:
    return {"choices": [{"index": 0, "delta": {"content": text}}]}


def call_frames(name: str, args: dict, *, call_id: str = "abcdefghi") -> list[dict]:
    """A tool call the way it really arrives: name first, arguments in pieces."""
    encoded = json.dumps(args, ensure_ascii=False)
    head, tail = encoded[: len(encoded) // 2], encoded[len(encoded) // 2:]
    return [
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": call_id, "type": "function",
             "function": {"name": name, "arguments": head}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": tail}}]}}]},
    ]


def usage_frame(prompt: int, completion: int) -> dict:
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                      "total_tokens": prompt + completion}}


class _FakeStream:
    def __init__(self, status: int, lines: list[str], body: bytes = b"",
                 headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}
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


class FakeMistralTransport:
    """Stands in for httpx.AsyncClient for one scripted exchange."""

    def __init__(self, *, lines=None, status=200, error_body=b"", headers=None,
                 raises: Exception | None = None):
        self.lines = lines or []
        self.status = status
        self.error_body = error_body
        self.headers = headers or {}
        self.raises = raises
        self.capture: list[dict] = []
        self.headers_seen: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def stream(self, method, url, headers=None, json=None, **_kw):
        validate_body(json or {})
        self.capture.append({"url": url, "method": method, "body": json})
        self.headers_seen.append(dict(headers or {}))
        if self.raises is not None:
            raise self.raises
        return _FakeStream(self.status, self.lines, self.error_body, self.headers)


def run(coro):
    return asyncio.run(coro)


async def drain(chat: MistralChat, body: dict, collector: dict) -> str:
    return "".join([piece async for piece in chat.stream("m", body, collector)])


def stream_with(monkeypatch, **kwargs) -> tuple[FakeMistralTransport, str, dict]:
    """Run one scripted exchange through the real transport."""
    transport = FakeMistralTransport(**kwargs)
    monkeypatch.setattr(mistral_provider.httpx, "AsyncClient", lambda **kw: transport)
    collector: dict = {}
    body = mistral_provider.build_request("m", [{"role": "user", "content": "oi"}], None)
    text = run(drain(MistralChat("not-a-real-key"), body, collector))
    return transport, text, collector


TOOLS = [{"type": "function", "function": {
    "name": "pc_volume_set", "description": "define o volume",
    "parameters": {"type": "object", "properties": {"level": {"type": "integer"}},
                   "required": ["level"]}}}]


# --------------------------------------------------------------------------
#  Credentials
# --------------------------------------------------------------------------


def test_every_provider_uses_a_separate_credential_identifier():
    """One provider's key must never satisfy or overwrite another's, so
    removing one cannot silently disable the others."""
    names = list(providers.CLOUD_SECRET_NAMES.values())
    assert len(set(names)) == len(names)
    assert mistral_provider.MISTRAL_SECRET_NAME == "mistral_api_key"


def test_the_documented_environment_variables_are_really_read(monkeypatch):
    """Both documented fallbacks are read, so an existing .env keeps working."""
    monkeypatch.setattr(secret_store, "_read_store", lambda: {})
    for name in ("NANO_MISTRAL_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv("NANO_MISTRAL_API_KEY", raising=False)
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        monkeypatch.setenv(name, f"value-from-{name}")
        assert mistral_provider.mistral_api_key() == f"value-from-{name}"


def test_no_environment_variable_serves_two_providers():
    """A name shared between two providers would mean one vendor's key
    silently authenticating requests to another, and removing one credential
    disabling a provider the user never touched.

    Asserted on the fallback table itself rather than by setting variables,
    because the process running the tests may legitimately already have a real
    key for another provider in its environment -- and a check that depends on
    the developer's machine being empty is a check that fails for the wrong
    reason.
    """
    seen: dict[str, str] = {}
    for secret_name, variables in secret_store._ENV_FALLBACK.items():
        for variable in variables:
            assert variable not in seen, (
                f"{variable} is a fallback for both {seen.get(variable)} and {secret_name}")
            seen[variable] = secret_name
    assert "NANO_MISTRAL_API_KEY" in seen and seen["NANO_MISTRAL_API_KEY"] == "mistral_api_key"


def test_the_stored_key_wins_over_the_environment(monkeypatch):
    """Once configured through Nano, the encrypted store is authoritative."""
    monkeypatch.setenv("MISTRAL_API_KEY", "from-environment")
    monkeypatch.setattr(secret_store, "_read_store", lambda: {"mistral_api_key": "from-store"})
    assert mistral_provider.mistral_api_key() == "from-store"


def test_an_unconfigured_provider_is_setup_required_and_makes_no_request(monkeypatch):
    """Describing an unconfigured provider must not cost a network call."""
    monkeypatch.setattr(secret_store, "_read_store", lambda: {})
    monkeypatch.delenv("NANO_MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    def _explode(*_a, **_k):
        raise AssertionError("an unconfigured provider was contacted")

    monkeypatch.setattr(mistral_provider.httpx, "Client", _explode)

    payload = mistral_provider.describe_mistral("", "")
    assert payload["state"] == providers.ProviderState.SETUP_REQUIRED.value
    assert payload["secret"]["configured"] is False
    assert payload["models"] == []


def test_the_status_payload_never_carries_the_key(monkeypatch):
    monkeypatch.setattr(secret_store, "_read_store",
                        lambda: {"mistral_api_key": "abcd1234efgh5678ijkl"})
    monkeypatch.setattr(mistral_provider, "list_mistral_models",
                        lambda *a, **k: ([{"id": "ministral-x", "display_name": "x"}], None))

    payload = mistral_provider.describe_mistral("ministral-x", "ministral-x")
    blob = json.dumps(payload, ensure_ascii=False)
    assert "abcd1234efgh5678ijkl" not in blob
    assert set(payload["secret"]) == {"configured", "masked", "source", "encrypted"}
    assert payload["state"] == providers.ProviderState.READY.value


# --------------------------------------------------------------------------
#  Model discovery
# --------------------------------------------------------------------------


class _Response:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    @property
    def is_success(self):
        return self.status_code < 400

    def json(self):
        return self._payload


def _discovery(monkeypatch, payload, status=200) -> tuple[list[dict], str | None, list[dict]]:
    seen: list[dict] = []

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, url, headers=None, **_kw):
            seen.append({"url": url, "headers": dict(headers or {})})
            return _Response(payload, status)

    monkeypatch.setattr(mistral_provider.httpx, "Client", lambda **kw: _Client())
    records, error = mistral_provider.list_mistral_models("not-a-real-key")
    return records, error, seen


def test_discovery_reads_the_accounts_own_capability_flags(monkeypatch):
    """The account publishes per-model capabilities. Those are evidence; a
    guess from the model NAME is what this project got wrong twice on Gemma."""
    records, error, _seen = _discovery(monkeypatch, {"data": [
        {"id": "ministral-14b-2512", "name": "Ministral 14B",
         "max_context_length": 128000,
         "capabilities": {"completion_chat": True, "function_calling": True}},
        {"id": "some-chat-model-without-tools",
         "capabilities": {"completion_chat": True, "function_calling": False}},
        {"id": "mistral-embed",
         "capabilities": {"completion_chat": False, "function_calling": False}},
    ]})
    assert error is None
    by_id = {r["id"]: r for r in records}
    assert "mistral-embed" not in by_id, "a non-chat model was offered for chat"
    assert by_id["ministral-14b-2512"]["tool_calling"] is True
    assert by_id["ministral-14b-2512"]["input_tokens"] == 128000
    assert by_id["some-chat-model-without-tools"]["tool_calling"] is False


def test_a_model_the_account_says_nothing_about_stays_usable(monkeypatch):
    """An unstated capability is not a denied one. Reporting False would be
    Nano claiming a limitation it never measured."""
    records, error, _seen = _discovery(monkeypatch, {"data": [{"id": "ministral-8b-2512"}]})
    assert error is None
    assert records[0]["tool_calling"] is True
    assert records[0]["streaming"] is True


def test_discovery_sends_the_key_in_a_header_and_never_in_the_url(monkeypatch):
    _records, _error, seen = _discovery(monkeypatch, {"data": []})
    assert "key=" not in seen[0]["url"]
    assert seen[0]["headers"]["Authorization"] == "Bearer not-a-real-key"


def test_a_refused_key_is_reported_as_a_refused_key(monkeypatch):
    _records, error, _seen = _discovery(monkeypatch, {}, status=401)
    assert error == "invalid_api_key"
    verdict = mistral_provider.test_mistral("not-a-real-key")
    assert verdict["ok"] is False


def test_a_network_failure_never_leaks_the_request_url(monkeypatch):
    """httpx puts the full request URL into its exception messages."""

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, *_a, **_kw):
            raise httpx.ConnectError("failed connecting to https://api.mistral.ai/v1/models")

    monkeypatch.setattr(mistral_provider.httpx, "Client", lambda **kw: _Client())
    _records, error = mistral_provider.list_mistral_models("not-a-real-key")
    assert error == "network_error: ConnectError"
    assert "api.mistral.ai" not in error


def test_the_picker_prefers_the_small_fast_families_but_keeps_the_rest():
    ranked = sorted(["mistral-large-latest", "ministral-8b-2512", "unknown-model"],
                    key=mistral_provider.rank_model)
    assert ranked[0] == "ministral-8b-2512"
    assert "unknown-model" in ranked, "an unknown family was dropped rather than ranked last"


# --------------------------------------------------------------------------
#  Request shaping
# --------------------------------------------------------------------------


def test_tools_are_sent_in_the_openai_shape_with_an_explicit_choice():
    body = mistral_provider.build_request(
        "m", [{"role": "user", "content": "põe o volume a 30"}], TOOLS)
    assert body["tools"] == TOOLS, "the tool schema was rewritten"
    assert body["tool_choice"] == "auto"
    validate_body(body)


def test_no_tool_keys_are_sent_when_there_are_no_tools():
    """A null tool_choice is a 422 on EVERY message, not a warning on one."""
    body = mistral_provider.build_request("m", [{"role": "user", "content": "olá"}], [])
    assert "tools" not in body and "tool_choice" not in body
    validate_body(body)


def test_a_model_the_account_says_cannot_call_tools_is_not_offered_any():
    body = mistral_provider.build_request(
        "m", [{"role": "user", "content": "olá"}], TOOLS,
        metadata={"tool_calling": False})
    assert "tools" not in body


def test_the_system_message_survives_as_a_system_message():
    body = mistral_provider.build_request(
        "m", [{"role": "system", "content": "regras"},
              {"role": "user", "content": "olá"}], None)
    assert body["messages"][0] == {"role": "system", "content": "regras"}


def test_streaming_is_always_requested():
    body = mistral_provider.build_request("m", [{"role": "user", "content": "olá"}], None)
    assert body["stream"] is True


# --------------------------------------------------------------------------
#  Tool call ids: the thing that makes cross-provider failover legal
# --------------------------------------------------------------------------


def test_a_history_from_another_provider_is_made_legal():
    """The exact shape a turn that started on Groq or Gemini leaves behind.

    Those adapters produce ``call_0`` / ``call_<random>``; Mistral answers 422
    to anything but nine alphanumeric characters, and a 422 is BAD_REQUEST,
    which never falls back. So a turn that ran a tool and then failed over
    would not degrade -- it would end.
    """
    history = [
        {"role": "user", "content": "põe o volume a 30"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_0", "type": "function",
             "function": {"name": "pc_volume_set", "arguments": '{"level": 30}'}}]},
        {"role": "tool", "tool_call_id": "call_0", "content": '{"ok": true}'},
    ]
    body = mistral_provider.build_request("m", history, TOOLS)
    validate_body(body)                       # would raise on a foreign id

    assistant = next(m for m in body["messages"] if m["role"] == "assistant")
    tool = next(m for m in body["messages"] if m["role"] == "tool")
    assert tool["tool_call_id"] == assistant["tool_calls"][0]["id"], (
        "the result was detached from the call it answers")


def test_the_rewritten_id_is_stable_across_repeated_requests():
    """Failover re-sends the whole history. An id that changed between two
    requests would detach a result from its call, and a model that cannot see
    that an action already happened is a model that asks for it again."""
    history = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_abc", "type": "function",
             "function": {"name": "pc_volume_set", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_abc", "content": "{}"},
    ]
    first = mistral_provider.to_messages(history)
    second = mistral_provider.to_messages(history)
    assert first == second


def test_two_calls_in_one_turn_get_two_different_ids():
    history = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "", "type": "function",
         "function": {"name": "pc_volume_set", "arguments": '{"level": 10}'}},
        {"id": "", "type": "function",
         "function": {"name": "pc_volume_set", "arguments": '{"level": 20}'}},
    ]}]
    calls = mistral_provider.to_messages(history)[0]["tool_calls"]
    assert calls[0]["id"] != calls[1]["id"]


def test_a_tool_call_with_unparseable_arguments_is_dropped_not_emptied():
    """An empty argument map invites the model to re-issue the call with
    arguments of its own invention, which the per-turn execution ledger would
    not recognise as a repeat."""
    history = [{"role": "assistant", "content": "a fazer", "tool_calls": [
        {"id": "call_0", "type": "function",
         "function": {"name": "pc_volume_set", "arguments": "{level: 30"}}]}]
    shaped = mistral_provider.to_messages(history)
    assert shaped[0].get("tool_calls") is None
    assert shaped[0]["content"] == "a fazer"


def test_an_assistant_turn_with_nothing_left_is_omitted():
    history = [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_0", "type": "function",
         "function": {"name": "pc_volume_set", "arguments": "not json"}}]}]
    assert mistral_provider.to_messages(history) == []


# --------------------------------------------------------------------------
#  Streaming
# --------------------------------------------------------------------------


def test_text_arrives_as_it_streams_and_the_first_token_is_timed(monkeypatch):
    _t, text, collector = stream_with(
        monkeypatch, lines=sse([text_frame("Olá"), text_frame(", Simão.")]))
    assert text == "Olá, Simão."
    assert collector["first_token_at"] is not None


def test_the_key_travels_in_the_authorization_header_only(monkeypatch):
    transport, _text, _collector = stream_with(monkeypatch, lines=sse([text_frame("olá")]))
    assert "key=" not in transport.capture[0]["url"]
    assert transport.headers_seen[0]["Authorization"] == "Bearer not-a-real-key"


def test_a_fragmented_tool_call_is_reassembled(monkeypatch):
    """Arguments arrive a few characters at a time across deltas."""
    _t, text, collector = stream_with(
        monkeypatch, lines=sse(call_frames("pc_volume_set", {"level": 30})))
    assert text == ""
    assert collector["tool_calls"] == [
        {"id": "abcdefghi", "name": "pc_volume_set", "args": '{"level": 30}'}]
    assert json.loads(collector["tool_calls"][0]["args"]) == {"level": 30}


def test_usage_metadata_is_collected(monkeypatch):
    _t, _text, collector = stream_with(
        monkeypatch, lines=sse([text_frame("olá"), usage_frame(120, 7)]))
    assert collector["usage"] == {"prompt_tokens": 120, "completion_tokens": 7,
                                  "total_tokens": 127}
    assert collector["finish_reason"] == "stop"


def test_a_malformed_frame_is_skipped_rather_than_ending_the_turn(monkeypatch):
    """A single unparseable SSE frame must not throw away an answer that is
    otherwise arriving correctly."""
    lines = ["data: {not json", *sse([text_frame("olá")]), ": a comment"]
    _t, text, _collector = stream_with(monkeypatch, lines=lines)
    assert text == "olá"


def test_a_response_with_no_tool_calls_reports_an_empty_list(monkeypatch):
    _t, _text, collector = stream_with(monkeypatch, lines=sse([text_frame("olá")]))
    assert collector["tool_calls"] == []


def test_cancellation_propagates_and_closes_the_connection(monkeypatch):
    """The caller's CancelledError must reach the caller, not be swallowed."""
    transport = FakeMistralTransport(lines=sse([text_frame("a"), text_frame("b")]))
    monkeypatch.setattr(mistral_provider.httpx, "AsyncClient", lambda **kw: transport)

    async def _cancel_midway():
        chat = MistralChat("not-a-real-key")
        body = mistral_provider.build_request("m", [{"role": "user", "content": "oi"}], None)
        async for _piece in chat.stream("m", body, {}):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        run(_cancel_midway())


# --------------------------------------------------------------------------
#  Failures land in Nano's taxonomy
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status,expected", [
    (401, FailureType.AUTH_ERROR),
    (403, FailureType.AUTH_ERROR),
    (404, FailureType.MODEL_UNAVAILABLE),
    (422, FailureType.BAD_REQUEST),
    (429, FailureType.RATE_LIMIT),
    (500, FailureType.SERVER_ERROR),
    (503, FailureType.SERVER_ERROR),
])
def test_every_http_failure_classifies_the_way_the_other_providers_do(
        monkeypatch, status, expected):
    transport = FakeMistralTransport(
        status=status, error_body=json.dumps({"message": "nope"}).encode())
    monkeypatch.setattr(mistral_provider.httpx, "AsyncClient", lambda **kw: transport)

    with pytest.raises(MistralAPIError) as raised:
        run(drain(MistralChat("not-a-real-key"),
                  mistral_provider.build_request("m", [{"role": "user", "content": "oi"}], None),
                  {}))
    assert classify(raised.value, provider="mistral").type is expected


def test_a_timeout_is_a_timeout_and_not_an_unknown_error(monkeypatch):
    transport = FakeMistralTransport(raises=httpx.ReadTimeout("timed out"))
    monkeypatch.setattr(mistral_provider.httpx, "AsyncClient", lambda **kw: transport)
    with pytest.raises(MistralAPIError) as raised:
        run(drain(MistralChat("not-a-real-key"),
                  mistral_provider.build_request("m", [{"role": "user", "content": "oi"}], None),
                  {}))
    failure = classify(raised.value, provider="mistral")
    assert failure.type is FailureType.TIMEOUT
    assert failure.may_fall_back


def test_a_transport_failure_never_carries_the_request_url(monkeypatch):
    transport = FakeMistralTransport(
        raises=httpx.ConnectError("connecting to https://api.mistral.ai/v1/chat/completions"))
    monkeypatch.setattr(mistral_provider.httpx, "AsyncClient", lambda **kw: transport)
    with pytest.raises(MistralAPIError) as raised:
        run(drain(MistralChat("not-a-real-key"),
                  mistral_provider.build_request("m", [{"role": "user", "content": "oi"}], None),
                  {}))
    assert "api.mistral.ai" not in str(raised.value)


def test_a_429_carries_its_wait_into_the_shared_rate_limit_parser(monkeypatch):
    """Mistral names its throttling headers for what they meter, not for
    tokens. The wait has to survive the rename or the cooldown guesses."""
    transport = FakeMistralTransport(
        status=429, error_body=b'{"message": "rate limit"}',
        headers={"retry-after": "27", "ratelimitbysize-reset": "44",
                 "ratelimitbysize-remaining": "0"})
    monkeypatch.setattr(mistral_provider.httpx, "AsyncClient", lambda **kw: transport)

    with pytest.raises(MistralAPIError) as raised:
        run(drain(MistralChat("not-a-real-key"),
                  mistral_provider.build_request("m", [{"role": "user", "content": "oi"}], None),
                  {}))

    failure = classify(raised.value, provider="mistral")
    assert failure.type is FailureType.RATE_LIMIT
    assert failure.rate_limit["retry_after_seconds"] == 27.0
    assert failure.rate_limit["reset_tokens_seconds"] == 44.0
    # The LONGER signal wins: coming back at 27 s spends the budget that has
    # not returned yet, and the next failure arrives immediately.
    assert failure.cooldown_seconds() == 44.0


def test_a_429_without_headers_still_cools_down(monkeypatch):
    transport = FakeMistralTransport(status=429, error_body=b"{}", headers={})
    monkeypatch.setattr(mistral_provider.httpx, "AsyncClient", lambda **kw: transport)
    with pytest.raises(MistralAPIError) as raised:
        run(drain(MistralChat("not-a-real-key"),
                  mistral_provider.build_request("m", [{"role": "user", "content": "oi"}], None),
                  {}))
    failure = classify(raised.value, provider="mistral")
    assert failure.cooldown_seconds() > 0, "a 429 with no timing became a free retry"


def test_the_error_sentence_names_mistral_and_not_another_vendor():
    """Telling somebody their Groq key was refused when Mistral's was is a bug
    report waiting to happen."""
    failure = provider_failures.ProviderFailure(
        FailureType.AUTH_ERROR, "mistral", 401, "unauthorized")
    assert "Mistral" in failure.user_message()
    assert "Groq" not in failure.user_message()


def test_a_validation_error_body_is_reduced_to_one_readable_line(monkeypatch):
    """422 comes back as FastAPI's nested detail list, not as {"message": ...}."""
    body = json.dumps({"detail": [{"loc": ["body", "messages", 0],
                                   "msg": "field required", "type": "value_error"}]})
    transport = FakeMistralTransport(status=422, error_body=body.encode())
    monkeypatch.setattr(mistral_provider.httpx, "AsyncClient", lambda **kw: transport)
    with pytest.raises(MistralAPIError) as raised:
        run(drain(MistralChat("not-a-real-key"),
                  mistral_provider.build_request("m", [{"role": "user", "content": "oi"}], None),
                  {}))
    assert "field required" in str(raised.value)
    assert "\n" not in str(raised.value)


def test_an_unconfigured_client_fails_as_auth_without_a_request(monkeypatch):
    def _explode(**_kw):
        raise AssertionError("a request was made with no key")

    monkeypatch.setattr(mistral_provider.httpx, "AsyncClient", _explode)
    with pytest.raises(MistralAPIError) as raised:
        run(drain(MistralChat(""), {"messages": []}, {}))
    assert classify(raised.value, provider="mistral").type is FailureType.AUTH_ERROR


# --------------------------------------------------------------------------
#  Cooldowns are per provider
# --------------------------------------------------------------------------


def test_a_rate_limited_mistral_does_not_stop_nano_asking_the_others():
    """Routing to the next provider is precisely what a third provider is for."""
    provider_failures.reset_all_cooldowns()
    try:
        failure = provider_failures.ProviderFailure(
            FailureType.RATE_LIMIT, "mistral", 429, "limit",
            {"retry_after_seconds": 30.0})
        provider_failures.cooldown_for("mistral").note_failure(failure)

        assert provider_failures.cooldown_for("mistral").is_cooling_down()
        for other in ("groq", "google"):
            assert not provider_failures.cooldown_for(other).is_cooling_down(), (
                f"{other} was stopped by a Mistral rate limit")
    finally:
        provider_failures.reset_all_cooldowns()
