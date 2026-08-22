"""Regression tests for the fluid-conversation / voice-reliability pass.

Each test here corresponds to a defect that was measured in the real runtime,
not to a hypothetical. The comment above each group names the failure it locks
out.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import model_selection as ms
from core import providers, speech_filter, frontend_build
from core.providers import ProviderMode, ProviderState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _groq(state=ProviderState.READY, fast="fast-model", strong="strong-model"):
    return {"id": "groq", "state": state.value, "model": fast, "models": [fast, strong],
            "tiers": {"fast": fast, "complex": strong}, "detail": "d"}


def _ollama(state=ProviderState.READY, model="qwen3:8b"):
    return {"id": "ollama", "state": state.value, "model": model, "models": [model],
            "detail": "d"}


def _tools(*names):
    return [{"type": "function", "function": {"name": n, "description": "x",
                                              "parameters": {"type": "object"}}}
            for n in names]


ALL_TOOLS = _tools(
    "system_stats", "system_run_powershell", "system_files", "system_volume",
    "web_search", "web_navigate_extract", "remember_fact", "list_facts",
    "calendar_add_event", "set_reminder", "iot_command", "organize_downloads",
)


# ---------------------------------------------------------------------------
# MODEL ROUTING
# Cloud chat must never reach Ollama, and Local must never reach the cloud.
# ---------------------------------------------------------------------------

def test_cloud_mode_never_routes_to_ollama():
    route = providers.resolve_route(ProviderMode.CLOUD, _groq(), _ollama())
    assert route["provider"] == "groq"
    assert route["fallback"] is False


def test_cloud_mode_stays_on_groq_even_when_groq_is_down():
    """A cloud-only user must be told, never silently downgraded."""
    route = providers.resolve_route(
        ProviderMode.CLOUD, _groq(state=ProviderState.UNAVAILABLE), _ollama())
    assert route["provider"] == "groq"
    assert route["usable"] is False
    assert route["fallback"] is False


def test_local_mode_never_reaches_the_cloud():
    route = providers.resolve_route(ProviderMode.LOCAL, _groq(), _ollama())
    assert route["provider"] == "ollama"


def test_auto_prefers_groq_and_falls_back_visibly():
    assert providers.resolve_route(ProviderMode.AUTO, _groq(), _ollama())["provider"] == "groq"
    fell_back = providers.resolve_route(
        ProviderMode.AUTO, _groq(state=ProviderState.UNAVAILABLE), _ollama())
    assert fell_back["provider"] == "ollama"
    assert fell_back["fallback"] is True, "a fallback must be visible, never silent"


def test_simple_chat_selects_the_fast_model():
    route = providers.resolve_route(ProviderMode.CLOUD, _groq(), _ollama(), tier="FAST")
    assert route["model"] == "fast-model"


def test_complex_requests_select_the_strong_model():
    route = providers.resolve_route(ProviderMode.CLOUD, _groq(), _ollama(), tier="STRONG")
    assert route["model"] == "strong-model"


def test_an_unknown_tier_never_escalates_to_the_expensive_model():
    route = providers.resolve_route(ProviderMode.CLOUD, _groq(), _ollama(), tier="nonsense")
    assert route["model"] == "fast-model"


@pytest.mark.parametrize("message", [
    "Olá", "Bom dia", "Conta-me uma piada", "Obrigado!",
    "Qual é a capital de Portugal?", "Explica o que é RAM",
])
def test_conversation_stays_on_the_fast_tier(message):
    """Message length and vocabulary must not promote small talk to the big model."""
    assert ms.tier_for(ms.classify(message)) is ms.ModelTier.FAST


@pytest.mark.parametrize("message", [
    "Analisa este código e corrige o bug",
    "Implementa uma função de bubble sort",
    "```python\nprint(1)\n```  porque falha?",
])
def test_real_work_is_promoted_to_the_strong_tier(message):
    assert ms.tier_for(ms.classify(message)) is ms.ModelTier.STRONG


def test_a_long_rambling_message_is_not_promoted_by_length_alone():
    """Length is not evidence of complexity; only explicit task words are."""
    long_chat = ("Estou a pensar em mudar de cidade este ano e ando com muitas "
                 "duvidas sobre onde viver, o que fazer com a casa, os amigos, "
                 "e tambem sobre o trabalho, sinceramente nao sei por onde comecar. ") * 3
    assert ms.tier_for(ms.classify(long_chat)) is ms.ModelTier.FAST


# ---------------------------------------------------------------------------
# TOOLS
# ~36 tool definitions (~1500 tokens) were attached to every single message.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "Olá", "Bom dia", "Conta-me uma piada", "Obrigado!",
    "Explica em duas frases o que é RAM.", "Qual é a capital de Portugal?",
])
def test_simple_conversation_sends_zero_tool_definitions(message):
    assert ms.select_tools(message, ALL_TOOLS) == []


def test_a_pc_request_sends_only_pc_tools():
    selected = {t["function"]["name"] for t in ms.select_tools("Abre o Spotify", ALL_TOOLS)}
    assert selected, "an action request must still get tools"
    assert "web_search" not in selected
    assert "calendar_add_event" not in selected


def test_a_search_request_sends_only_browser_tools():
    selected = {t["function"]["name"] for t in ms.select_tools("Pesquisa notícias sobre IA", ALL_TOOLS)}
    assert "web_search" in selected
    assert "system_run_powershell" not in selected


def test_no_request_ever_receives_the_entire_registry():
    """The whole point: never blindly send every registered tool."""
    for message in ["Abre o Spotify", "Cria um ficheiro", "Pesquisa isto",
                    "Marca uma reunião", "faz alguma coisa", "Analisa este código"]:
        selected = ms.select_tools(message, ALL_TOOLS)
        assert len(selected) < len(ALL_TOOLS), (
            f"{message!r} received the full registry ({len(selected)} tools)"
        )


def test_an_ambiguous_action_gets_a_bounded_subset_not_everything():
    selected = ms.select_tools("Analisa isto por favor", ALL_TOOLS)
    assert 0 < len(selected) <= 6


def test_tool_filtering_is_not_a_permission_boundary():
    """Filtering only reduces cost/noise; it must not be load-bearing security.

    This is a documentation guard: selection never consults the permission
    manager, and must never be mistaken for it.
    """
    source = (ROOT / "core" / "model_selection.py").read_text(encoding="utf-8")
    for forbidden in ("PermissionManager", "policy_engine", "ToolExecutor"):
        assert forbidden not in source, (
            f"{forbidden} appears in model_selection; tool filtering must stay "
            "independent of the permission path"
        )


# ---------------------------------------------------------------------------
# TOKEN BUDGET
# Groq charges the reserved max_tokens against TPM, not the tokens produced.
# ---------------------------------------------------------------------------

def test_small_talk_reserves_far_fewer_tokens_than_complex_work():
    assert ms.max_tokens_for(ms.TaskClass.SMALL_TALK) < ms.max_tokens_for(ms.TaskClass.COMPLEX)


def test_conversation_never_reserves_the_whole_minute_budget():
    """8000 TPM: reserving 4096 per message capped throughput at ~2 messages."""
    for task in (ms.TaskClass.SMALL_TALK, ms.TaskClass.QUESTION):
        assert ms.max_tokens_for(task) <= 1024


# ---------------------------------------------------------------------------
# RATE LIMIT
# The SDK slept 30-46 s through a 429 without telling anyone.
# ---------------------------------------------------------------------------

def test_the_groq_client_is_constructed_without_hidden_retries():
    source = (ROOT / "core" / "brain.py").read_text(encoding="utf-8")
    # Strip comments so the explanatory prose above the call cannot satisfy it.
    code = "\n".join(line.split("#")[0] for line in source.splitlines())
    assert "AsyncGroq(" in code
    for call in code.split("AsyncGroq(")[1:]:
        head = call[:120]
        assert "max_retries=0" in head, (
            "AsyncGroq is built without max_retries=0; the SDK default of 2 "
            "sleeps silently through a 429"
        )


def test_retry_after_in_plain_seconds_is_parsed():
    info = providers.parse_rate_limit({"retry-after": "12"})
    assert info["wait_seconds"] == 12.0


def test_groq_duration_headers_are_parsed():
    info = providers.parse_rate_limit({"x-ratelimit-reset-tokens": "1m31.2s"})
    assert info["wait_seconds"] == pytest.approx(91.2)


def test_remaining_budget_is_exposed_to_the_ui():
    info = providers.parse_rate_limit({
        "retry-after": "5",
        "x-ratelimit-limit-tokens": "8000",
        "x-ratelimit-remaining-tokens": "120",
    })
    assert info["limit_tokens"] == 8000
    assert info["remaining_tokens"] == 120


def test_a_rate_limit_message_names_the_wait_and_is_never_just_error():
    message = providers.rate_limit_message(providers.parse_rate_limit({"retry-after": "12"}))
    assert "12" in message
    assert message.lower() != "error"
    assert "Groq" in message


def test_a_rate_limit_without_headers_still_explains_itself():
    message = providers.rate_limit_message(providers.parse_rate_limit({}))
    assert message.strip()
    assert "erro" not in message.lower()


# ---------------------------------------------------------------------------
# tool_choice
# Sending tool_choice: null made Groq reject every message with 400.
# ---------------------------------------------------------------------------

def test_tool_choice_is_only_sent_alongside_tools():
    source = (ROOT / "core" / "brain.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in source.splitlines())
    assert 'tool_choice="auto" if tools else None' not in code, (
        "tool_choice=None serialises to JSON null, which Groq rejects with 400"
    )
    assert '"tool_choice"' in code
    # The assignment must sit inside a truthiness guard on tools.
    guarded = "if tools:" in code and 'kwargs["tool_choice"]' in code
    assert guarded, "tool_choice must only be set when tools are present"


# ---------------------------------------------------------------------------
# STREAMING
# The old path awaited the full completion, then faked chunks.
# ---------------------------------------------------------------------------

def test_the_groq_request_asks_for_a_real_stream():
    source = (ROOT / "core" / "brain.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in source.splitlines())
    assert '"stream": True' in code, "the Groq call no longer requests streaming"


def test_send_message_returns_an_ack_and_not_the_answer():
    """A slow turn timed out on the bridge and looked like 'Motor offline'."""
    source = (ROOT / "core" / "main.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in source.splitlines())
    body = code.split("def send_message(")[1].split("\ndef ")[0]
    assert "accepted" in body, "send_message must return an explicit ACK"
    assert "run_coro(" not in body, (
        "send_message still blocks on the full completion, which is what made "
        "a slow answer look like a dead backend"
    )
    assert "run_coroutine_threadsafe" in body


def test_the_ui_treats_an_accepted_ack_as_success():
    source = (ROOT / "frontend" / "pages" / "index.tsx").read_text(encoding="utf-8")
    assert "ack?.accepted" in source, (
        "the UI no longer distinguishes an accepted request from a dead bridge"
    )


def test_ui_notifications_never_block_waiting_for_the_browser():
    """`eel.fn(args)()` polls for a JS return value nobody needs.

    eel already sends the message when `eel.fn(args)` is called; the trailing
    `()` then blocks polling for a return value. Paying that round trip once
    per streamed chunk stalled the shared event loop and added up to 1.6 s to
    a single answer. Every notification must use the callback form instead.
    """
    source = (ROOT / "core" / "main.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in source.splitlines())
    import re as _re
    blocking = _re.findall(r"eel\.(on_\w+)\([^\n]*\)\(\)", code)
    assert not blocking, (
        f"these UI notifications still block on a browser round trip: {sorted(set(blocking))}"
    )
    assert "_notify_ui(" in code, "the non-blocking notification helper is gone"


def test_one_authoritative_user_bubble_per_turn():
    """The user's message was appearing twice, after Nano's reply.

    sendMessage appends [user, assistant], so on_stream_start's dedup check --
    which compared the text of the LAST message -- was looking at the empty
    assistant bubble, never matched, and appended the user's text again.
    Identity must come from the request id, never from text equality.
    """
    source = (ROOT / "frontend" / "pages" / "index.tsx").read_text(encoding="utf-8")
    assert "const userMessageId" in source, "the turn-scoped user id helper is gone"

    start = source.split('"on_stream_start"')[0].split("expose((msgId")[-1]
    assert "userMessageId(msgId)" in start, (
        "on_stream_start no longer identifies the user bubble by turn id"
    )
    assert "last.content !== userText" not in start, (
        "on_stream_start is back to comparing message text, which both misses "
        "the duplicate and breaks when the user sends the same text twice"
    )


def test_the_optimistic_user_bubble_uses_the_same_turn_id():
    source = (ROOT / "frontend" / "pages" / "index.tsx").read_text(encoding="utf-8")
    send = source.split("const sendMessage")[1].split("}, [")[0]
    assert "userMessageId(msgId)" in send, (
        "sendMessage inserts a user bubble with an id on_stream_start cannot "
        "recognise, so the message is inserted twice"
    )


def test_a_voice_turn_is_inserted_exactly_once():
    source = (ROOT / "frontend" / "pages" / "index.tsx").read_text(encoding="utf-8")
    block = source.split('"on_voice_exchange"')[0].split("expose((turnId")[-1]
    assert "m.id === turnId" in block, (
        "on_voice_exchange does not guard against inserting the same spoken "
        "turn twice"
    )


def test_transport_failure_and_model_failure_are_separate_ui_events():
    bridge = (ROOT / "frontend" / "public" / "nano_bridge.js").read_text(encoding="utf-8")
    assert "on_stream_error" in bridge
    assert "on_rate_limited" in bridge


# ---------------------------------------------------------------------------
# OLLAMA
# Cloud chat was issuing ~4 GET /api/tags per message.
# ---------------------------------------------------------------------------

def _describe_calls(monkeypatch):
    """Record which provider descriptions are actually performed."""
    from core import provider_status

    calls: list[str] = []
    monkeypatch.setattr(provider_status.providers, "describe_groq",
                        lambda *a, **k: calls.append("groq") or _groq())
    monkeypatch.setattr(provider_status.providers, "describe_ollama",
                        lambda *a, **k: calls.append("ollama") or _ollama())
    return calls


def _pair(mode):
    from core import provider_status

    return provider_status.describe_pair(
        mode, groq_fast_model="fast-model", groq_complex_model="strong-model",
        ollama_model="qwen3:8b", ollama_base_url="http://127.0.0.1:11434")


def test_cloud_mode_does_not_describe_ollama_at_all(monkeypatch):
    """Describing Ollama is what produced the per-message /api/tags traffic.

    Behavioural: the probe function is replaced with a recorder, so this fails
    if CLOUD mode ever contacts Ollama, however the code is spelled.
    """
    calls = _describe_calls(monkeypatch)
    _pair(ProviderMode.CLOUD)
    assert calls == ["groq"], f"CLOUD mode probed {calls}"


def test_local_mode_does_not_describe_groq_at_all(monkeypatch):
    """In LOCAL mode nothing leaves the machine -- not even a status probe."""
    calls = _describe_calls(monkeypatch)
    _pair(ProviderMode.LOCAL)
    assert calls == ["ollama"], f"LOCAL mode probed {calls}"


def test_auto_mode_describes_both(monkeypatch):
    calls = _describe_calls(monkeypatch)
    _pair(ProviderMode.AUTO)
    assert sorted(calls) == ["groq", "ollama"]


def test_the_provider_snapshot_is_cached():
    """A second read inside the TTL must not re-run the probe."""
    from core.provider_status import ProviderStatusCache

    cache = ProviderStatusCache(ttl_seconds=60.0)
    runs = []
    produce = lambda: runs.append(1) or {"state": "READY"}

    assert cache.get_fresh("k", produce) == {"state": "READY"}
    assert cache.get_fresh("k", produce) == {"state": "READY"}
    assert len(runs) == 1, "the cached snapshot re-probed inside its TTL"


def test_an_expired_provider_snapshot_is_refreshed():
    from core.provider_status import ProviderStatusCache

    cache = ProviderStatusCache(ttl_seconds=0.0)
    runs = []
    produce = lambda: runs.append(1) or {"state": "READY"}
    cache.get_fresh("k", produce)
    cache.get_fresh("k", produce)
    assert len(runs) == 2, "an expired snapshot was served as if it were current"


def test_invalidating_the_snapshot_forces_a_reprobe():
    """Saving a key must be visible immediately, not when the TTL expires."""
    from core.provider_status import ProviderStatusCache

    cache = ProviderStatusCache(ttl_seconds=600.0)
    runs = []
    produce = lambda: runs.append(1) or {"state": "READY"}
    cache.get_fresh("k", produce)
    cache.invalidate()
    cache.get_fresh("k", produce)
    assert len(runs) == 2


def test_a_high_frequency_poll_never_waits_on_the_network():
    """The 1 s Settings poll must serve stale data rather than block.

    The property under test is LATENCY, not probe count: a poller must never
    wait on the network. The producer is deliberately slow, so a single
    blocking call would exceed the whole assertion budget on its own.
    """
    import time as _time
    from core.provider_status import ProviderStatusCache

    PROBE_SECONDS = 0.4
    cache = ProviderStatusCache(ttl_seconds=0.0)   # every entry instantly stale

    def slow_produce():
        _time.sleep(PROBE_SECONDS)
        return {"state": "READY"}

    # Only the very first read for a key is allowed to pay the probe.
    # (A tolerance, not an exact floor: Windows' timer granularity is ~15 ms
    # and time.sleep can return a hair early. This line only establishes that
    # the probe really is slow; the assertion that matters is the one below.)
    started = _time.monotonic()
    assert cache.get_stale_ok("k", slow_produce) == {"state": "READY"}
    cold = _time.monotonic() - started
    assert cold >= PROBE_SECONDS * 0.9, f"the cold read took {cold:.3f}s; probe is not slow"

    # Every later read is served from the snapshot, even though it has expired.
    started = _time.monotonic()
    for _ in range(20):
        assert cache.get_stale_ok("k", slow_produce)["state"] == "READY"
    elapsed = _time.monotonic() - started
    assert elapsed < PROBE_SECONDS, (
        f"20 polls took {elapsed:.2f}s; one blocking probe is {PROBE_SECONDS}s, "
        "so the poller is waiting on the network"
    )


def test_the_chat_path_probes_providers_off_the_event_loop():
    """H6: a provider probe must never freeze the loop the chat runs on.

    describe_groq/describe_ollama are synchronous httpx calls. Running one
    inline from `async def chat` stalled streaming, eel callbacks and
    confirmation dialogs for the whole round trip.
    """
    import time as _time
    from core.provider_status import ProviderStatusCache

    PROBE_SECONDS = 0.5
    cache = ProviderStatusCache(ttl_seconds=60.0)

    def slow_produce():
        _time.sleep(PROBE_SECONDS)
        return {"state": "READY"}

    async def scenario():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            result = await cache.get_async("k", slow_produce)
        finally:
            beat.cancel()
        return result, ticks

    result, ticks = asyncio.run(scenario())
    assert result == {"state": "READY"}
    # A blocked loop cannot run the heartbeat at all.
    assert ticks > 5, f"the event loop only ticked {ticks} times during the probe"


def test_the_legacy_model_router_is_off_the_chat_hot_path():
    """Two competing routing systems is the defect; there must be exactly one."""
    source = (ROOT / "core" / "brain.py").read_text(encoding="utf-8")
    chat = source.split("async def chat(")[1].split("\n    def _record_metrics")[0]
    assert "model_router" not in chat
    # chat() routes through the async variant so the provider probe cannot block
    # the shared event loop; either spelling is the one authoritative router.
    assert "route_for_async(" in chat, "chat() must use the single authoritative router"
    assert "await self.route_for_async(" in chat, (
        "chat() must await the router: a synchronous probe here freezes the loop"
    )


def test_nano_never_preloads_a_local_model():
    settings = (ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
    assert "auto_download: false" in settings


# ---------------------------------------------------------------------------
# TTS
# Every typed reply was spoken, which is how a delayed "Olá" appeared.
# ---------------------------------------------------------------------------

def _should_speak(cfg, source):
    voice_cfg = cfg.get("voice", {})
    if not voice_cfg.get("tts_enabled", True):
        return False
    if source == "voice":
        return bool(voice_cfg.get("voice_reply_tts", True))
    return bool(voice_cfg.get("typed_chat_tts", False))


def test_typed_chat_does_not_speak_by_default():
    from core.config import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["voice"]["typed_chat_tts"] is False


def test_a_voice_turn_still_speaks_by_default():
    from core.config import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["voice"]["voice_reply_tts"] is True


def test_the_typed_chat_tts_setting_is_honoured():
    cfg = {"voice": {"tts_enabled": True, "typed_chat_tts": True, "voice_reply_tts": True}}
    assert _should_speak(cfg, "text") is True
    cfg["voice"]["typed_chat_tts"] = False
    assert _should_speak(cfg, "text") is False
    assert _should_speak(cfg, "voice") is True


def test_the_master_switch_silences_both_sources():
    cfg = {"voice": {"tts_enabled": False, "typed_chat_tts": True, "voice_reply_tts": True}}
    assert _should_speak(cfg, "text") is False
    assert _should_speak(cfg, "voice") is False


def test_main_routes_tts_through_the_source_aware_gate():
    source = (ROOT / "core" / "main.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in source.splitlines())
    body = code.split("async def _process_message(")[1].split("\ndef ")[0]
    assert "_should_speak(" in body, (
        "_process_message speaks without consulting the typed/voice gate"
    )


# ---------------------------------------------------------------------------
# WAKE / MICROPHONE
# A fixed RMS>=110 gate rejected 100% of this machine's audio.
# ---------------------------------------------------------------------------

def test_the_gate_calibrates_to_a_quiet_microphone():
    """Measured noise floor on the real machine was RMS 5-7."""
    gate = speech_filter.AdaptiveGate()
    threshold = gate.calibrate([6, 5, 7, 6, 7, 5, 6])
    assert threshold < 110, (
        "the derived threshold is still above the old fixed floor, which "
        "rejected every chunk on this microphone"
    )
    assert threshold >= speech_filter.MIN_ADAPTIVE_THRESHOLD


def test_the_threshold_is_bounded_below_so_a_dead_line_is_not_speech():
    gate = speech_filter.AdaptiveGate()
    gate.calibrate([0, 0, 0, 0, 0])
    assert gate.threshold >= speech_filter.MIN_ADAPTIVE_THRESHOLD


def test_the_threshold_is_bounded_above_so_a_noisy_room_stays_usable():
    gate = speech_filter.AdaptiveGate()
    gate.calibrate([50_000] * 8)
    assert gate.threshold <= speech_filter.MAX_ADAPTIVE_THRESHOLD


def test_ambient_noise_does_not_read_as_speech():
    gate = speech_filter.AdaptiveGate()
    gate.calibrate([6, 5, 7, 6, 7])
    quiet = _wav(amplitude=8, seconds=1.0)
    assert gate.has_speech(quiet) is False


def test_real_speech_level_audio_passes_the_calibrated_gate():
    gate = speech_filter.AdaptiveGate()
    gate.calibrate([6, 5, 7, 6, 7])
    speech = _wav(amplitude=900, seconds=1.0)
    assert gate.has_speech(speech) is True


def test_the_threshold_never_ratchets_itself_deaf():
    """The gate must not raise its own bar until nothing can pass.

    Measured live: a chunk landing just under the threshold was counted as
    "silence", folded into the noise floor, and pushed the threshold up --
    32.5 -> 39.5 -> 46.5 -> 60.5 while a test tone played. Each rise made the
    next utterance less likely to pass, so the wake phrase went permanently
    deaf while the UI still reported "A ouvir".
    """
    gate = speech_filter.AdaptiveGate()
    start = gate.calibrate([6, 5, 7, 6, 7, 5])
    # The real dynamic: chunk after chunk landing just UNDER the current bar.
    # Those are the ones routed into the floor estimate, so they are what
    # drives the runaway. Each iteration aims just below wherever the bar
    # currently sits, exactly as speech near the limit does in a real room.
    for _ in range(40):
        near_miss = gate.threshold * 0.9
        gate.observe(near_miss)
    assert gate.threshold <= start * 1.6, (
        f"threshold ratcheted from {start:.1f} to {gate.threshold:.1f}; "
        "energy just below the bar is being folded into the noise floor"
    )


def test_a_loud_event_does_not_move_the_noise_floor():
    gate = speech_filter.AdaptiveGate()
    gate.calibrate([6, 6, 6, 6, 6])
    floor_before = gate.noise_floor
    for _ in range(15):
        gate.observe(900.0)          # a door slam / someone talking
    assert gate.noise_floor <= floor_before * 2.0 + 1.0, (
        "a loud sound event was averaged into the room's baseline"
    )


def test_the_floor_still_adapts_to_a_genuinely_noisier_room():
    """The fix must not turn the gate back into a fixed threshold."""
    gate = speech_filter.AdaptiveGate()
    gate.calibrate([6, 6, 6, 6, 6])
    quiet_threshold = gate.threshold
    for _ in range(30):
        gate.observe(11.0)           # a fan switched on: a new, higher floor
    assert gate.noise_floor > 6.0, "the floor no longer tracks ambient change"
    assert gate.threshold > quiet_threshold


def test_drift_is_bounded_around_the_startup_measurement():
    gate = speech_filter.AdaptiveGate()
    gate.calibrate([10, 10, 10, 10, 10])
    for _ in range(60):
        gate.observe(19.0)           # just under the observe ceiling each time
    assert gate.noise_floor <= 10.0 * gate._MAX_DRIFT_UP + 0.01, (
        "the floor drifted beyond the bounded range around calibration"
    )


def test_a_silent_microphone_is_reported_as_such_not_as_listening():
    gate = speech_filter.AdaptiveGate()
    gate.calibrate([5, 5, 5, 5])
    for _ in range(12):
        gate.has_speech(_wav(amplitude=6, seconds=0.3))
    assert gate.looks_dead() is True


def test_a_working_microphone_is_never_reported_as_dead():
    gate = speech_filter.AdaptiveGate()
    gate.calibrate([6, 6, 6, 6])
    gate.has_speech(_wav(amplitude=900, seconds=0.5))
    for _ in range(12):
        gate.has_speech(_wav(amplitude=6, seconds=0.3))
    assert gate.looks_dead() is False


def test_looks_dead_needs_evidence_before_judging():
    gate = speech_filter.AdaptiveGate()
    gate.has_speech(_wav(amplitude=6, seconds=0.3))
    assert gate.looks_dead() is False, "one chunk is not enough to condemn a mic"


def _wav(amplitude: int, seconds: float, rate: int = 16000) -> bytes:
    """A synthetic tone at a given amplitude, as a 16-bit mono WAV."""
    import io, math, struct, wave
    frames = bytearray()
    for i in range(int(rate * seconds)):
        frames += struct.pack("<h", int(math.sin(2 * math.pi * 220 * (i / rate)) * amplitude))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


def test_bare_nano_stays_off_by_default():
    from core.config import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["voice"]["wake_phrase_allow_nano_only"] is False


def test_the_default_wake_phrase_is_portuguese():
    """Real recordings of "Hey Nano" transcribed as "Ei, não." / "NÃO!".

    faster-whisper-tiny is forced to Portuguese, and it does not render the
    English "Hey" reliably. Six real user utterances produced zero wake
    matches. "Ei" is a native interjection the model actually knows.
    """
    from core.wake_phrase import DEFAULT_WAKE_PHRASE
    from core.config import DEFAULT_CONFIG
    assert DEFAULT_WAKE_PHRASE == "ei nano"
    assert DEFAULT_CONFIG["voice"]["wake_phrase"] == "ei nano"


@pytest.mark.parametrize("heard", [
    "Ei Nano", "ei nano", "Ei, Nano!", "ei, nano.", "  EI   NANO  ",
    "ei nano podes ajudar",
])
def test_the_wake_phrase_matches_normal_stt_variation(heard):
    from core.wake_phrase import WakePhraseDetector
    detector = WakePhraseDetector(phrase="ei nano", allow_nano_only=False,
                                  cooldown_seconds=0.0)
    assert detector.matches(heard), f"{heard!r} should wake Nano"


@pytest.mark.parametrize("heard", [
    # Every one of these is something Whisper actually produced while the user
    # was NOT successfully calling Nano. None may wake it.
    "Nano", "nano", "Ei", "ei", "não", "NÃO!", "e aí", "E ai, no.",
    "Ei, nanos!", "ei nanos", "nanos", "ai na no", "Ei, não.",
    "nanotecnologia", "olá bom dia", "vamos ver isso", "",
])
def test_random_speech_does_not_wake_nano(heard):
    from core.wake_phrase import WakePhraseDetector
    detector = WakePhraseDetector(phrase="ei nano", allow_nano_only=False,
                                  cooldown_seconds=0.0)
    assert not detector.matches(heard), f"{heard!r} must not wake Nano"


def test_matching_is_not_broadly_fuzzy():
    """Near-misses must stay misses, or ordinary speech wakes Nano.

    "Ei, não." differs from "ei nano" by two characters. Any edit-distance or
    prefix style matching would accept it, and the transcriber emits it often.
    """
    from core.wake_phrase import WakePhraseDetector
    detector = WakePhraseDetector(phrase="ei nano", allow_nano_only=False,
                                  cooldown_seconds=0.0)
    assert detector.matches("ei nano") is True
    for near in ("ei nao", "ei não", "einano", "ei  nan", "ei nanoo", "hei nano"):
        assert detector.matches(near) is False, f"{near!r} is too fuzzy a match"


def test_bare_nano_stays_disabled_with_the_new_phrase():
    from core.wake_phrase import WakePhraseDetector
    detector = WakePhraseDetector(phrase="ei nano", allow_nano_only=False,
                                  cooldown_seconds=0.0)
    assert detector.matches("nano") is False
    # ...and the opt-in still works for anyone who deliberately enables it.
    permissive = WakePhraseDetector(phrase="ei nano", allow_nano_only=True,
                                    cooldown_seconds=0.0)
    assert permissive.matches("nano") is True


def test_the_wake_phrase_tester_never_wakes_nano():
    """The calibration tool must be diagnostic only."""
    source = (ROOT / "core" / "main.py").read_text(encoding="utf-8")
    body = source.split("def test_wake_phrase(")[1].split("\n@eel.expose")[0]
    code = "\n".join(line.split("#")[0] for line in body.splitlines())
    for forbidden in ("on_wake", "handle_wake_word", "process_request",
                      "brain.chat", "acknowledge_wake"):
        assert forbidden not in code, (
            f"the wake-phrase tester calls {forbidden!r}; it must never wake "
            "Nano, reach the Brain or run a tool"
        )
    assert "detector.matches" in code, "the tester does not use the real matcher"


def test_the_wake_phrase_tester_reports_the_transcript_verbatim():
    source = (ROOT / "core" / "main.py").read_text(encoding="utf-8")
    body = source.split("def test_wake_phrase(")[1].split("\n@eel.expose")[0]
    assert '"transcript": transcript' in body, (
        "the tester must return exactly what STT heard, not a summary"
    )


def test_every_offered_candidate_phrase_is_matchable():
    """A candidate the matcher could never accept would be a trap."""
    from core.main import WAKE_PHRASE_CANDIDATES
    from core.wake_phrase import WakePhraseDetector
    assert "ei nano" in WAKE_PHRASE_CANDIDATES
    for candidate in WAKE_PHRASE_CANDIDATES:
        detector = WakePhraseDetector(phrase=candidate, allow_nano_only=False,
                                      cooldown_seconds=0.0)
        assert detector.matches(candidate), f"{candidate!r} cannot match itself"
        assert detector.matches(f"{candidate.title()}!"), (
            f"{candidate!r} fails with normal punctuation/casing"
        )
        assert not detector.matches("nano"), f"{candidate!r} leaks a bare-nano match"


def test_mic_silent_is_a_real_readiness_state():
    from core.wake_phrase import WakePhraseReadiness
    assert WakePhraseReadiness.MIC_SILENT.value == "MIC_SILENT"


def test_readiness_degrades_to_mic_silent_when_no_audio_arrives():
    from core.wake_phrase import WakePhraseEngine, WakePhraseReadiness

    class DeadMic:
        _available = True
        gate = speech_filter.AdaptiveGate()   # the provider owns the gate
        def capture(self, seconds):
            return _wav(amplitude=5, seconds=0.2)

    class FakeSTT:
        online = True
        def transcribe(self, audio):
            raise AssertionError("silence must never reach the transcriber")

    engine = WakePhraseEngine({"wake_phrase_enabled": True}, lambda t: None,
                              audio_provider=DeadMic(), stt_provider=FakeSTT())
    engine.gate.calibrate([5, 5, 5, 5])
    for _ in range(12):
        engine.gate.has_speech(_wav(amplitude=5, seconds=0.2))
    assert engine.readiness() is WakePhraseReadiness.MIC_SILENT
    assert "microfone" in engine.explain().lower()


def test_the_wake_engine_exposes_diagnostic_counters():
    from core.wake_phrase import WakePhraseEngine

    class Mic:
        _available = True
        gate = speech_filter.AdaptiveGate()   # the provider owns the gate
        def capture(self, seconds): return b""

    class STT:
        online = True
        def transcribe(self, audio): return None

    engine = WakePhraseEngine({"wake_phrase_enabled": True}, lambda t: None,
                              audio_provider=Mic(), stt_provider=STT())
    status = engine.status()
    for key in ("chunks_captured", "silent_chunks", "speech_chunks",
                "transcripts_seen", "wake_matches", "audio"):
        assert key in status, f"status() is missing the {key!r} diagnostic"
    for key in ("noise_floor", "threshold", "last_rms"):
        assert key in status["audio"], f"audio diagnostics missing {key!r}"


def test_overlapping_windows_are_configured_so_a_phrase_is_never_split():
    from core import wake_phrase
    assert wake_phrase.OVERLAP_SECONDS > 0
    # The overlap must exceed a spoken "Hey Nano" (~0.8 s) or the phrase can
    # still land astride two windows and match neither.
    assert wake_phrase.OVERLAP_SECONDS >= 0.8


# ---------------------------------------------------------------------------
# VOICE TURN + STATE
# Silence after a wake produced a phantom "Olá".
# ---------------------------------------------------------------------------

def test_silence_after_a_wake_never_reaches_the_brain():
    from core.voice import VoiceRuntime, VoiceEngine

    engine = VoiceEngine({"enabled": True})
    engine.input_provider.capture = lambda *a, **k: _wav(amplitude=4, seconds=1.0)

    class ExplodingBrain:
        async def chat(self, *a, **k):
            raise AssertionError("silence must never produce a Brain request")

    runtime = VoiceRuntime(engine, brain=ExplodingBrain(), config={"voice": {}})
    result = asyncio.run(runtime.process_wake_word_turn(duration_seconds=1))
    assert result["ok"] is False
    assert result["cancelled"] is True
    assert result["error"] == "no_speech"
    assert "response" not in result, "a cancelled turn must not produce speech"


def test_the_command_window_is_bounded():
    from core.voice import VoiceRuntime, VoiceEngine
    runtime = VoiceRuntime(VoiceEngine({"enabled": True}),
                           config={"voice": {"wake_command_timeout_seconds": 999}})
    assert 3 <= runtime.command_timeout_seconds <= 15


def test_the_default_command_window_is_in_the_requested_range():
    from core.voice import VoiceRuntime, VoiceEngine
    runtime = VoiceRuntime(VoiceEngine({"enabled": True}), config={"voice": {}})
    assert 5 <= runtime.command_timeout_seconds <= 8


def test_session_transitions_log_the_previous_state_not_the_new_one(caplog):
    """Every log line read "X -> X", so the real sequence was unrecoverable."""
    import logging
    from core.voice import VoiceSession, VoiceSessionState

    session = VoiceSession({})
    with caplog.at_level(logging.INFO, logger="nano.voice"):
        session.transition(VoiceSessionState.LISTENING)
    lines = [r.getMessage() for r in caplog.records if "voice session state" in r.getMessage()]
    assert lines, "no transition was logged"
    assert "IDLE -> LISTENING" in lines[-1], f"expected a real transition, got {lines[-1]!r}"


def test_the_full_voice_state_sequence_is_representable():
    from core.wake_phrase import WakePhraseState
    for name in ("IDLE", "WAKE_LISTENING", "WAKE_DETECTED", "COMMAND_LISTENING", "PROCESSING"):
        assert hasattr(WakePhraseState, name)


# ---------------------------------------------------------------------------
# LAUNCHER / BUILD FRESHNESS
# ---------------------------------------------------------------------------

def test_a_changed_frontend_source_makes_the_build_stale(tmp_path):
    frontend = tmp_path / "frontend"
    (frontend / "components").mkdir(parents=True)
    (frontend / "out").mkdir(parents=True)
    index = frontend / "out" / "index.html"
    index.write_text("<html></html>", encoding="utf-8")
    stamp = frontend / "out" / ".stamp.json"

    component = frontend / "components" / "Thing.tsx"
    component.write_text("export const a = 1;", encoding="utf-8")
    frontend_build.write_stamp(stamp, frontend)
    assert frontend_build.is_stale(frontend, index=index, stamp=stamp) is False

    import os, time
    future = time.time() + 120
    component.write_text("export const a = 2;", encoding="utf-8")
    os.utime(component, (future, future))
    assert frontend_build.is_stale(frontend, index=index, stamp=stamp) is True


def test_an_unchanged_frontend_is_not_rebuilt(tmp_path):
    frontend = tmp_path / "frontend"
    (frontend / "styles").mkdir(parents=True)
    (frontend / "out").mkdir(parents=True)
    index = frontend / "out" / "index.html"
    index.write_text("<html></html>", encoding="utf-8")
    stamp = frontend / "out" / ".stamp.json"
    (frontend / "styles" / "a.css").write_text("body{}", encoding="utf-8")
    frontend_build.write_stamp(stamp, frontend)
    for _ in range(3):
        assert frontend_build.is_stale(frontend, index=index, stamp=stamp) is False


def test_a_missing_build_is_always_stale(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    assert frontend_build.is_stale(frontend, index=frontend / "out" / "index.html",
                                   stamp=frontend / "out" / "s.json") is True


def test_node_modules_are_never_walked(tmp_path):
    """Walking node_modules would make the check slower than the build."""
    frontend = tmp_path / "frontend"
    (frontend / "node_modules" / "pkg").mkdir(parents=True)
    (frontend / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
    (frontend / "components").mkdir(parents=True)
    (frontend / "components" / "A.tsx").write_text("x", encoding="utf-8")
    walked = [p for p in frontend_build._iter_sources(frontend)]
    assert all("node_modules" not in p.parts for p in walked)


# ---------------------------------------------------------------------------
# SECURITY (must survive this pass unchanged)
# ---------------------------------------------------------------------------

def test_the_trust_boundary_survives_whenever_tools_are_reachable():
    """Dropping the rules is a token saving only when no tool can be called."""
    source = (ROOT / "core" / "brain.py").read_text(encoding="utf-8")
    body = source.split("async def _build_system_prompt(")[1].split("\n    def ")[0]
    assert "TRUST_BOUNDARY_SYSTEM_RULES" in body
    assert "with_tools" in body and "_history_has_external_content" in body, (
        "the trust boundary must be kept whenever tools are offered or tool "
        "output already exists in the conversation"
    )


def test_the_saved_key_wins_over_a_stale_env_key():
    """A stale .env key silently beat the key saved in Settings.

    The Settings page reads the encrypted store and reported "Pronto", while
    the Brain was constructed from os.getenv(...) and failed every cloud
    request with AuthenticationError. secret_store.get_secret() already
    prefers the store and falls back to the environment, so it must be the
    only thing main.py consults.
    """
    source = (ROOT / "core" / "main.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in source.splitlines())
    assignment = [ln for ln in code.splitlines() if ln.startswith("API_KEY")]
    assert assignment, "API_KEY assignment not found in core/main.py"
    line = assignment[0]
    assert "secret_store.get_secret" in line, (
        "main.py must resolve the Groq key through secret_store, which puts "
        "the key saved in Settings ahead of the environment"
    )
    assert "os.getenv" not in line, (
        "reading the environment directly here lets a stale .env key override "
        "the key the user saved in Settings"
    )


def test_the_api_key_never_reaches_response_metadata():
    source = (ROOT / "core" / "brain.py").read_text(encoding="utf-8")
    body = source.split("self.last_metadata = {")[1].split("}")[0]
    for forbidden in ("api_key", "key", "secret", "token="):
        assert forbidden not in body.lower().replace("prompt_tokens", "").replace("max_tokens", ""), (
            f"response metadata may leak {forbidden!r}"
        )
