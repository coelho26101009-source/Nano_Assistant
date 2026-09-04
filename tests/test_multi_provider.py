"""Three cloud providers plus a local one, and the rules that bound the crossing.

Everything here drives the real ``Brain.chat`` generator and the real
``providers.resolve_route``. Provider status is supplied as the payloads the
describe_* functions really produce, so routing, the cloud chain, the cooldowns
and the failover are the production ones; only the transports are fakes.

The point of the suite is that WHO ANSWERS may change and nothing else may. Not
what the user asked. Not how many times an action ran on the machine. Not what
the user was asked to approve. Not what they are shown.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core import provider_failures, providers
from core.google_provider import GoogleAPIError
from core.mistral_provider import MistralAPIError
from core.provider_failures import FailureType

from tests.test_provider_fallback import (          # the fakes that already
    FakeGroqClient, FakeGroqError, FakeOllamaClient,  # behave like the real
    _Chunk, local_text,                             # providers
)


# --------------------------------------------------------------------------
#  Fakes
# --------------------------------------------------------------------------


class FakeGoogleClient:
    """Scripted Gemini transport, shaped like core.google_provider.GoogleChat.

    Each script entry is either an exception or a list of (text, tool_calls)
    steps, mirroring how the real adapter fills the collector.
    """

    def __init__(self, script: list):
        self.script = list(script)
        self.bodies: list[dict] = []

    async def stream(self, model, body, collector=None):
        self.bodies.append({"model": model, "body": body})
        sink = collector if collector is not None else {}
        if not self.script:
            raise AssertionError("Google called more times than the script allows")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        text, calls = step
        if text:
            sink.setdefault("first_token_at", None)
            import time as _time
            sink["first_token_at"] = _time.monotonic()
            yield text
        sink["tool_calls"] = [
            {"id": f"call_{i}", "name": name, "args": json.dumps(args, ensure_ascii=False)}
            for i, (name, args) in enumerate(calls or [])
        ]
        sink["usage"] = {"prompt_tokens": 100, "completion_tokens": 10}


def google_text(text: str):
    return (text, [])


def google_call(name: str, args: dict):
    return ("", [(name, args)])


class FakeMistralClient(FakeGoogleClient):
    """Scripted Mistral transport, shaped like core.mistral_provider.MistralChat.

    Deliberately the SAME collector contract as the Gemini fake, because that
    is the property under test: the Brain handles every cloud provider with one
    branchless body precisely because all three adapters fill one shape. A fake
    that filled a different one would hide a real adapter that did.

    It is stricter than a permissive stub in the one place Mistral is strict:
    the real API answers 422 to a tool call id that is not exactly nine
    alphanumeric characters, and a 422 is BAD_REQUEST, which never falls back.
    So a history that reaches it with a foreign id fails here too.
    """

    async def stream(self, model, body, collector=None):
        for message in body.get("messages") or []:
            for call in message.get("tool_calls") or []:
                call_id = str(call.get("id") or "")
                if len(call_id) != 9 or not call_id.isalnum():
                    raise AssertionError(
                        f"HTTP 422: tool call id {call_id!r} is not nine alphanumeric "
                        "characters; Mistral rejects the whole request")
            if message.get("role") == "tool":
                call_id = str(message.get("tool_call_id") or "")
                if len(call_id) != 9 or not call_id.isalnum():
                    raise AssertionError(
                        f"HTTP 422: tool_call_id {call_id!r} is not nine alphanumeric "
                        "characters")
        async for piece in super().stream(model, body, collector):
            yield piece


class RecordingExecutor:
    """Stands in for ToolExecutor; records every execution that reaches it."""

    def __init__(self, result=None):
        self.executions: list[tuple[str, dict]] = []
        self.registry = {"pc_volume_set": {}, "pc_app_launch": {}}
        self._result = result or {"success": True, "status": "completed",
                                  "output": {"ok": True, "level": 30}, "metadata": {}}

    async def execute_tool_async(self, name, args=None, **_kw):
        self.executions.append((name, dict(args or {})))
        return dict(self._result)


TOOLS = [{"type": "function", "function": {
    "name": "pc_volume_set", "description": "volume",
    "parameters": {"type": "object", "properties": {"level": {"type": "integer"}}}}}]


# --------------------------------------------------------------------------
#  Provider status payloads, in the shape describe_* really returns
# --------------------------------------------------------------------------


def cloud(provider_id: str, state, fast: str, complex_model: str | None = None) -> dict:
    return {
        "id": provider_id, "name": providers.PROVIDER_NAMES[provider_id],
        "kind": "cloud", "role": "cloud", "state": state.value,
        "model": fast, "models": [fast], "records": [],
        "secret": {"configured": True, "masked": "abc••••1234",
                   "source": "encrypted_store", "encrypted": True},
        "tiers": {"fast": fast, "complex": complex_model or fast},
        "detail": f"{provider_id} detail",
    }


def local(state=providers.ProviderState.READY, model: str = "qwen3:8b") -> dict:
    return {
        "id": "ollama", "name": "Ollama", "kind": "local", "role": "fallback",
        "state": state.value, "model": model, "models": [model],
        "secret": {"configured": True, "masked": "", "source": "none", "encrypted": False},
        "detail": "ollama detail", "url": "http://127.0.0.1:11434",
    }


READY = providers.ProviderState.READY
SETUP = providers.ProviderState.SETUP_REQUIRED


# --------------------------------------------------------------------------
#  Brain under test
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_cooldowns():
    provider_failures.reset_all_cooldowns()
    yield
    provider_failures.reset_all_cooldowns()


def build_brain(monkeypatch, *, mode="AUTO", preferred="google",
                google_script=None, groq_script=None, mistral_script=None,
                ollama_script=None,
                google_state=READY, groq_state=READY, mistral_state=SETUP,
                ollama_state=READY,
                tools=None, executor=None, task="ACTION"):
    from core import model_selection
    from core.brain import Brain
    from core.guardrails import GuardrailsEngine
    from core.memory import MemoryEngine

    brain = Brain(api_key="test-key", guardrails=GuardrailsEngine(),
                  memory=MemoryEngine(),
                  config={"provider_mode": mode, "preferred_cloud": preferred,
                          "google_fast_model": "gemini-test-fast",
                          "google_complex_model": "gemini-test-strong",
                          "mistral_fast_model": "mistral-test-fast",
                          "mistral_complex_model": "mistral-test-strong",
                          "local": {"enabled": True, "model": "qwen3:8b"}},
                  tool_executor=executor)
    brain.provider_mode = mode
    brain.preferred_cloud = preferred
    brain.tool_executor = executor

    brain.client = FakeGroqClient(groq_script or [])
    brain.google_client = FakeGoogleClient(google_script or [])
    brain.google_enabled = True
    brain.mistral_client = FakeMistralClient(mistral_script or [])
    # Mistral defaults to SETUP_REQUIRED so every pre-existing test still
    # describes exactly the two-provider world it was written for. A test that
    # wants the third provider asks for it.
    brain.mistral_enabled = mistral_state is not SETUP

    ollama = FakeOllamaClient(ollama_script or [local_text("resposta local")])
    monkeypatch.setattr("core.brain.httpx.AsyncClient", lambda **kw: ollama)
    brain._fake_ollama = ollama                       # type: ignore[attr-defined]

    async def _describe(_mode):
        return ({
            "google": cloud("google", google_state, "gemini-test-fast", "gemini-test-strong"),
            "groq": cloud("groq", groq_state, "openai/gpt-oss-20b", "openai/gpt-oss-120b"),
            "mistral": cloud("mistral", mistral_state, "mistral-test-fast",
                             "mistral-test-strong"),
        }, local(ollama_state))

    # The REAL resolve_route, _finish_route and _cloud_chain run on these.
    monkeypatch.setattr(brain, "_describe_providers_async", _describe)
    monkeypatch.setattr(model_selection, "select_tools",
                        lambda *a, **k: tools if tools is not None else [])
    monkeypatch.setattr(model_selection, "classify",
                        lambda *_a, **_k: model_selection.TaskClass(task))

    async def _prompt(*_a, **_k):
        return "system"

    monkeypatch.setattr(brain, "_build_system_prompt", _prompt)
    return brain


async def collect(brain, message: str) -> str:
    return "".join([chunk async for chunk in brain.chat(message, stream=True)])


def run(coro):
    return asyncio.run(coro)


def rate_limited(delay: str = "27s") -> GoogleAPIError:
    return GoogleAPIError(429, "RESOURCE_EXHAUSTED quota", {"retry-after": delay})


# --------------------------------------------------------------------------
#  Routing: mode and provider are different questions
# --------------------------------------------------------------------------


def test_auto_uses_the_preferred_cloud_provider(monkeypatch):
    brain = build_brain(monkeypatch, preferred="google",
                        google_script=[google_text("resposta gemini")])
    answer = run(collect(brain, "olá"))
    assert "resposta gemini" in answer
    assert brain.last_provider_used == "google"
    assert brain.client.calls == [], "Groq was contacted although Google answered"


def test_switching_the_preference_switches_the_provider_and_nothing_else(monkeypatch):
    brain = build_brain(monkeypatch, preferred="groq",
                        groq_script=[[_Chunk("resposta groq")]])
    answer = run(collect(brain, "olá"))
    assert "resposta groq" in answer
    assert brain.last_provider_used == "groq"
    assert brain.last_metadata["mode"] == "AUTO", "the mode changed with the provider"
    assert brain.google_client.bodies == [], "Google was contacted although Groq is preferred"


def test_local_mode_contacts_no_cloud_provider_at_all(monkeypatch):
    brain = build_brain(monkeypatch, mode="LOCAL", preferred="google",
                        ollama_script=[local_text("resposta local")])
    answer = run(collect(brain, "olá"))
    assert "resposta local" in answer
    assert brain.last_provider_used == "ollama"
    assert brain.google_client.bodies == [], "LOCAL mode contacted Google"
    assert brain.client.calls == [], "LOCAL mode contacted Groq"


def test_local_mode_does_not_even_describe_a_cloud_provider():
    """The privacy guarantee is about status probes too, not only chat."""
    from core import provider_status

    clouds, _ollama = provider_status.describe_all(
        providers.ProviderMode.LOCAL,
        cloud_tiers={"groq": ("g", "g2"), "google": ("x", "x2"),
                     "mistral": ("m", "m2")},
        ollama_model="qwen3:8b", ollama_base_url="http://127.0.0.1:11434",
        local_enabled=False)
    # EVERY cloud provider, not a named pair: the privacy guarantee has to hold
    # for the provider added tomorrow as well as the two that were here first.
    assert set(clouds) == set(providers.CLOUD_PROVIDER_IDS)
    for provider_id, payload in clouds.items():
        assert payload["state"] == providers.ProviderState.DISABLED.value, provider_id
        assert payload["secret"]["configured"] is False, provider_id


def test_cloud_mode_never_substitutes_a_different_vendor(monkeypatch):
    """"Use this provider" is an instruction. Quietly answering from the other
    one is the same class of surprise as falling back to local silently."""
    brain = build_brain(monkeypatch, mode="CLOUD", preferred="google",
                        google_script=[rate_limited()],
                        groq_script=[[_Chunk("resposta groq")]])
    answer = run(collect(brain, "olá"))
    assert brain.client.calls == [], "CLOUD mode fell over to the other vendor"
    assert brain._fake_ollama.requests == [], "CLOUD mode fell back to local"
    assert "Google" in answer and "Cloud" in answer


def test_in_cloud_mode_the_attempt_chain_holds_exactly_one_provider(monkeypatch):
    """The chain is the first of two independent guards on this rule; the
    second is the `mode != "CLOUD"` test in chat's failover decision. Because
    either alone is sufficient, the behavioural test above cannot tell them
    apart, so the chain is pinned directly here.
    """
    brain = build_brain(monkeypatch, mode="CLOUD", preferred="google")
    route = {
        "provider": "google", "model": "gemini-test-fast", "mode": "CLOUD",
        "alternatives": ["groq"],
        "cloud_models": {"google": "gemini-test-fast", "groq": "openai/gpt-oss-20b"},
        "cloud_states": {"google": READY.value, "groq": READY.value},
    }
    chain, _ = brain._cloud_chain(route, "CLOUD")
    assert chain == [("google", "gemini-test-fast")]

    # AUTO, by contrast, keeps the alternative so the turn can be finished.
    route["mode"] = "AUTO"
    chain, _ = brain._cloud_chain(route, "AUTO")
    assert chain == [("google", "gemini-test-fast"), ("groq", "openai/gpt-oss-20b")]


def test_cloud_mode_with_an_unconfigured_preferred_provider_says_so(monkeypatch):
    brain = build_brain(monkeypatch, mode="CLOUD", preferred="google",
                        google_state=SETUP)
    answer = run(collect(brain, "olá"))
    assert "Modo Cloud" in answer
    assert brain._fake_ollama.requests == []


# --------------------------------------------------------------------------
#  Cloud -> cloud failover inside one turn
# --------------------------------------------------------------------------


def test_a_rate_limited_gemini_is_finished_by_groq_in_the_same_turn(monkeypatch):
    brain = build_brain(monkeypatch, preferred="google",
                        google_script=[rate_limited()],
                        groq_script=[[_Chunk("resposta groq")]])
    answer = run(collect(brain, "olá")
                 )
    assert "resposta groq" in answer
    assert brain.last_provider_used == "groq"
    assert brain.last_metadata["fallback_used"] is True
    assert brain._fake_ollama.requests == [], "it skipped the second cloud provider"


def test_the_user_is_told_which_provider_took_over(monkeypatch):
    brain = build_brain(monkeypatch, preferred="google",
                        google_script=[rate_limited()],
                        groq_script=[[_Chunk("resposta groq")]])
    answer = run(collect(brain, "olá"))
    thinking = [line for line in answer.split("_thinking_:") if line]
    assert any("Groq" in line for line in thinking), "the handover was invisible"
    assert "429" not in answer and "retry" not in answer.lower()


def test_a_rate_limit_payload_never_reaches_the_answer(monkeypatch):
    brain = build_brain(monkeypatch, preferred="google",
                        google_script=[rate_limited()],
                        groq_script=[[_Chunk("ok")]])
    answer = run(collect(brain, "olá"))
    for forbidden in ("_ratelimit_", "retry_after_seconds", "RESOURCE_EXHAUSTED",
                      "GoogleAPIError", "Traceback", "x-goog-api-key"):
        assert forbidden not in answer, f"{forbidden!r} leaked into the answer"


def test_the_rate_limit_numbers_are_kept_in_structured_diagnostics(monkeypatch):
    brain = build_brain(monkeypatch, preferred="google",
                        google_script=[rate_limited("27s")],
                        groq_script=[[_Chunk("ok")]])
    run(collect(brain, "olá"))
    failure = brain.last_metadata["provider_failure"]
    assert failure["provider"] == "google"
    assert failure["failure_type"] == "RATE_LIMIT"
    assert failure["retry_after_seconds"] == 27.0
    assert brain.last_metadata["google_cooldown_seconds"] > 0


def test_an_auth_failure_is_never_hidden_behind_the_other_provider(monkeypatch):
    """A rejected key is a configuration problem only the user can fix.
    Answering from somewhere else would bury it, possibly for weeks."""
    brain = build_brain(monkeypatch, preferred="google",
                        google_script=[GoogleAPIError(401, "API key not valid")],
                        groq_script=[[_Chunk("resposta groq")]])
    answer = run(collect(brain, "olá"))
    assert brain.client.calls == [], "an auth failure fell over to Groq"
    assert brain._fake_ollama.requests == [], "an auth failure fell back to local"
    assert "Google" in answer


def test_a_bad_request_is_never_hidden_behind_the_other_provider(monkeypatch):
    """We sent something invalid; the other provider would receive the same
    invalid intent and turn our bug into a mystery."""
    brain = build_brain(monkeypatch, preferred="google",
                        google_script=[GoogleAPIError(400, "invalid argument")],
                        groq_script=[[_Chunk("resposta groq")]])
    run(collect(brain, "olá"))
    assert brain.client.calls == []
    assert brain._fake_ollama.requests == []


def test_a_half_streamed_answer_is_never_replaced_by_a_second_one(monkeypatch):
    """Two assistant voices in one bubble is worse than one honest partial."""
    class _PartialThenFail:
        def __init__(self):
            self.bodies: list[dict] = []

        async def stream(self, model, body, collector=None):
            self.bodies.append(body)
            sink = collector if collector is not None else {}
            import time as _time
            sink["first_token_at"] = _time.monotonic()
            yield "Começo da resp"
            raise rate_limited()

    brain = build_brain(monkeypatch, preferred="google",
                        groq_script=[[_Chunk("resposta groq completa")]])
    brain.google_client = _PartialThenFail()
    answer = run(collect(brain, "olá"))
    assert "Começo da resp" in answer
    assert "resposta groq completa" not in answer
    assert brain.client.calls == []
    assert brain.last_metadata["fallback_reason"] == "partial_stream_not_replaced"


def test_both_cloud_providers_failing_reaches_the_local_model(monkeypatch):
    brain = build_brain(monkeypatch, preferred="google",
                        google_script=[rate_limited()],
                        groq_script=[Exception("groq down")],
                        ollama_script=[local_text("resposta local")])
    answer = run(collect(brain, "olá"))
    assert "resposta local" in answer
    assert brain.last_provider_used == "ollama"


def test_a_cooling_down_provider_is_not_asked_again(monkeypatch):
    provider_failures.cooldown_for("google").note_failure(
        provider_failures.ProviderFailure(FailureType.RATE_LIMIT, "google", 429, "",
                                          {"retry_after_seconds": 40.0}))
    brain = build_brain(monkeypatch, preferred="google", google_script=[],
                        groq_script=[[_Chunk("resposta groq")]])
    answer = run(collect(brain, "olá"))
    assert "resposta groq" in answer
    assert brain.google_client.bodies == [], "a doomed Google request was still sent"
    assert brain.last_provider_used == "groq"


def test_every_cloud_provider_cooling_down_reports_the_real_reason(monkeypatch):
    """"No cloud available" is true and useless. The number is the fact the
    user needs."""
    for provider_id in ("google", "groq"):
        provider_failures.cooldown_for(provider_id).note_failure(
            provider_failures.ProviderFailure(FailureType.RATE_LIMIT, provider_id, 429, "",
                                              {"retry_after_seconds": 40.0}))
    brain = build_brain(monkeypatch, preferred="google", google_script=[], groq_script=[],
                        ollama_script=[local_text("resposta local")])
    run(collect(brain, "olá"))
    assert brain.last_metadata["fallback_reason"] == "google_cooldown"
    assert brain.last_metadata["google_cooldown_seconds"] > 0
    skipped = {entry["provider"]: entry["reason"] for entry in brain.last_metadata["cloud_skipped"]}
    assert skipped == {"google": "cooldown", "groq": "cooldown"}


# --------------------------------------------------------------------------
#  The execution ledger across a CLOUD -> CLOUD crossing
# --------------------------------------------------------------------------


def test_a_windows_action_runs_once_when_the_turn_crosses_providers(monkeypatch):
    """THE INVARIANT THIS WHOLE PHASE MUST NOT BREAK.

    Gemini asks for a tool, the tool really runs, the follow-up round fails,
    Groq finishes the turn and re-issues the identical call. The machine must
    be acted on exactly once.
    """
    executor = RecordingExecutor()
    brain = build_brain(
        monkeypatch, preferred="google", tools=TOOLS, executor=executor,
        google_script=[google_call("pc_volume_set", {"level": 30}), rate_limited()],
        groq_script=[
            [_Chunk(tool_calls=[("pc_volume_set", '{"level": 30}')])],
            [_Chunk("Volume definido.")],
        ])
    answer = run(collect(brain, "põe o volume a 30"))

    assert executor.executions == [("pc_volume_set", {"level": 30})], (
        f"the action ran {len(executor.executions)} times across the failover")
    assert "Volume definido." in answer


def test_the_replayed_result_is_marked_as_a_replay(monkeypatch):
    """The model must see a truthful answer; the machine is only touched once."""
    executor = RecordingExecutor()
    brain = build_brain(monkeypatch, preferred="google", tools=TOOLS, executor=executor,
                        google_script=[google_call("pc_volume_set", {"level": 30})],
                        groq_script=[])
    call = {"function": {"name": "pc_volume_set", "arguments": '{"level": 30}'}}
    first = run(brain._run_tool(call))
    second = run(brain._run_tool(call))
    assert len(executor.executions) == 1
    assert first.get("metadata", {}).get("replayed") is not True
    assert second["metadata"]["replayed"] is True


def test_a_differently_spelled_repeat_is_still_the_same_effect(monkeypatch):
    """The ledger keys on canonical arguments, so "Left" and "left" are one
    call. Two spellings would be two keys -- the hole the ledger closes."""
    executor = RecordingExecutor()
    executor.registry["pc_window_snap"] = {}
    brain = build_brain(monkeypatch, preferred="google", tools=TOOLS, executor=executor)
    run(brain._run_tool({"function": {"name": "pc_window_snap",
                                      "arguments": '{"position": "Left"}'}}))
    run(brain._run_tool({"function": {"name": "pc_window_snap",
                                      "arguments": '{"position": "left"}'}}))
    assert len(executor.executions) == 1


def test_the_ledger_is_cleared_between_user_turns(monkeypatch):
    """A genuine second request in a LATER turn must still act."""
    executor = RecordingExecutor()
    brain = build_brain(
        monkeypatch, preferred="google", tools=TOOLS, executor=executor,
        google_script=[google_call("pc_volume_set", {"level": 30}), google_text("feito"),
                       google_call("pc_volume_set", {"level": 30}), google_text("feito")])
    run(collect(brain, "põe o volume a 30"))
    run(collect(brain, "põe o volume a 30 outra vez"))
    assert len(executor.executions) == 2


# --------------------------------------------------------------------------
#  One composed context, whatever answers
# --------------------------------------------------------------------------


def test_both_providers_receive_the_same_composed_system_prompt(monkeypatch):
    """Memory, threads and the Second Brain must behave identically whichever
    provider serves the turn. A provider-specific memory path is exactly the
    divergence this asserts against."""
    executor = RecordingExecutor()
    brain = build_brain(monkeypatch, preferred="google", tools=TOOLS, executor=executor,
                        google_script=[rate_limited()],
                        groq_script=[[_Chunk("ok")]])

    composed = "CONTEXTO COMPOSTO: o utilizador chama-se Ana."

    async def _prompt(*_a, **_k):
        return composed

    monkeypatch.setattr(brain, "_build_system_prompt", _prompt)
    run(collect(brain, "como me chamo?"))

    google_system = brain.google_client.bodies[0]["body"]["systemInstruction"]["parts"][0]["text"]
    groq_system = brain.client.calls[0]["messages"][0]["content"]
    assert google_system == composed
    assert groq_system == composed


def test_the_same_tool_set_is_offered_to_both_providers(monkeypatch):
    executor = RecordingExecutor()
    brain = build_brain(monkeypatch, preferred="google", tools=TOOLS, executor=executor,
                        google_script=[rate_limited()],
                        groq_script=[[_Chunk("ok")]])
    run(collect(brain, "põe o volume a 30"))

    google_names = {d["name"] for d in
                    brain.google_client.bodies[0]["body"]["tools"][0]["functionDeclarations"]}
    groq_names = {t["function"]["name"] for t in brain.client.calls[0]["tools"]}
    assert google_names == groq_names == {"pc_volume_set"}


def test_the_tool_history_reaches_the_second_provider_intact(monkeypatch):
    """The second provider must continue from the recorded result, not be
    handed the original request again."""
    executor = RecordingExecutor()
    brain = build_brain(
        monkeypatch, preferred="google", tools=TOOLS, executor=executor,
        google_script=[google_call("pc_volume_set", {"level": 30}), rate_limited()],
        groq_script=[[_Chunk("Volume definido.")]])
    run(collect(brain, "põe o volume a 30"))

    roles = [m["role"] for m in brain.client.calls[0]["messages"]]
    assert "tool" in roles, "Groq did not receive the tool result Gemini produced"
    assert roles.count("user") == 1, "the user message was replayed"


# --------------------------------------------------------------------------
#  The third cloud provider, on the same terms as the other two
# --------------------------------------------------------------------------


def mistral_text(text: str):
    return (text, [])


def mistral_call(name: str, args: dict):
    return ("", [(name, args)])


def test_auto_can_prefer_mistral_and_nothing_else_changes(monkeypatch):
    brain = build_brain(monkeypatch, preferred="mistral", mistral_state=READY,
                        mistral_script=[mistral_text("resposta mistral")])
    answer = run(collect(brain, "olá"))
    assert "resposta mistral" in answer
    assert brain.last_provider_used == "mistral"
    assert brain.last_metadata["mode"] == "AUTO", "the mode changed with the provider"
    assert brain.client.calls == [], "Groq was contacted although Mistral answered"
    assert brain.google_client.bodies == [], "Google was contacted although Mistral answered"


def test_cloud_mode_on_mistral_never_substitutes_another_vendor(monkeypatch):
    """"Use this provider" is an instruction, whichever provider it names."""
    brain = build_brain(monkeypatch, mode="CLOUD", preferred="mistral",
                        mistral_state=READY,
                        mistral_script=[MistralAPIError(429, "rate limit",
                                                        {"retry-after": "30"})],
                        groq_script=[[_Chunk("resposta groq")]],
                        google_script=[google_text("resposta gemini")])
    answer = run(collect(brain, "olá"))
    assert brain.client.calls == [], "CLOUD mode fell over to Groq"
    assert brain.google_client.bodies == [], "CLOUD mode fell over to Google"
    assert brain._fake_ollama.requests == [], "CLOUD mode fell back to local"
    assert "Mistral" in answer and "Cloud" in answer


def test_local_mode_contacts_mistral_no_more_than_the_others(monkeypatch):
    """LOCAL's privacy guarantee must not weaken because a provider was added."""
    brain = build_brain(monkeypatch, mode="LOCAL", preferred="mistral",
                        mistral_state=READY,
                        ollama_script=[local_text("resposta local")])
    answer = run(collect(brain, "olá"))
    assert "resposta local" in answer
    assert brain.mistral_client.bodies == [], "LOCAL mode contacted Mistral"
    assert brain.google_client.bodies == [], "LOCAL mode contacted Google"
    assert brain.client.calls == [], "LOCAL mode contacted Groq"


def test_an_unconfigured_mistral_is_never_contacted(monkeypatch):
    """Preferring a provider that has no key must not send it a request.

    AUTO moves on to the next ready provider, which is the whole point of
    AUTO; what it must NOT do is spend a round trip discovering what the
    status snapshot already said.
    """
    brain = build_brain(monkeypatch, preferred="mistral", mistral_state=SETUP,
                        google_script=[google_text("resposta gemini")])
    answer = run(collect(brain, "olá"))
    assert "resposta gemini" in answer
    assert brain.mistral_client.bodies == [], "an unconfigured provider was contacted"


def test_a_provider_this_process_holds_no_client_for_is_skipped_out_loud(monkeypatch):
    """The snapshot said READY but this process has no Mistral client -- the
    window between saving a key and reloading credentials.

    Skipping silently would leave the diagnostics panel saying only "no cloud
    available", which is true and useless. Nano must never report a state
    vaguer than the one it measured.
    """
    brain = build_brain(monkeypatch, preferred="mistral", mistral_state=READY,
                        google_script=[google_text("resposta gemini")])
    brain.mistral_enabled = False
    brain.mistral_client = None

    answer = run(collect(brain, "olá"))
    assert "resposta gemini" in answer
    skipped = brain.last_metadata.get("cloud_skipped") or []
    assert [entry for entry in skipped
            if entry["provider"] == "mistral" and entry["reason"] == "not_configured"], skipped


def test_a_failing_mistral_is_finished_by_the_next_cloud_provider(monkeypatch):
    """AUTO crosses vendors inside one turn, in the declared order."""
    brain = build_brain(monkeypatch, preferred="mistral", mistral_state=READY,
                        mistral_script=[MistralAPIError(503, "high demand")],
                        google_script=[google_text("resposta gemini")])
    answer = run(collect(brain, "olá"))
    assert "resposta gemini" in answer
    assert brain.last_provider_used == "google"
    assert brain.last_metadata["fallback_used"] is True
    hops = [(a["provider"], a.get("outcome")) for a in brain.last_metadata["provider_attempts"]]
    assert hops[0] == ("mistral", FailureType.SERVER_ERROR.value)
    assert hops[-1] == ("google", "ok")


def test_a_windows_action_runs_once_when_a_turn_crosses_into_mistral(monkeypatch):
    """THE INVARIANT, on the new pair of hops.

    Gemini asks for a tool, the tool really runs, the follow-up round fails,
    Mistral finishes the turn and re-issues the identical call. The machine
    must be acted on exactly once.
    """
    executor = RecordingExecutor()
    brain = build_brain(
        monkeypatch, preferred="google", tools=TOOLS, executor=executor,
        mistral_state=READY,
        google_script=[google_call("pc_volume_set", {"level": 30}),
                       GoogleAPIError(503, "high demand")],
        mistral_script=[mistral_call("pc_volume_set", {"level": 30}),
                        mistral_text("Volume definido.")])
    answer = run(collect(brain, "põe o volume a 30"))

    assert executor.executions == [("pc_volume_set", {"level": 30})], (
        f"the action ran {len(executor.executions)} times across the failover")
    assert "Volume definido." in answer


def test_a_windows_action_runs_once_leaving_mistral_for_groq(monkeypatch):
    """The same invariant in the other direction, because the tool history
    Mistral produced is what Groq is then asked to continue from."""
    executor = RecordingExecutor()
    brain = build_brain(
        monkeypatch, preferred="mistral", tools=TOOLS, executor=executor,
        mistral_state=READY,
        mistral_script=[mistral_call("pc_volume_set", {"level": 30}),
                        MistralAPIError(503, "high demand")],
        groq_script=[[_Chunk(tool_calls=[("pc_volume_set", '{"level": 30}')])],
                     [_Chunk("Volume definido.")]])
    answer = run(collect(brain, "põe o volume a 30"))

    assert executor.executions == [("pc_volume_set", {"level": 30})]
    assert "Volume definido." in answer


def test_the_tool_history_reaches_mistral_in_a_shape_it_accepts(monkeypatch):
    """The ids in that history came from ANOTHER vendor.

    FakeMistralClient rejects a foreign id exactly as the real API does, so
    this fails if the adapter ever stops rewriting them -- which would turn
    every cross-vendor failover into a BAD_REQUEST that never recovers.
    """
    executor = RecordingExecutor()
    brain = build_brain(
        monkeypatch, preferred="google", tools=TOOLS, executor=executor,
        mistral_state=READY,
        google_script=[google_call("pc_volume_set", {"level": 30}),
                       GoogleAPIError(503, "high demand")],
        mistral_script=[mistral_text("Volume definido.")])
    run(collect(brain, "põe o volume a 30"))

    sent = brain.mistral_client.bodies[0]["body"]["messages"]
    roles = [m["role"] for m in sent]
    assert "tool" in roles, "the tool result never reached the second provider"
    assert brain.mistral_client.bodies, "Mistral was never asked"


def test_an_auth_failure_on_mistral_is_never_hidden_behind_another_provider(monkeypatch):
    """A rejected key must reach the user, or a configuration problem stays
    invisible for weeks behind a provider that happens to work."""
    brain = build_brain(monkeypatch, preferred="mistral", mistral_state=READY,
                        mistral_script=[MistralAPIError(401, "unauthorized")],
                        groq_script=[[_Chunk("resposta groq")]])
    answer = run(collect(brain, "olá"))
    assert brain.client.calls == [], "an auth failure fell over to another provider"
    assert "Mistral" in answer


def test_the_message_records_which_provider_and_model_answered(monkeypatch):
    """What the stored row may say about itself, for a Mistral turn."""
    from core import response_meta

    brain = build_brain(monkeypatch, preferred="mistral", mistral_state=READY,
                        mistral_script=[mistral_text("olá")])
    run(collect(brain, "olá"))
    shaped = response_meta.for_message(brain.last_metadata)
    assert shaped["provider"] == "mistral"
    assert shaped["model"] == "mistral-test-fast"
    assert shaped["fallback_used"] is False
    blob = json.dumps(shaped, ensure_ascii=False)
    assert "Bearer" not in blob and "api_key" not in blob


def test_a_rate_limited_mistral_says_which_provider_was_limited(monkeypatch):
    """The numbers travel with the provider, so the sentence the user reads
    names the account whose quota ran out and not whichever one is hardcoded."""
    brain = build_brain(monkeypatch, mode="CLOUD", preferred="mistral",
                        mistral_state=READY,
                        mistral_script=[MistralAPIError(429, "rate limit",
                                                        {"retry-after": "27"})])
    run(collect(brain, "olá"))
    limited = brain.last_metadata["rate_limited"]
    assert limited["provider"] == "mistral"
    assert "Mistral" in providers.rate_limit_message(limited)


# --------------------------------------------------------------------------
#  Persistence and the settings contract
# --------------------------------------------------------------------------


def test_every_cloud_providers_models_are_persistable_settings():
    from core import user_settings

    assert "preferred_cloud" in user_settings.ALLOWED_KEYS
    # Derived from CLOUD_PROVIDER_IDS rather than named, so a provider whose
    # model choice cannot be saved from the UI fails here on the day it is
    # added instead of the day a user tries to configure it.
    for provider_id in providers.CLOUD_PROVIDER_IDS:
        for key in (f"{provider_id}_fast_model", f"{provider_id}_complex_model"):
            assert key in user_settings.ALLOWED_KEYS, f"{key} cannot be saved from the UI"


def test_an_unknown_preference_falls_back_to_the_default_rather_than_routing_nowhere():
    # "mistral" was the unknown id here until Mistral shipped. The property is
    # about ids Nano does not route to, so the case moved to one that is really
    # unknown -- and the arrival of Mistral is pinned in the same place.
    assert providers.parse_preferred_cloud("sambanova") == providers.DEFAULT_CLOUD_PROVIDER
    assert providers.parse_preferred_cloud(None) == providers.DEFAULT_CLOUD_PROVIDER
    assert providers.parse_preferred_cloud("GOOGLE") == "google"
    assert providers.parse_preferred_cloud("mistral") == "mistral"


def test_groq_remains_the_default_until_a_benchmark_says_otherwise():
    assert providers.DEFAULT_CLOUD_PROVIDER == "groq"


def test_a_stored_preference_survives_the_overlay():
    from core import user_settings

    stored = {"preferred_cloud": "google", "google_fast_model": "gemini-x"}
    merged: dict = {}
    # apply_overlay reads the real store, so the mapping is exercised through
    # the same code path with an injected snapshot rather than a reimplementation.
    original = user_settings.all_settings
    try:
        user_settings.all_settings = lambda: dict(stored)   # type: ignore[assignment]
        merged = user_settings.apply_overlay({})
    finally:
        user_settings.all_settings = original               # type: ignore[assignment]
    assert merged["preferred_cloud"] == "google"
    assert merged["google_fast_model"] == "gemini-x"
    # A fast model with no complex one would leave the complex tier empty, and
    # an empty tier routes nowhere.
    assert merged["google_complex_model"] == "gemini-x"


def test_adding_the_next_provider_needs_no_change_to_the_router():
    """Mistral and SambaNova must arrive as one id plus one describe_*."""
    payloads = {
        "groq": cloud("groq", READY, "g"),
        "google": cloud("google", READY, "x"),
    }
    ordered = providers.cloud_candidates("google", payloads)
    assert [pid for pid, _ in ordered] == ["google", "groq"]
    ordered = providers.cloud_candidates("groq", payloads)
    assert [pid for pid, _ in ordered] == ["groq", "google"]
    # The id comes from the mapping key, never from the payload body.
    anonymous = providers.cloud_candidates("groq", {"groq": {"state": READY.value}})
    assert anonymous[0][0] == "groq"


# --------------------------------------------------------------------------
#  The provider / model UI contract
# --------------------------------------------------------------------------
#
# The pill and Definições → IA are ONE control with two surfaces. These pin the
# claim: one canonical stored answer, one set of backend endpoints, and a menu
# that can only ever offer models the account really reported.

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def _strip_comments(source: str) -> str:
    """Every source scan in this repository has at least once matched the
    comment explaining the bug instead of the bug."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)//.*$", "", source)


def _read(name: str) -> str:
    return _strip_comments((FRONTEND / name).read_text(encoding="utf-8"))


def test_the_pill_and_settings_write_through_the_same_endpoints():
    """One authority per stored choice.

    The provider-specific endpoints these named are gone: the pill and Settings
    now both call the provider-parameterised ones, which is what stopped the
    Groq model select writing the setting directly while Google's went through
    validation. The property is unchanged and now covers every provider at
    once -- there must be exactly ONE call site for each.
    """
    shell = _read("pages/index.tsx")
    for endpoint in ("set_provider_mode", "set_preferred_cloud_provider",
                     "set_cloud_model", "set_cloud_api_key",
                     "remove_cloud_api_key", "test_cloud_connection"):
        assert shell.count(f'"{endpoint}"') == 1, (
            f"{endpoint} is called from more than one place; there must be one authority")


def test_every_endpoint_the_ui_calls_really_exists():
    """Derived from the source, not from a list that has to be maintained.

    The previous version named five endpoints by hand, so an endpoint added to
    the UI tomorrow was unchecked and one removed from it made the test fail
    for the wrong reason. Reading every ``call("...")`` out of the shell means
    the check cannot go stale.
    """
    shell = _read("pages/index.tsx")
    backend = (Path(__file__).resolve().parent.parent / "core" / "main.py").read_text(encoding="utf-8")
    called = set(re.findall(r'call<[^>]*>\(\s*"([a-z0-9_]+)"', shell))
    assert len(called) > 20, f"only found {len(called)} endpoints; the pattern stopped matching"
    for endpoint in sorted(called):
        assert f"def {endpoint}(" in backend, f"the UI calls {endpoint}, which is not exposed"


def test_the_menu_holds_no_hardcoded_model_id():
    """Every entry must come from provider.models, which is what the ACCOUNT
    reported. A menu that can offer a model the account lacks is a 404 on every
    message."""
    menu = _read("components/AiModeMenu.tsx")
    for invented in ("gemini-", "gemma-", "gpt-oss", "qwen3"):
        assert invented not in menu.lower(), f"the menu hardcodes {invented!r}"


def test_the_settings_page_holds_no_hardcoded_model_id():
    page = _read("components/SettingsPage.tsx")
    for invented in ("gemini-", "gemma-", "gpt-oss", "qwen3:"):
        assert invented not in page.lower(), f"Settings hardcodes {invented!r}"


def test_the_ui_never_receives_a_key_back():
    """The renderer may SEND a key once through a narrow privileged operation
    and must never be able to read one back."""
    backend = (Path(__file__).resolve().parent.parent / "core" / "main.py").read_text(encoding="utf-8")
    for setter in ("set_cloud_api_key", "test_cloud_connection", "remove_cloud_api_key",
                   "set_google_api_key", "test_google_connection", "remove_google_api_key",
                   "set_mistral_api_key", "test_mistral_connection", "remove_mistral_api_key",
                   "set_groq_api_key", "test_groq_connection", "remove_groq_api_key"):
        body = re.search(rf"def {setter}\(.*?\n\n\n", backend, re.S)
        assert body, f"{setter} not found"
        source = body.group(0)
        assert "get_secret" not in source, f"{setter} reads a stored secret back"
        assert "api_key\"" not in source.replace('api_key: str', ''), (
            f"{setter} may return a key field")


def test_the_status_payload_the_ui_polls_carries_no_secret(monkeypatch):
    """describe_providers feeds both surfaces once a second."""
    import core.main as main_module

    payload = main_module.describe_providers(stale_ok=True)
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    for provider_id in providers.CLOUD_PROVIDER_IDS:
        secret = payload[provider_id]["secret"]
        assert set(secret) == {"configured", "masked", "source", "encrypted"}
    assert "x-goog-api-key" not in blob
    assert "Authorization" not in blob
    assert "Bearer" not in blob


def test_the_payload_names_one_preferred_provider_and_the_list_it_came_from():
    import core.main as main_module

    payload = main_module.describe_providers(stale_ok=True)
    assert payload["preferredCloud"] in payload["cloudProviders"]
    assert payload["cloudProviders"] == list(providers.CLOUD_PROVIDER_IDS)
    assert set(payload["cooldowns"]) == set(providers.CLOUD_PROVIDER_IDS)


# --------------------------------------------------------------------------
#  Per-message metadata: what THIS turn did, not what is configured now
# --------------------------------------------------------------------------


def test_the_observed_gemini_to_groq_fallback_is_recorded_hop_by_hop(monkeypatch):
    """The exact turn human testing reported, replayed.

    Gemini was preferred and selected. Google answered HTTP 503 "this model is
    currently experiencing high demand". That is SERVER_ERROR, it is
    fallback-eligible, and AUTO finished the same turn on Groq -- correct
    behaviour that LOOKED like a bug because the message said only
    "Provider: groq (fallback)".

    So the metadata has to carry the whole story: who was asked first, with
    which model, why they did not answer, and who did.
    """
    from core import response_meta

    brain = build_brain(
        monkeypatch, preferred="google",
        google_script=[GoogleAPIError(503, "UNAVAILABLE This model is currently "
                                           "experiencing high demand.")],
        groq_script=[[_Chunk("resposta groq")]])
    answer = run(collect(brain, "olá"))

    assert "resposta groq" in answer
    shaped = response_meta.for_message(brain.last_metadata)
    assert shaped["provider"] == "groq"
    assert shaped["fallback_used"] is True
    assert shaped["fallback_from"] == "google"
    assert shaped["fallback_reason"] == "provider_error"
    assert [(step["provider"], step["outcome"]) for step in shaped["provider_attempts"]] == [
        ("google", "provider_error"), ("groq", "ok")]
    # The vendor's own sentence stays in the log.
    assert "high demand" not in json.dumps(shaped, ensure_ascii=False)


def test_a_turn_that_never_fell_back_records_one_successful_hop(monkeypatch):
    from core import response_meta

    brain = build_brain(monkeypatch, preferred="google",
                        google_script=[google_text("resposta gemini")])
    run(collect(brain, "olá"))
    shaped = response_meta.for_message(brain.last_metadata)
    assert shaped["provider"] == "google"
    assert shaped["fallback_used"] is False
    assert shaped["provider_attempts"] == [
        {"provider": "google", "model": "gemini-test-fast", "outcome": "ok"}]


def test_a_turn_that_ends_on_the_local_model_says_so_in_the_chain(monkeypatch):
    """`provider`/`model` name Ollama and the chain still shows both cloud
    providers were asked. Reporting only the survivor would hide the two round
    trips that made the answer slow."""
    from core import response_meta

    brain = build_brain(monkeypatch, preferred="google",
                        google_script=[GoogleAPIError(503, "unavailable")],
                        groq_script=[FakeGroqError(500, message="server error")],
                        ollama_script=[local_text("resposta local")])
    answer = run(collect(brain, "olá"))
    assert "resposta local" in answer
    shaped = response_meta.for_message(brain.last_metadata)
    assert shaped["provider"] == "ollama"
    assert [step["provider"] for step in shaped["provider_attempts"]] == [
        "google", "groq", "ollama"]
    assert shaped["provider_attempts"][-1]["outcome"] == "ok"


def test_the_local_hop_is_not_reported_ok_before_the_local_model_answers(monkeypatch):
    """Honest status reporting, at the level of one field.

    Recording "ok" at the moment Ollama is ASKED would claim an outcome that
    has not happened. When the local model is unreachable too, the chain has to
    say the hop failed.
    """
    from core import response_meta

    class DeadOllama(FakeOllamaClient):
        async def post(self, url, json=None, **_kwargs):
            raise ConnectionError("connection refused")

        def stream(self, *_a, **_kw):
            raise ConnectionError("connection refused")

    dead = DeadOllama([])
    brain = build_brain(monkeypatch, preferred="google",
                        google_script=[GoogleAPIError(503, "unavailable")],
                        groq_script=[FakeGroqError(500, message="server error")])
    monkeypatch.setattr("core.brain.httpx.AsyncClient", lambda **kw: dead)
    run(collect(brain, "olá"))

    shaped = response_meta.for_message(brain.last_metadata)
    last = shaped["provider_attempts"][-1]
    assert last["provider"] == "ollama"
    assert last["outcome"] != "ok"


def test_the_metadata_the_ui_polls_is_shaped_by_the_same_allow_list():
    """get_last_response_meta used to return brain.last_metadata verbatim,
    provider error string included."""
    import core.main as main_module

    main_module.brain.last_metadata = {
        "provider": "google", "model": "gemini-2.5-flash",
        "provider_failure": {"message": "UNAVAILABLE high demand"},
    }
    payload = main_module.get_last_response_meta()
    assert "provider_failure" not in payload
    assert "high demand" not in json.dumps(payload, ensure_ascii=False)
    assert payload["provider"] == "google"
