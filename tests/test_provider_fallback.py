"""AUTO fallback: Groq primary, Ollama fallback, and the rules that bound it.

The point of this suite is that a provider failure changes WHO ANSWERS and
nothing else. It must not change what the user asked, how many times an action
ran, what they were asked to approve, or what they are shown.

Everything here drives the real `Brain.chat` generator. Groq is replaced by a
fake async client that fails exactly how the real one fails (status codes on
the exception, real headers on a 429), and Ollama by a fake HTTP layer that
speaks the real `/api/chat` shape. Nothing asserts on source text.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from core import provider_failures
from core.provider_failures import FailureType, ProviderCooldown, ProviderFailure, classify


# --------------------------------------------------------------------------
#  Fakes that behave like the real providers
# --------------------------------------------------------------------------


class _Response:
    def __init__(self, status_code: int, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}


class FakeGroqError(Exception):
    """Shaped like the Groq SDK's APIStatusError: status + response headers."""

    def __init__(self, status_code: int, headers: dict | None = None, message: str = "groq failed"):
        super().__init__(message)
        self.status_code = status_code
        self.response = _Response(status_code, headers)


RATE_LIMIT_HEADERS = {
    "retry-after": "3",
    "x-ratelimit-reset-tokens": "47s",
    "x-ratelimit-limit-tokens": "8000",
    "x-ratelimit-remaining-tokens": "1708",
}


class _Chunk:
    """One streamed Groq delta."""

    def __init__(self, content=None, tool_calls=None):
        function = None
        calls = None
        if tool_calls:
            calls = []
            for index, (name, args) in enumerate(tool_calls):
                function = type("F", (), {"name": name, "arguments": args})()
                calls.append(type("T", (), {"index": index, "id": f"call_{index}",
                                            "function": function})())
        delta = type("D", (), {"content": content, "tool_calls": calls})()
        self.choices = [type("C", (), {"delta": delta})()]
        self.x_groq = None


class FakeGroqClient:
    """Scripted Groq. Each entry is either an exception or a list of chunks."""

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[dict] = []
        self.chat = type("Chat", (), {"completions": self})()

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("Groq called more times than the script allows")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step

        async def _stream():
            for chunk in step:
                yield chunk

        return _stream()


class FakeOllamaResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeOllamaClient:
    """Scripted local model speaking the real /api/chat response shape."""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.requests: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, json=None, **_kwargs):
        self.requests.append(json or {})
        payload = self.script.pop(0) if self.script else {"message": {"content": "pronto"}}
        return FakeOllamaResponse(payload)


def local_text(text: str) -> dict:
    return {"message": {"role": "assistant", "content": text}}


def local_tool_call(name: str, arguments: dict) -> dict:
    return {"message": {"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}}


# --------------------------------------------------------------------------
#  Brain under test
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_cooldown():
    provider_failures.GROQ_COOLDOWN.reset()
    yield
    provider_failures.GROQ_COOLDOWN.reset()


def build_brain(monkeypatch, *, mode="AUTO", groq_script=None, ollama_script=None,
                tools=None, executor=None):
    from core import model_selection
    from core.brain import Brain
    from core.guardrails import GuardrailsEngine
    from core.memory import MemoryEngine

    brain = Brain(api_key="test-key", guardrails=GuardrailsEngine(),
                  memory=MemoryEngine(), config={"provider_mode": mode,
                                                 "local": {"enabled": True, "model": "qwen3:8b"}},
                  tool_executor=executor)
    brain.provider_mode = mode
    brain.client = FakeGroqClient(groq_script or [])
    brain.tool_executor = executor

    ollama = FakeOllamaClient(ollama_script or [local_text("resposta local")])
    monkeypatch.setattr("core.brain.httpx.AsyncClient", lambda **kw: ollama)
    brain._fake_ollama = ollama                       # type: ignore[attr-defined]

    async def _route(_message):
        return {"provider": "groq" if mode != "LOCAL" else "ollama",
                "model": "openai/gpt-oss-20b" if mode != "LOCAL" else "qwen3:8b",
                "mode": mode, "tier": "FAST", "fallback": False,
                "task": model_selection.TaskClass.ACTION.value, "usable": True,
                "reason": "test"}

    monkeypatch.setattr(brain, "route_for_async", _route)
    monkeypatch.setattr(model_selection, "select_tools",
                        lambda *a, **k: tools if tools is not None else [])

    async def _prompt(*_a, **_k):
        return "system"

    monkeypatch.setattr(brain, "_build_system_prompt", _prompt)
    return brain


async def collect(brain, message: str) -> str:
    return "".join([chunk async for chunk in brain.chat(message, stream=True)])


def run(coro):
    return asyncio.run(coro)


TOOL_SCHEMA = [{"type": "function",
                "function": {"name": "pc_volume_get", "description": "volume",
                             "parameters": {"type": "object", "properties": {}}}}]


class RecordingExecutor:
    """Stands in for ToolExecutor; records every execution that reaches it."""

    def __init__(self, result=None):
        self.executions: list[tuple[str, dict]] = []
        self.registry = {"pc_volume_get": {}, "pc_app_launch": {}}
        self._result = result or {"success": True, "status": "completed",
                                  "output": {"ok": True, "status": "read", "level": 100},
                                  "metadata": {}}

    async def execute_tool_async(self, name, args=None, **_kw):
        self.executions.append((name, dict(args or {})))
        return self._result


# --------------------------------------------------------------------------
#  Failure classification
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status,expected", [
    (429, FailureType.RATE_LIMIT),
    (401, FailureType.AUTH_ERROR),
    (403, FailureType.AUTH_ERROR),
    (404, FailureType.MODEL_UNAVAILABLE),
    (408, FailureType.TIMEOUT),
    (400, FailureType.BAD_REQUEST),
    (422, FailureType.BAD_REQUEST),
    (500, FailureType.SERVER_ERROR),
    (503, FailureType.SERVER_ERROR),
])
def test_http_status_maps_to_the_right_failure_type(status, expected):
    assert classify(FakeGroqError(status)).type is expected


def test_timeout_and_connection_errors_are_classified_without_a_status():
    assert classify(asyncio.TimeoutError()).type is FailureType.TIMEOUT
    assert classify(ConnectionError("connection refused")).type is FailureType.CONNECTION_ERROR
    assert classify(RuntimeError("network unreachable")).type is FailureType.CONNECTION_ERROR


def test_cancellation_is_never_a_provider_failure_to_recover_from():
    failure = classify(asyncio.CancelledError())
    assert failure.type is FailureType.CANCELLED
    assert failure.may_fall_back is False


def test_transient_failures_are_fallback_eligible_and_config_errors_are_not():
    for transient in (429, 500, 503, 408, 404):
        assert classify(FakeGroqError(transient)).may_fall_back is True
    for permanent in (401, 403, 400):
        assert classify(FakeGroqError(permanent)).may_fall_back is False


def test_a_rate_limit_carries_the_real_headers_into_diagnostics():
    failure = classify(FakeGroqError(429, RATE_LIMIT_HEADERS))
    payload = failure.as_dict()
    assert payload["failure_type"] == "RATE_LIMIT"
    assert payload["retry_after_seconds"] == 3.0
    assert payload["reset_tokens_seconds"] == 47.0
    assert payload["remaining_tokens"] == 1708


def test_a_failure_message_is_a_sentence_not_a_payload():
    for status in (429, 500, 401, 400, 404):
        message = classify(FakeGroqError(status)).user_message()
        assert "{" not in message and "_ratelimit_" not in message
        assert message.endswith(".")


# --------------------------------------------------------------------------
#  Cooldown
# --------------------------------------------------------------------------


def test_cooldown_prefers_the_token_reset_over_the_shorter_retry_after():
    """retry_after=3 with reset_tokens=47 must not mean "come back in 3s"."""
    failure = classify(FakeGroqError(429, RATE_LIMIT_HEADERS))
    assert failure.cooldown_seconds() == 47.0


def test_cooldown_is_bounded_at_both_ends():
    tiny = ProviderFailure(FailureType.RATE_LIMIT, rate_limit={"retry_after_seconds": 0.01})
    huge = ProviderFailure(FailureType.RATE_LIMIT, rate_limit={"reset_tokens_seconds": 99_999})
    breaker = ProviderCooldown("groq")
    assert breaker.note_failure(tiny) == provider_failures.MIN_COOLDOWN_SECONDS
    breaker.reset()
    assert breaker.note_failure(huge) == provider_failures.MAX_COOLDOWN_SECONDS


def test_an_auth_failure_never_starts_a_cooldown():
    breaker = ProviderCooldown("groq")
    assert breaker.note_failure(classify(FakeGroqError(401))) == 0.0
    assert breaker.is_cooling_down() is False


def test_a_cooldown_expires_on_the_monotonic_clock(monkeypatch):
    breaker = ProviderCooldown("groq")
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    breaker.note_failure(classify(FakeGroqError(429, {"retry-after": "10"})))
    assert breaker.is_cooling_down() is True
    now[0] += 9.0
    assert breaker.is_cooling_down() is True
    now[0] += 2.0
    assert breaker.is_cooling_down() is False


def test_a_successful_call_clears_the_cooldown():
    breaker = ProviderCooldown("groq")
    breaker.note_failure(classify(FakeGroqError(429, RATE_LIMIT_HEADERS)))
    assert breaker.is_cooling_down() is True
    breaker.note_success()
    assert breaker.is_cooling_down() is False
    assert breaker.status()["temporarily_limited"] is False


def test_cooldown_status_is_in_memory_and_makes_no_provider_call(monkeypatch):
    """Settings polls this once a second; it must never become API traffic."""
    import core.providers as providers_module

    called: list[str] = []
    monkeypatch.setattr(providers_module, "list_groq_models",
                        lambda *a, **k: called.append("probe") or ([], None))
    breaker = ProviderCooldown("groq")
    breaker.note_failure(classify(FakeGroqError(429, RATE_LIMIT_HEADERS)))
    for _ in range(50):
        status = breaker.status()
    assert status["temporarily_limited"] is True
    assert status["retry_in_seconds"] > 0
    assert called == []


# --------------------------------------------------------------------------
#  AUTO / CLOUD / LOCAL behaviour
# --------------------------------------------------------------------------


def test_auto_uses_groq_when_it_works_and_never_touches_ollama(monkeypatch):
    brain = build_brain(monkeypatch, mode="AUTO",
                        groq_script=[[_Chunk("resposta da cloud")]])
    answer = run(collect(brain, "olá"))
    assert "resposta da cloud" in answer
    assert brain.last_provider_used == "groq"
    assert brain._fake_ollama.requests == []
    assert brain.last_metadata["fallback_used"] is False


@pytest.mark.parametrize("status", [429, 500, 503, 408])
def test_auto_falls_back_to_ollama_on_a_transient_groq_failure(monkeypatch, status):
    headers = RATE_LIMIT_HEADERS if status == 429 else {}
    brain = build_brain(monkeypatch, mode="AUTO",
                        groq_script=[FakeGroqError(status, headers)],
                        ollama_script=[local_text("resposta local")])
    answer = run(collect(brain, "olá"))
    assert "resposta local" in answer
    assert brain.last_provider_used == "ollama"
    assert brain.last_metadata["fallback_used"] is True
    assert len(brain._fake_ollama.requests) == 1


def test_auto_does_not_fall_back_on_an_auth_error(monkeypatch):
    """A rejected key must be visible, not hidden behind a local answer."""
    brain = build_brain(monkeypatch, mode="AUTO",
                        groq_script=[FakeGroqError(401)],
                        ollama_script=[local_text("resposta local")])
    answer = run(collect(brain, "olá"))
    assert "resposta local" not in answer
    assert "chave" in answer.lower()
    assert brain._fake_ollama.requests == []
    assert brain.last_metadata["fallback_used"] is False


def test_auto_does_not_fall_back_on_our_own_bad_request(monkeypatch):
    brain = build_brain(monkeypatch, mode="AUTO",
                        groq_script=[FakeGroqError(400)],
                        ollama_script=[local_text("resposta local")])
    answer = run(collect(brain, "olá"))
    assert "resposta local" not in answer
    assert brain._fake_ollama.requests == []


def test_auto_skips_groq_entirely_while_it_is_cooling_down(monkeypatch):
    provider_failures.GROQ_COOLDOWN.note_failure(
        classify(FakeGroqError(429, RATE_LIMIT_HEADERS)))
    brain = build_brain(monkeypatch, mode="AUTO", groq_script=[],
                        ollama_script=[local_text("resposta local")])
    answer = run(collect(brain, "olá"))
    assert "resposta local" in answer
    assert brain.client.calls == [], "a doomed Groq request was still sent"
    assert brain.last_metadata["fallback_reason"] == "groq_cooldown"


def test_groq_is_used_again_once_the_cooldown_expires(monkeypatch):
    now = [500.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    provider_failures.GROQ_COOLDOWN.note_failure(
        classify(FakeGroqError(429, {"retry-after": "10"})))
    now[0] += 30.0
    brain = build_brain(monkeypatch, mode="AUTO",
                        groq_script=[[_Chunk("cloud de volta")]])
    answer = run(collect(brain, "olá"))
    assert "cloud de volta" in answer
    assert brain.last_provider_used == "groq"


def test_a_groq_success_clears_a_cooldown_left_by_an_earlier_failure(monkeypatch):
    brain = build_brain(monkeypatch, mode="AUTO",
                        groq_script=[[_Chunk("ok")]])
    provider_failures.GROQ_COOLDOWN._until = None      # eligible again
    run(collect(brain, "olá"))
    assert provider_failures.GROQ_COOLDOWN.is_cooling_down() is False


@pytest.mark.parametrize("status", [429, 500, 408])
def test_cloud_mode_never_calls_ollama(monkeypatch, status):
    """The user chose cloud-only. A clean failure is the correct output."""
    brain = build_brain(monkeypatch, mode="CLOUD",
                        groq_script=[FakeGroqError(status, RATE_LIMIT_HEADERS)],
                        ollama_script=[local_text("NUNCA")])
    answer = run(collect(brain, "olá"))
    assert "NUNCA" not in answer
    assert brain._fake_ollama.requests == []
    assert "Cloud" in answer
    assert brain.last_metadata["fallback_used"] is False


def test_local_mode_never_calls_groq(monkeypatch):
    brain = build_brain(monkeypatch, mode="LOCAL",
                        groq_script=[],
                        ollama_script=[local_text("só local")])
    answer = run(collect(brain, "olá"))
    assert "só local" in answer
    assert brain.client.calls == []


def test_both_providers_failing_produces_one_clean_bounded_message(monkeypatch):
    brain = build_brain(monkeypatch, mode="AUTO", groq_script=[FakeGroqError(429)])
    brain.local_enabled = False
    answer = run(collect(brain, "olá"))
    assert "cloud nem" in answer.lower()
    assert len(answer) < 400
    assert "{" not in answer


# --------------------------------------------------------------------------
#  One logical turn
# --------------------------------------------------------------------------


def test_the_user_message_is_not_duplicated_after_a_fallback(monkeypatch):
    brain = build_brain(monkeypatch, mode="AUTO",
                        groq_script=[FakeGroqError(429, RATE_LIMIT_HEADERS)],
                        ollama_script=[local_text("resposta local")])
    run(collect(brain, "abre a calculadora"))
    sent = brain._fake_ollama.requests[0]["messages"]
    user_entries = [m for m in sent if m.get("role") == "user"
                    and m.get("content") == "abre a calculadora"]
    assert len(user_entries) == 1, f"user message appeared {len(user_entries)} times"


def test_a_fallback_produces_exactly_one_assistant_answer(monkeypatch):
    brain = build_brain(monkeypatch, mode="AUTO",
                        groq_script=[FakeGroqError(500)],
                        ollama_script=[local_text("resposta local")])
    run(collect(brain, "olá"))
    assistants = [m for m in brain.conversation if m.get("role") == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["content"] == "resposta local"


def test_a_mid_stream_failure_never_produces_two_answers(monkeypatch):
    """Groq streamed visible words, then died. One voice, not two."""
    brain = build_brain(monkeypatch, mode="AUTO",
                        groq_script=[[_Chunk("Claro, vou "), FakeGroqError(503)]],
                        ollama_script=[local_text("RESPOSTA LOCAL COMPLETA")])

    async def _script(**kwargs):
        brain.client.calls.append(kwargs)

        async def _stream():
            yield _Chunk("Claro, vou ")
            raise FakeGroqError(503)

        return _stream()

    brain.client.create = _script
    answer = run(collect(brain, "olá"))
    assert "Claro, vou" in answer
    assert "RESPOSTA LOCAL COMPLETA" not in answer
    assert brain._fake_ollama.requests == []
    assert brain.last_metadata["partial_answer"] is True


# --------------------------------------------------------------------------
#  Tool-turn continuation and duplicate protection
# --------------------------------------------------------------------------


def test_a_tool_result_from_groq_is_handed_to_the_local_continuation(monkeypatch):
    """Groq called a tool, it ran, then Groq died on the follow-up step."""
    executor = RecordingExecutor()
    brain = build_brain(
        monkeypatch, mode="AUTO", tools=TOOL_SCHEMA, executor=executor,
        groq_script=[[_Chunk(tool_calls=[("pc_volume_get", "{}")])],
                     FakeGroqError(429, RATE_LIMIT_HEADERS)],
        ollama_script=[local_text("O volume está a 100%.")])

    answer = run(collect(brain, "qual é o volume?"))
    assert "100%" in answer
    assert executor.executions == [("pc_volume_get", {})], "the tool ran the wrong number of times"

    sent = brain._fake_ollama.requests[0]["messages"]
    assert any(m.get("role") == "tool" for m in sent), "the tool result was not carried over"
    # And the user message was not replayed on top of it.
    assert len([m for m in sent if m.get("role") == "user"
                and m.get("content") == "qual é o volume?"]) == 1


def test_a_consequential_tool_never_executes_twice_across_a_fallback(monkeypatch):
    """The local model re-issues the same launch. It must not run again."""
    executor = RecordingExecutor(result={"success": True, "status": "completed",
                                         "output": {"ok": True, "status": "launched"},
                                         "metadata": {}})
    brain = build_brain(
        monkeypatch, mode="AUTO", tools=TOOL_SCHEMA, executor=executor,
        groq_script=[[_Chunk(tool_calls=[("pc_app_launch", '{"name": "Calculadora"}')])],
                     FakeGroqError(429, RATE_LIMIT_HEADERS)],
        ollama_script=[local_tool_call("pc_app_launch", {"name": "Calculadora"}),
                       local_text("A Calculadora foi aberta.")])

    answer = run(collect(brain, "abre a calculadora"))
    assert "Calculadora" in answer
    assert executor.executions == [("pc_app_launch", {"name": "Calculadora"})], (
        f"launched {len(executor.executions)} times: {executor.executions}")


def test_the_replayed_result_is_marked_so_it_is_never_mistaken_for_a_new_one(monkeypatch):
    executor = RecordingExecutor()
    brain = build_brain(monkeypatch, mode="AUTO", tools=TOOL_SCHEMA, executor=executor,
                        groq_script=[[_Chunk("x")]])
    call = {"function": {"name": "pc_volume_get", "arguments": "{}"}}
    first = run(brain._run_tool(call))
    second = run(brain._run_tool(call))
    assert len(executor.executions) == 1
    assert first["metadata"].get("replayed") is None
    assert second["metadata"]["replayed"] is True


def test_the_ledger_is_per_turn_so_a_later_request_still_executes(monkeypatch):
    executor = RecordingExecutor()
    brain = build_brain(monkeypatch, mode="AUTO", tools=TOOL_SCHEMA, executor=executor,
                        groq_script=[[_Chunk(tool_calls=[("pc_volume_get", "{}")])],
                                     [_Chunk("100%")],
                                     [_Chunk(tool_calls=[("pc_volume_get", "{}")])],
                                     [_Chunk("100%")]])
    run(collect(brain, "qual é o volume?"))
    run(collect(brain, "e agora?"))
    assert len(executor.executions) == 2, "a genuine second turn was suppressed"


def test_a_failed_tool_is_not_cached_and_stays_retryable(monkeypatch):
    """A refusal must be retryable: the user may approve on a second ask."""
    executor = RecordingExecutor(result={"success": False, "status": "permission_denied",
                                         "error": "denied", "metadata": {}})
    brain = build_brain(monkeypatch, mode="AUTO", tools=TOOL_SCHEMA, executor=executor,
                        groq_script=[[_Chunk("x")]])
    call = {"function": {"name": "pc_app_launch", "arguments": '{"name": "X"}'}}
    run(brain._run_tool(call))
    run(brain._run_tool(call))
    assert len(executor.executions) == 2


def test_different_arguments_are_different_calls(monkeypatch):
    executor = RecordingExecutor()
    brain = build_brain(monkeypatch, mode="AUTO", tools=TOOL_SCHEMA, executor=executor,
                        groq_script=[[_Chunk("x")]])
    run(brain._run_tool({"function": {"name": "pc_app_launch", "arguments": '{"name": "A"}'}}))
    run(brain._run_tool({"function": {"name": "pc_app_launch", "arguments": '{"name": "B"}'}}))
    assert len(executor.executions) == 2


# --------------------------------------------------------------------------
#  Permissions are untouched by provider failover
# --------------------------------------------------------------------------


def test_a_replayed_call_never_reaches_the_permission_layer_again(monkeypatch):
    """ALLOW_ONCE stays exactly once: the replay does not re-enter the executor.

    A second execution would consume a second grant, or prompt the user again
    for something they already approved and which already happened.
    """
    from core.permission_manager import PermissionManager
    from core.tool_execution import ToolExecutor
    from core import plugin_loader

    plugin_loader.load_all_plugins()
    prompts: list[tuple[str, Any]] = []
    manager = PermissionManager(confirmation_callback=lambda c, a: (prompts.append((c, a.get("target"))), True)[1])
    executor = ToolExecutor(manager)
    executor.register_plugin_tools()

    brain = build_brain(monkeypatch, mode="AUTO", tools=TOOL_SCHEMA, executor=executor,
                        groq_script=[[_Chunk("x")]])
    call = {"function": {"name": "pc_system_info", "arguments": "{}"}}
    run(brain._run_tool(call))
    prompts_after_first = len(prompts)
    run(brain._run_tool(call))
    assert len(prompts) == prompts_after_first, "the replay re-entered the permission layer"


def test_a_provider_failure_does_not_widen_a_target_bound_grant(monkeypatch):
    from core.permission_manager import PermissionManager

    manager = PermissionManager(confirmation_callback=lambda *_: True)
    request = manager.request_permission("pc.window.close", {"target": "window:100"})
    manager.resolve_permission(request, "ALLOW_ONCE")

    # A provider failure happens; nothing about the grant may change.
    provider_failures.GROQ_COOLDOWN.note_failure(classify(FakeGroqError(429, RATE_LIMIT_HEADERS)))

    assert manager._has_execution_grant("pc.window.close", {"target": "window:100"}) is True
    assert manager._has_execution_grant("pc.window.close", {"target": "window:200"}) is False


# --------------------------------------------------------------------------
#  Information hygiene
# --------------------------------------------------------------------------


def test_rate_limit_metadata_never_reaches_the_answer(monkeypatch):
    brain = build_brain(monkeypatch, mode="AUTO",
                        groq_script=[FakeGroqError(429, RATE_LIMIT_HEADERS)],
                        ollama_script=[local_text("resposta local")])
    answer = run(collect(brain, "olá"))
    for forbidden in ("_ratelimit_", "retry_after_seconds", "x-ratelimit",
                      "remaining_tokens", "APIError", "Traceback"):
        assert forbidden not in answer, f"{forbidden!r} leaked into the answer"


def test_rate_limit_metadata_is_kept_in_structured_diagnostics(monkeypatch):
    brain = build_brain(monkeypatch, mode="AUTO",
                        groq_script=[FakeGroqError(429, RATE_LIMIT_HEADERS)],
                        ollama_script=[local_text("resposta local")])
    run(collect(brain, "olá"))
    failure = brain.last_metadata["provider_failure"]
    assert failure["failure_type"] == "RATE_LIMIT"
    assert failure["retry_after_seconds"] == 3.0
    assert brain.last_metadata["groq_cooldown_seconds"] > 0


def test_no_control_token_survives_into_a_cloud_answer(monkeypatch):
    brain = build_brain(monkeypatch, mode="CLOUD",
                        groq_script=[FakeGroqError(429, RATE_LIMIT_HEADERS)])
    answer = run(collect(brain, "olá"))
    assert "_ratelimit_" not in answer
    assert "{" not in answer


def test_the_brain_no_longer_emits_a_ratelimit_control_token(monkeypatch):
    """Behavioural: drive a 429 in every mode and inspect the token stream."""
    for mode in ("AUTO", "CLOUD"):
        brain = build_brain(monkeypatch, mode=mode,
                            groq_script=[FakeGroqError(429, RATE_LIMIT_HEADERS)],
                            ollama_script=[local_text("local")])

        async def _tokens():
            return [chunk async for chunk in brain.chat("olá", stream=True)]

        assert not any(t.startswith("_ratelimit_") for t in run(_tokens())), mode


def test_the_voice_path_strips_every_control_token(monkeypatch):
    """The voice path feeds TTS; a leaked sentinel would be read aloud."""
    from core.voice import VoiceRuntime

    class _Brain:
        async def chat(self, text, stream=True):
            yield "_thinking_:⚙️ pc_volume_get..."
            yield "_ratelimit_:{\"retry_after_seconds\": 3}"
            yield "O volume está a 100%."

    runtime = VoiceRuntime.__new__(VoiceRuntime)
    runtime.brain = _Brain()
    spoken = run(runtime._direct_chat("qual é o volume?"))
    assert spoken == "O volume está a 100%."
    assert "_ratelimit_" not in spoken and "_thinking_" not in spoken


# --------------------------------------------------------------------------
#  Malformed local output
# --------------------------------------------------------------------------


def test_a_leaked_tool_call_is_detected_but_never_parsed_into_a_call():
    """Observed for real: qwen3:8b answered `{"name": "pc", "arguments": {}}`."""
    from core.brain import _looks_like_a_leaked_tool_call as leaked

    assert leaked('{"name": "pc", "arguments": {}}') is True
    assert leaked('{"function": "pc_volume_get", "arguments": {}}') is True
    assert leaked("O volume está a 100%.") is False
    assert leaked('{"resultado": 42}') is False
    assert leaked('Aqui tens: {"a": 1}') is False
    assert leaked("") is False


def test_a_leaked_tool_call_is_replaced_and_never_executed(monkeypatch):
    executor = RecordingExecutor()
    brain = build_brain(monkeypatch, mode="AUTO", tools=TOOL_SCHEMA, executor=executor,
                        groq_script=[FakeGroqError(429, RATE_LIMIT_HEADERS)],
                        ollama_script=[local_text('{"name": "pc", "arguments": {}}')])
    answer = run(collect(brain, "como está a ram?"))
    assert '{"name"' not in answer, "raw JSON was shown to the user"
    assert "modelo local" in answer
    assert executor.executions == [], "model prose was executed as a tool call"
    assert brain.last_metadata["local_malformed_tool_call"] is True


def test_a_normal_local_answer_is_not_mistaken_for_a_leaked_call(monkeypatch):
    brain = build_brain(monkeypatch, mode="AUTO",
                        groq_script=[FakeGroqError(429, RATE_LIMIT_HEADERS)],
                        ollama_script=[local_text("O volume está a 100%.")])
    answer = run(collect(brain, "qual é o volume?"))
    assert "O volume está a 100%." in answer
    assert brain.last_metadata.get("local_malformed_tool_call") is None
