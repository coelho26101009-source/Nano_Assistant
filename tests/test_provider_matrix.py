"""The provider contract as one matrix: modes, status, credentials, failover, offline.

WHAT EACH SECTION PINS
----------------------
A. Routing contract      LOCAL / CLOUD / AUTO against every combination of
                         local and cloud availability, on the real router.
B. Status vs execution   a chat turn waits for provider status only as long as
                         its decision can depend on it: a hung provider that
                         cannot change the decision costs the turn nothing,
                         one that can costs at most ROUTE_WAIT_SECONDS, and a
                         provider whose status arrives after the decision can
                         still finish a turn that failed -- without anything
                         starting a probe on the failover's account.
C. Credentials           a key change rebuilds exactly the client it changes,
                         a removal leaves no client behind, and none of it
                         parses the CA bundle (the P4 stall's cause).
D. Failure policy        which failures may move a turn to another provider,
                         in AUTO and in CLOUD, including a key removed
                         mid-turn and a cancelled turn.
E. Local and offline     an unreachable local model is asked once; a slow one
                         gets the time the benchmark measured; no network at
                         all still answers locally within the route budget.
F. Transports            a streamed round, a local turn and a status probe
                         parse no CA bundle.

Everything drives the real Brain, the real router, the real status cache and
the real transports. The fakes are the ones the rest of the suite already
trusts to fail the way the real providers fail; nothing here reaches the
internet -- network conditions are local listeners, and cloud requests are
routed into them through HTTPS_PROXY where a real transport is involved.
"""
from __future__ import annotations

import asyncio
import json
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from core import http_tls, provider_failures, provider_status, providers, secret_store
from core.google_provider import GoogleAPIError
from core.provider_failures import FailureType
from core.providers import ProviderMode, ProviderState

from tests.test_multi_provider import (FakeGoogleClient, FakeMistralClient, RecordingExecutor,
                                       TOOLS, build_brain, google_call, google_text)
from tests.test_ollama_status import ClosingListener
from tests.test_provider_fallback import (FakeGroqClient, FakeGroqError, FakeOllamaClient,
                                          RATE_LIMIT_HEADERS, _Chunk, local_text)

ROOT = Path(__file__).resolve().parent.parent

READY, SETUP = ProviderState.READY, ProviderState.SETUP_REQUIRED
DOWN, UNKNOWN = ProviderState.UNAVAILABLE, ProviderState.UNKNOWN

#: Every variable a cloud credential can come from, and fake values for three.
CLOUD_ENV = ("NANO_API_KEY", "HELIOS_API_KEY", "GROQ_API_KEY", "NANO_GEMINI_API_KEY",
             "GEMINI_API_KEY", "GOOGLE_API_KEY", "NANO_MISTRAL_API_KEY", "MISTRAL_API_KEY")
FAKE_KEYS = {"GROQ_API_KEY": "gsk_" + "0" * 48,
             "MISTRAL_API_KEY": "mistral-" + "0" * 24,
             "GEMINI_API_KEY": "AIza" + "0" * 35}


def run(coro):
    return asyncio.run(coro)


async def collect(brain, message: str) -> str:
    return "".join([chunk async for chunk in brain.chat(message, stream=True)])


@pytest.fixture(autouse=True)
def clean_cooldowns():
    provider_failures.reset_all_cooldowns()
    yield
    provider_failures.reset_all_cooldowns()


# ===========================================================================
#  A. The routing contract
# ===========================================================================

def _payload(state: ProviderState, model: str = "m") -> dict:
    return {"state": state.value, "model": model, "tiers": {"fast": model, "complex": model},
            "detail": state.value.lower()}


# (mode, groq, ollama) -> (provider, usable, fallback)
CONTRACT = [
    # LOCAL: Ollama only, whatever the cloud can do. Never a cloud provider.
    (ProviderMode.LOCAL, READY, READY, "ollama", True, False),
    (ProviderMode.LOCAL, READY, DOWN, "ollama", False, False),
    (ProviderMode.LOCAL, DOWN, READY, "ollama", True, False),
    # CLOUD: the preferred provider only. Never a quiet downgrade to local.
    (ProviderMode.CLOUD, READY, READY, "groq", True, False),
    (ProviderMode.CLOUD, READY, DOWN, "groq", True, False),
    (ProviderMode.CLOUD, DOWN, READY, "groq", False, False),
    (ProviderMode.CLOUD, DOWN, DOWN, "groq", False, False),
    # AUTO: cloud first, local as the reported fallback, "none" said plainly.
    (ProviderMode.AUTO, READY, READY, "groq", True, False),
    (ProviderMode.AUTO, READY, DOWN, "groq", True, False),
    (ProviderMode.AUTO, DOWN, READY, "ollama", True, True),
    (ProviderMode.AUTO, DOWN, DOWN, "none", False, False),
    # Unmeasured is never routed to, however likely it is to be fine.
    (ProviderMode.AUTO, UNKNOWN, READY, "ollama", True, True),
    (ProviderMode.AUTO, READY, UNKNOWN, "groq", True, False),
]


@pytest.mark.parametrize("mode,groq,ollama,provider,usable,fallback", CONTRACT)
def test_the_routing_contract(mode, groq, ollama, provider, usable, fallback):
    route = providers.resolve_route(mode, _payload(groq), _payload(ollama, "qwen3:8b"))
    assert (route["provider"], route["usable"], route["fallback"]) == (provider, usable, fallback)
    assert route["mode"] == mode.value and route["tier"] == "FAST"


def test_auto_says_which_candidates_it_passed_over_and_why():
    route = providers.resolve_route(
        ProviderMode.AUTO, _payload(UNKNOWN), _payload(READY, "qwen3:8b"),
        mistral=_payload(DOWN), google=_payload(READY), preferred="groq")
    assert route["provider"] == "google"
    assert route["passed_over"] == [{"provider": "groq", "state": "UNKNOWN"},
                                    {"provider": "mistral", "state": "UNAVAILABLE"}]
    # Only AUTO passes over anything: CLOUD's one provider is still attempted.
    assert providers.resolve_route(ProviderMode.CLOUD, _payload(DOWN),
                                   _payload(READY))["passed_over"] == []


# ===========================================================================
#  B. Status vs execution
# ===========================================================================

@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Own status cache, own Ollama monitor (answering READY, never the network),
    own secret store, and no ambient credential or proxy."""
    from core import ollama_service
    from core.ollama_service import StatusMonitor

    cache = provider_status.ProviderStatusCache()
    monitor = StatusMonitor(prober=lambda _url: {"reachable": True, "installed": ["qwen3:8b"],
                                                 "executable": None})
    monkeypatch.setattr(provider_status, "CACHE", cache)
    monkeypatch.setattr(ollama_service, "STATUS", monitor)
    for name in CLOUD_ENV + ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr(secret_store, "_STORE_PATH", tmp_path / "secrets.dat")
    http_tls.prewarm()
    return cache, monitor


def describer(provider_id: str, *, state: ProviderState = READY,
              gate: threading.Event | None = None, calls: list | None = None):
    """A describe_* stand-in: answers ``state``, optionally only once ``gate`` opens."""
    def _describe(fast: str = "", strong: str = "") -> dict:
        if calls is not None:
            calls.append(provider_id)
        if gate is not None:
            assert gate.wait(15), f"the test never released {provider_id}"
        model = fast or f"{provider_id}-model"
        return {"id": provider_id, "name": providers.provider_name(provider_id),
                "kind": "cloud", "role": "cloud", "state": state.value,
                "model": model, "models": [model], "records": [],
                "secret": {"configured": True, "masked": "", "source": "environment",
                           "encrypted": False},
                "tiers": {"fast": model, "complex": strong or model},
                "detail": state.value.lower()}
    return _describe


def status_brain(monkeypatch, *, mode: str = "AUTO", preferred: str = "groq",
                 keys=("GROQ_API_KEY", "MISTRAL_API_KEY")):
    """A real Brain whose credentials come from fake environment keys and whose
    status comes from the REAL provider_status path -- only the describers,
    the transports and the local model are stand-ins."""
    from core import model_selection
    from core.brain import Brain
    from core.guardrails import GuardrailsEngine
    from core.memory import MemoryEngine

    for name in keys:
        monkeypatch.setenv(name, FAKE_KEYS[name])
    brain = Brain(api_key="", guardrails=GuardrailsEngine(), memory=MemoryEngine(),
                  config={"provider_mode": mode, "preferred_cloud": preferred,
                          "local": {"enabled": True, "model": "qwen3:8b"}})
    brain.provider_mode = mode
    brain.preferred_cloud = preferred
    brain.reload_cloud_credentials()
    brain._fake_ollama = FakeOllamaClient([local_text("resposta local")])  # type: ignore[attr-defined]
    monkeypatch.setattr(brain, "_local_http_client", lambda **kw: brain._fake_ollama)
    monkeypatch.setattr(model_selection, "select_tools", lambda *a, **k: [])

    async def _prompt(*_a, **_k):
        return "system"

    monkeypatch.setattr(brain, "_build_system_prompt", _prompt)
    return brain


def _wait_for(predicate, timeout: float = 10.0, message: str = "never happened"):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, message
        time.sleep(0.01)


@pytest.mark.parametrize("mode", ["AUTO", "CLOUD"])
def test_a_hung_provider_that_cannot_change_the_decision_costs_the_turn_nothing(
        isolated, monkeypatch, mode):
    """Measured before the fix: 10.46 s (AUTO) and 10.60 s (CLOUD) per turn, for
    a provider that was not answering and was not the one that answered."""
    gate = threading.Event()
    monkeypatch.setattr(providers, "describe_groq", describer("groq"))
    monkeypatch.setattr(providers, "describe_mistral", describer("mistral", gate=gate))
    brain = status_brain(monkeypatch, mode=mode)
    try:
        started = time.perf_counter()
        route = run(brain.route_for_async("olá"))
        elapsed = time.perf_counter() - started
    finally:
        gate.set()
    assert route["provider"] == "groq" and route["usable"] is True
    assert elapsed < 1.0, f"the route waited {elapsed:.2f}s for a provider it does not need"
    assert route["status"]["unmeasured"] == ["mistral"]


def test_the_status_a_turn_stopped_waiting_for_still_lands_for_the_next_one(isolated,
                                                                            monkeypatch):
    cache, _monitor = isolated
    gate = threading.Event()
    monkeypatch.setattr(providers, "describe_groq", describer("groq"))
    monkeypatch.setattr(providers, "describe_mistral", describer("mistral", gate=gate))
    brain = status_brain(monkeypatch)
    first = run(brain.route_for_async("olá"))
    assert first["status"]["source"] == "partial" and first["alternatives"] == []
    gate.set()
    _wait_for(lambda: cache._entries, message="the refresh never landed in the cache")
    second = run(brain.route_for_async("olá"))
    assert second["status"]["source"] == "cached"
    assert second["status"]["unmeasured"] == []
    assert second["alternatives"] == ["mistral"], "the late answer never became an alternative"
    assert cache.refresh_count == 1, "the second turn probed again instead of reading the cache"


def test_a_preferred_provider_that_does_not_answer_is_passed_over_within_the_budget(
        isolated, monkeypatch):
    monkeypatch.setattr(provider_status, "ROUTE_WAIT_SECONDS", 0.4)
    gate = threading.Event()
    monkeypatch.setattr(providers, "describe_groq", describer("groq", gate=gate))
    monkeypatch.setattr(providers, "describe_mistral", describer("mistral"))
    brain = status_brain(monkeypatch)
    try:
        started = time.perf_counter()
        route = run(brain.route_for_async("olá"))
        elapsed = time.perf_counter() - started
    finally:
        gate.set()
    assert route["provider"] == "mistral" and route["usable"] is True
    assert 0.35 <= elapsed < 2.0, f"routed after {elapsed:.2f}s against a 0.4 s budget"
    assert route["passed_over"] == [{"provider": "groq", "state": "UNKNOWN"}]
    assert route["status"]["unmeasured"] == ["groq"]


def test_cloud_mode_still_tries_its_provider_when_the_status_probe_is_late(isolated,
                                                                           monkeypatch):
    """CLOUD's standing rule -- a stale or missing probe is not a refusal to
    work -- survives the bounded wait."""
    monkeypatch.setattr(provider_status, "ROUTE_WAIT_SECONDS", 0.3)
    gate = threading.Event()
    monkeypatch.setattr(providers, "describe_groq", describer("groq", gate=gate))
    brain = status_brain(monkeypatch, mode="CLOUD")
    brain.client = FakeGroqClient([[_Chunk("resposta groq")]])
    try:
        answer = run(collect(brain, "olá"))
    finally:
        gate.set()
    assert "resposta groq" in answer
    assert brain.last_metadata["provider"] == "groq"
    assert brain._fake_ollama.requests == [], "CLOUD mode reached the local model"


def _late_mistral_brain(monkeypatch, gate: threading.Event):
    monkeypatch.setattr(providers, "describe_groq", describer("groq"))
    monkeypatch.setattr(providers, "describe_mistral", describer("mistral", gate=gate))
    brain = status_brain(monkeypatch)
    brain.client = FakeGroqClient([FakeGroqError(429, RATE_LIMIT_HEADERS)])
    brain.mistral_client = FakeMistralClient([google_text("resposta mistral")])
    return brain


def test_a_provider_whose_status_arrives_late_still_takes_over_a_failed_turn(isolated,
                                                                            monkeypatch):
    """The failover the declared order exists for -- Groq rate-limited, Mistral
    finishes -- when Groq's probe answered first and the turn left without
    waiting for Mistral's."""
    gate = threading.Event()
    brain = _late_mistral_brain(monkeypatch, gate)
    threading.Timer(0.3, gate.set).start()
    answer = run(collect(brain, "olá"))
    assert "resposta mistral" in answer
    assert [(a["provider"], a["outcome"]) for a in brain.last_metadata["provider_attempts"]] == [
        ("groq", "RATE_LIMIT"), ("mistral", "ok")]
    assert brain._fake_ollama.requests == [], "the turn skipped a healthy cloud provider"


def test_without_the_late_lookup_that_same_turn_ends_on_the_local_model(isolated, monkeypatch):
    """The control for the test above: the late lookup is what keeps Mistral."""
    gate = threading.Event()
    brain = _late_mistral_brain(monkeypatch, gate)

    async def none(*_a, **_k):
        return []

    monkeypatch.setattr(brain, "_late_alternatives", none)
    threading.Timer(0.3, gate.set).start()
    answer = run(collect(brain, "olá"))
    assert "resposta local" in answer
    assert brain.mistral_client.bodies == []


def test_failing_over_never_starts_a_status_probe(isolated, monkeypatch):
    cache, _monitor = isolated
    gate, calls = threading.Event(), []
    monkeypatch.setattr(providers, "describe_groq", describer("groq", calls=calls))
    monkeypatch.setattr(providers, "describe_mistral",
                        describer("mistral", gate=gate, calls=calls))
    brain = _late_mistral_brain(monkeypatch, gate)
    monkeypatch.setattr(providers, "describe_groq", describer("groq", calls=calls))
    monkeypatch.setattr(providers, "describe_mistral",
                        describer("mistral", gate=gate, calls=calls))
    route = run(brain.route_for_async("olá"))
    assert route["status"]["unmeasured"] == ["mistral"]
    before = list(calls)
    # Nothing cached and nothing in flight that is still current: a failover
    # now may read, but may not ask.
    cache.invalidate()
    try:
        late = run(brain._late_alternatives(route, tried={"groq"}))
    finally:
        gate.set()
    assert late == []
    assert calls == before, f"failing over started a probe: {calls[len(before):]}"


def test_a_turn_that_began_in_auto_asks_no_new_cloud_provider_after_a_switch_to_local(
        isolated, monkeypatch):
    gate = threading.Event()
    brain = _late_mistral_brain(monkeypatch, gate)

    class SwitchedMidTurn(FakeGroqClient):
        async def create(self, **kwargs):
            brain.provider_mode = "LOCAL"          # the user switches while Groq works
            gate.set()                             # and Mistral's status arrives
            return await super().create(**kwargs)

    brain.client = SwitchedMidTurn([FakeGroqError(429, RATE_LIMIT_HEADERS)])
    run(collect(brain, "olá"))
    assert brain.mistral_client.bodies == [], "a cloud provider joined after the switch to LOCAL"


def test_local_mode_routes_without_asking_any_cloud_provider(isolated, monkeypatch):
    calls: list[str] = []
    for pid, name in (("groq", "describe_groq"), ("mistral", "describe_mistral"),
                      ("google", "describe_google")):
        monkeypatch.setattr(providers, name, describer(pid, calls=calls))
    brain = status_brain(monkeypatch, mode="LOCAL",
                         keys=("GROQ_API_KEY", "MISTRAL_API_KEY", "GEMINI_API_KEY"))
    route = run(brain.route_for_async("olá"))
    assert route["provider"] == "ollama" and route["usable"] is True
    assert calls == [], f"LOCAL mode described {calls}"
    assert route["status"]["source"] == "measured"


def test_cloud_mode_routes_without_asking_ollama(isolated, monkeypatch):
    _cache, monitor = isolated
    monkeypatch.setattr(providers, "describe_groq", describer("groq"))
    monkeypatch.setattr(providers, "describe_mistral", describer("mistral"))
    brain = status_brain(monkeypatch, mode="CLOUD")
    route = run(brain.route_for_async("olá"))
    assert route["provider"] == "groq"
    assert monitor.probes_started == 0, "CLOUD mode asked Ollama for its status"


def test_every_turn_logs_where_it_went_and_on_what_evidence(isolated, monkeypatch, caplog):
    gate = threading.Event()
    monkeypatch.setattr(providers, "describe_groq", describer("groq"))
    monkeypatch.setattr(providers, "describe_mistral", describer("mistral", gate=gate))
    brain = status_brain(monkeypatch)
    brain.client = FakeGroqClient([[_Chunk("olá")]])
    caplog.set_level("INFO", logger="nano.brain")
    try:
        run(collect(brain, "olá"))
    finally:
        gate.set()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Route:")]
    assert len(lines) == 1, lines
    assert "provider=groq" in lines[0] and "unmeasured=mistral" in lines[0]
    for secret in FAKE_KEYS.values():
        assert secret not in caplog.text


# ===========================================================================
#  C. Credentials
# ===========================================================================

def _clients(brain) -> tuple:
    return brain.client, brain.google_client, brain.mistral_client


def test_an_unchanged_credential_keeps_its_client(isolated, monkeypatch):
    brain = status_brain(monkeypatch, keys=tuple(FAKE_KEYS))
    before = _clients(brain)
    assert all(client is not None for client in before)
    brain.reload_cloud_credentials()
    assert all(a is b for a, b in zip(before, _clients(brain))), (
        "an unchanged key had its client rebuilt")


def test_a_changed_credential_replaces_only_its_own_client(isolated, monkeypatch):
    brain = status_brain(monkeypatch, keys=tuple(FAKE_KEYS))
    groq, google, mistral = _clients(brain)
    monkeypatch.setenv("MISTRAL_API_KEY", "mistral-" + "9" * 24)
    brain.reload_cloud_credentials()
    assert brain.mistral_client is not mistral
    assert brain.mistral_client.holds_key("mistral-" + "9" * 24)
    assert not brain.mistral_client.holds_key(FAKE_KEYS["MISTRAL_API_KEY"])
    assert brain.client is groq and brain.google_client is google


def test_a_removed_credential_leaves_no_client_behind(isolated, monkeypatch):
    brain = status_brain(monkeypatch, keys=tuple(FAKE_KEYS))
    monkeypatch.delenv("GROQ_API_KEY")
    assert brain.reload_cloud_credentials() is True       # Google and Mistral remain
    assert brain.client is None and brain.groq_enabled is False
    assert brain._cloud_provider_ready("groq") is False
    assert brain._cloud_provider_ready("mistral") is True


def test_an_added_credential_is_usable_by_the_very_next_turn(isolated, monkeypatch):
    monkeypatch.setattr(providers, "describe_groq", describer("groq", state=SETUP))
    monkeypatch.setattr(providers, "describe_google", describer("google"))
    brain = status_brain(monkeypatch, mode="CLOUD", preferred="google", keys=())
    assert brain.google_client is None
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEYS["GEMINI_API_KEY"])
    brain.reload_cloud_credentials()
    brain.google_client = FakeGoogleClient([google_text("resposta gemini")])
    assert "resposta gemini" in run(collect(brain, "olá"))


def test_a_credential_reload_parses_no_ca_bundle_even_behind_a_proxy(isolated, monkeypatch):
    """The P4 stall's cause, on the Brain itself: every AsyncGroq built a fresh
    context, two with a proxy configured -- 430-530 ms measured."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    brain = status_brain(monkeypatch, keys=tuple(FAKE_KEYS))
    parses = []
    real = ssl.SSLContext.load_verify_locations
    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations",
                        lambda self, *a, **k: parses.append(1) or real(self, *a, **k))
    for index in range(3):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_" + str(index + 1) * 48)
        brain.reload_cloud_credentials()
    assert brain.client.api_key == "gsk_" + "3" * 48
    assert parses == [], f"{len(parses)} CA-bundle parse(s) across three key changes"


def test_no_credential_reaches_the_log_on_a_reload(isolated, monkeypatch, caplog):
    caplog.set_level("DEBUG")
    brain = status_brain(monkeypatch, keys=tuple(FAKE_KEYS))
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "7" * 48)
    brain.reload_cloud_credentials()
    for secret in (*FAKE_KEYS.values(), "gsk_" + "7" * 48):
        assert secret not in caplog.text


# ===========================================================================
#  D. Failure policy
# ===========================================================================

class RemovesKeyWhileRunning(RecordingExecutor):
    """The tool runs -- and while it does, the user removes the Google key."""

    def __init__(self, brain_ref: list):
        super().__init__()
        self.brain_ref = brain_ref

    async def execute_tool_async(self, name, args=None, **kw):
        brain = self.brain_ref[0]
        brain.google_client = None                 # what reload does on removal
        brain.google_enabled = False
        return await super().execute_tool_async(name, args, **kw)


def test_a_key_removed_mid_turn_is_missing_not_refused_and_its_client_is_not_reused(
        monkeypatch):
    holder: list = []
    executor = RemovesKeyWhileRunning(holder)
    brain = build_brain(
        monkeypatch, preferred="google", tools=TOOLS, executor=executor,
        google_script=[google_call("pc_volume_set", {"level": 30}), google_text("nunca")],
        groq_script=[[_Chunk("Volume definido.")]])
    holder.append(brain)
    old_google = brain.google_client
    answer = run(collect(brain, "põe o volume a 30"))

    assert "Volume definido." in answer
    assert len(old_google.bodies) == 1, "the removed key's client was used again"
    assert executor.executions == [("pc_volume_set", {"level": 30})]
    outcomes = [(a["provider"], a.get("outcome")) for a in brain.last_metadata["provider_attempts"]]
    assert outcomes == [("google", "NOT_CONFIGURED"), ("groq", "ok")]
    from core import response_meta
    shaped = response_meta.for_message(brain.last_metadata)
    assert shaped["provider_attempts"][0]["outcome"] == "setup_required"
    assert "recusada" not in answer


def test_in_cloud_mode_a_key_removed_mid_turn_is_said_plainly(monkeypatch):
    holder: list = []
    executor = RemovesKeyWhileRunning(holder)
    brain = build_brain(
        monkeypatch, mode="CLOUD", preferred="google", tools=TOOLS, executor=executor,
        google_script=[google_call("pc_volume_set", {"level": 30})],
        groq_script=[[_Chunk("nunca")]])
    holder.append(brain)
    answer = run(collect(brain, "põe o volume a 30"))
    assert "já não tem uma chave de API configurada" in answer
    assert "recusada" not in answer
    assert brain.client.calls == [] and brain._fake_ollama.requests == []


FAILOVER_POLICY = [
    # (failure raised by the preferred provider, may another provider finish?)
    (lambda: GoogleAPIError(429, "quota", {"retry-after": "5s"}), True),
    (lambda: GoogleAPIError(408, "timeout: ReadTimeout"), True),
    (lambda: GoogleAPIError(503, "transport: ConnectError"), True),
    (lambda: GoogleAPIError(500, "internal"), True),
    (lambda: GoogleAPIError(404, "model not found"), True),
    (lambda: ConnectionError("network unreachable"), True),
    (lambda: provider_failures.ProviderNotConfigured("google"), True),
    (lambda: GoogleAPIError(401, "API key not valid"), False),
    (lambda: GoogleAPIError(403, "permission denied"), False),
    (lambda: GoogleAPIError(400, "invalid argument"), False),
]


@pytest.mark.parametrize("make_failure,falls_over", FAILOVER_POLICY,
                         ids=[f"{i}-{'over' if ok else 'stop'}"
                              for i, (_f, ok) in enumerate(FAILOVER_POLICY)])
def test_the_failover_policy_in_auto(monkeypatch, make_failure, falls_over):
    brain = build_brain(monkeypatch, preferred="google", google_script=[make_failure()],
                        groq_script=[[_Chunk("resposta groq")]])
    answer = run(collect(brain, "olá"))
    if falls_over:
        assert "resposta groq" in answer
        assert brain.last_metadata["fallback_used"] is True
    else:
        assert brain.client.calls == [], "a configuration failure was hidden behind Groq"
        assert brain._fake_ollama.requests == [], "a configuration failure was hidden locally"
        assert answer.startswith("**")


@pytest.mark.parametrize("make_failure,_falls_over", FAILOVER_POLICY,
                         ids=[str(i) for i in range(len(FAILOVER_POLICY))])
def test_cloud_mode_never_fails_over_whatever_the_failure(monkeypatch, make_failure,
                                                         _falls_over):
    brain = build_brain(monkeypatch, mode="CLOUD", preferred="google",
                        google_script=[make_failure()], groq_script=[[_Chunk("nunca")]])
    run(collect(brain, "olá"))
    assert brain.client.calls == [] and brain._fake_ollama.requests == []


class _CancelledMidStream(FakeGoogleClient):
    """The turn is cancelled while the provider streams. CancelledError is a
    BaseException, which the scripted fake does not raise, so this one does."""

    async def stream(self, model, body, collector=None):
        self.bodies.append({"model": model, "body": body})
        raise asyncio.CancelledError()
        yield  # pragma: no cover - makes this an async generator


def test_a_cancelled_turn_is_not_continued_anywhere_else(monkeypatch):
    brain = build_brain(monkeypatch, preferred="google", groq_script=[[_Chunk("nunca")]])
    brain.google_client = _CancelledMidStream([])
    with pytest.raises(asyncio.CancelledError):
        run(collect(brain, "olá"))
    assert brain.client.calls == [], "a cancelled turn was handed to Groq"
    assert brain._fake_ollama.requests == [], "a cancelled turn was handed to the local model"


def test_a_failure_is_never_retried_on_the_same_provider(monkeypatch):
    brain = build_brain(monkeypatch, preferred="google",
                        google_script=[GoogleAPIError(503, "down"), google_text("nunca")],
                        groq_script=[FakeGroqError(503)], ollama_script=[local_text("local")])
    answer = run(collect(brain, "olá"))
    assert "local" in answer
    assert len(brain.google_client.bodies) == 1 and len(brain.client.calls) == 1


# ===========================================================================
#  E. Local and offline
# ===========================================================================

class _UnreachableOllama:
    """Ollama not running: every connection fails, and every attempt is counted."""

    def __init__(self):
        self.posts = 0
        self.streams = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, *_a, **_kw):
        self.posts += 1
        raise httpx.ConnectError("connection refused")

    def stream(self, *_a, **_kw):
        self.streams += 1
        raise httpx.ConnectError("connection refused")


def test_an_unreachable_local_model_is_asked_once_not_twice(monkeypatch):
    brain = build_brain(monkeypatch, mode="LOCAL")
    dead = _UnreachableOllama()
    monkeypatch.setattr(brain, "_local_http_client", lambda **kw: dead)
    answer = run(collect(brain, "olá"))
    assert "O modelo local ainda não está disponível" in answer
    assert (dead.posts, dead.streams) == (1, 0), (
        f"{dead.posts} request(s) and {dead.streams} retry(ies) to a server that was not there")
    assert brain.last_metadata["provider_attempts"][-1]["outcome"] == "unavailable"
    assert brain.conversation == [], "a turn nobody answered was left in the history"


def test_a_local_answer_gets_at_least_the_time_the_benchmark_measured(monkeypatch):
    """Pinned to the EVIDENCE rather than to a number: the longest local turn
    the committed benchmark measured, with the request shape the Brain sends."""
    results = json.loads((ROOT / "benchmarks" / "provider_routing" /
                          "benchmark_results.json").read_text(encoding="utf-8"))
    local_runs = [case for model, cases in results["per_case"].items()
                  if model.startswith("ollama:")
                  for case in (cases if isinstance(cases, list) else cases.values())]
    longest_ms = max(case["total_ms"] for case in local_runs
                     if isinstance(case.get("total_ms"), (int, float)))
    seen: dict = {}
    brain = build_brain(monkeypatch, mode="LOCAL")
    fake = brain._fake_ollama
    monkeypatch.setattr(brain, "_local_http_client", lambda **kw: seen.update(kw) or fake)
    run(collect(brain, "olá"))
    assert seen["timeout"].read * 1000 > longest_ms, (
        f"read budget {seen['timeout'].read}s is shorter than a measured local turn "
        f"({longest_ms / 1000:.1f}s)")
    assert seen["verify"] is http_tls.shared_context()


def test_offline_auto_answers_locally_within_the_route_budget(isolated, monkeypatch):
    """No network at all: the REAL describers, with every HTTPS request routed
    into a listener that drops the connection -- what a broken network looks
    like from here -- and a local model that is up."""
    listener = ClosingListener()
    try:
        monkeypatch.setenv("HTTPS_PROXY", listener.url)
        monkeypatch.setattr(provider_status, "ROUTE_WAIT_SECONDS", 3.0)
        brain = status_brain(monkeypatch, keys=tuple(FAKE_KEYS))
        started = time.perf_counter()
        answer = run(collect(brain, "olá"))
        elapsed = time.perf_counter() - started
    finally:
        listener.close()
    assert "resposta local" in answer
    assert brain.last_metadata["provider"] == "ollama"
    assert brain.last_metadata["fallback_used"] is True
    assert elapsed < 3.0 + 2.0, f"an offline turn took {elapsed:.2f}s"
    assert listener.connections >= 1, "the probes never reached the simulated network"


def test_a_hung_network_costs_auto_at_most_the_route_budget(isolated, monkeypatch):
    """Worse than offline: connections are accepted and never answered, so
    every probe would wait out its 10 s timeout."""
    from tests.test_bridge_responsiveness import HungListener

    hung = HungListener()
    try:
        monkeypatch.setenv("HTTPS_PROXY", hung.url)
        monkeypatch.setattr(provider_status, "ROUTE_WAIT_SECONDS", 0.5)
        brain = status_brain(monkeypatch, keys=tuple(FAKE_KEYS))
        started = time.perf_counter()
        answer = run(collect(brain, "olá"))
        elapsed = time.perf_counter() - started
    finally:
        hung.close()
    assert "resposta local" in answer
    assert elapsed < 0.5 + 2.0, f"a hung network held the turn for {elapsed:.2f}s"
    assert brain.last_metadata["route_status"]["source"] == "partial"


def test_offline_with_the_local_model_down_too_says_so(isolated, monkeypatch):
    _cache, monitor = isolated
    monitor._prober = lambda _url: {"reachable": False, "installed": [], "executable": True}
    for pid, name in (("groq", "describe_groq"), ("mistral", "describe_mistral")):
        monkeypatch.setattr(providers, name, describer(pid, state=DOWN))
    brain = status_brain(monkeypatch)
    dead = _UnreachableOllama()
    monkeypatch.setattr(brain, "_local_http_client", lambda **kw: dead)
    answer = run(collect(brain, "olá"))
    assert "O modelo local ainda não está disponível" in answer
    assert dead.posts == 1 and dead.streams == 0


# ===========================================================================
#  F. Transports: nothing parses the CA bundle per request
# ===========================================================================

class _SSE(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802 - the status probes
        body = json.dumps({"data": [{"id": "m-1"}], "models": [{"name": "models/m-1"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 - a streamed round, or a local turn
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.startswith("/api/chat"):
            body = json.dumps({"message": {"role": "assistant", "content": "olá local"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        frame = ({"choices": [{"delta": {"content": "olá"}}]} if "chat/completions" in self.path
                 else {"candidates": [{"content": {"parts": [{"text": "olá"}]}}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(f"data: {json.dumps(frame)}\n\n".encode())


@pytest.fixture
def local_server(monkeypatch):
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SSE)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    http_tls.prewarm()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def ca_parses(monkeypatch):
    parses: list[str] = []
    real = ssl.SSLContext.load_verify_locations

    def counting(self, *a, **k):
        parses.append(threading.current_thread().name)
        return real(self, *a, **k)

    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", counting)
    return parses


@pytest.mark.parametrize("provider_id", ["google", "mistral"])
def test_a_streamed_cloud_round_parses_no_ca_bundle(local_server, ca_parses, provider_id):
    """Measured before the fix: 269 / 204 ms of stalled event loop per round."""
    from core.google_provider import GoogleChat
    from core.mistral_provider import MistralChat

    transport = (GoogleChat("fake-key", base_url=local_server) if provider_id == "google"
                 else MistralChat("fake-key", base_url=local_server))

    async def rounds():
        pieces = []
        for _ in range(3):
            async for piece in transport.stream("m-1", {}, {}):
                pieces.append(piece)
        return pieces

    assert run(rounds()) == ["olá"] * 3
    assert ca_parses == [], f"{len(ca_parses)} CA-bundle parse(s) in three rounds"


def test_a_local_turn_parses_no_ca_bundle(local_server, ca_parses, monkeypatch):
    from core import model_selection
    from core.brain import Brain
    from core.guardrails import GuardrailsEngine
    from core.memory import MemoryEngine

    brain = Brain(api_key="", guardrails=GuardrailsEngine(), memory=MemoryEngine(),
                  config={"provider_mode": "LOCAL",
                          "local": {"enabled": True, "model": "qwen3:8b", "url": local_server}})
    monkeypatch.setattr(model_selection, "select_tools", lambda *a, **k: [])
    answer = "".join(run(_collect_local(brain)))
    assert "olá local" in answer
    assert ca_parses == [], f"{len(ca_parses)} CA-bundle parse(s) for one local turn"


async def _collect_local(brain) -> list[str]:
    return [piece async for piece in brain._ollama_fallback("olá", "teste", "system")]


@pytest.mark.parametrize("provider_id", ["groq", "google", "mistral"])
def test_a_status_probe_parses_no_ca_bundle(local_server, ca_parses, monkeypatch, provider_id):
    from core import google_provider, mistral_provider

    monkeypatch.setattr(providers, "GROQ_API_BASE", local_server)
    monkeypatch.setattr(google_provider, "GOOGLE_API_BASE", local_server)
    monkeypatch.setattr(mistral_provider, "MISTRAL_API_BASE", local_server)
    listing = {"groq": lambda: providers.list_groq_models("fake-key"),
               "google": lambda: google_provider.list_google_models("fake-key"),
               "mistral": lambda: mistral_provider.list_mistral_models("fake-key")}[provider_id]
    models, error = listing()
    assert error is None and models
    assert ca_parses == [], f"{len(ca_parses)} CA-bundle parse(s) for one {provider_id} probe"
