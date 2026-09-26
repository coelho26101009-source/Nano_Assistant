"""No provider, model or status bridge call may wait on the network.

WHAT THESE TESTS GUARD, AS IT WAS MEASURED ON WINDOWS, through the live eel
bridge, with a cheap call sent 30 ms after the slow one:

    set_local_model, Ollama stopped .......... 2001 ms; the cheap call waited 1971 ms
    first get_providers, a provider hung ..... 10436 ms; the cheap call waited 10393 ms
    set_provider_mode, provider hung ......... 10424 ms; the cheap call waited 10395 ms
    first get_providers, healthy providers ... 595 ms;   the cheap call waited 552 ms
    readiness once every 30 s ................ 42 ms, 40 of them re-enumerating PortAudio
    startup, Ollama installed but stopped .... window after 7.3 s, 6.0 s of it on Ollama

eel serves every bridge call from ONE gevent hub and nothing in Nano is
monkey-patched, so any blocking wait inside a bridge call stops all of them.

HOW. The real exposed functions are dispatched through eel's own
``_process_message`` on the gevent hub, as eel does for a websocket message,
and latency is measured from when the client SENT each call. "Hung" means a
socket that accepts and never answers -- slow on every OS -- or a describer
blocked on an event, where the test has to control exactly when it answers.
Each test gets its own Ollama monitor, provider cache, settings file and
secret store, and cloud credentials exist only if the test sets them.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

import pytest

from core import ollama_service, provider_status, providers, secret_store, user_settings
from core.ollama_service import OllamaState, StatusMonitor
from core.providers import ProviderMode, ProviderState
from tests.test_ollama_status import MODEL, ClosingListener, FakeOllama, _RecordingSocket

ROOT = Path(__file__).resolve().parent.parent

#: Every environment variable a cloud credential can come from.
_CLOUD_ENV = ("NANO_API_KEY", "HELIOS_API_KEY", "GROQ_API_KEY", "NANO_GEMINI_API_KEY",
              "GEMINI_API_KEY", "GOOGLE_API_KEY", "NANO_MISTRAL_API_KEY", "MISTRAL_API_KEY")
_FAKE_KEYS = {"GROQ_API_KEY": "gsk_" + "0" * 48,
              "MISTRAL_API_KEY": "mistral-" + "0" * 24,
              "GEMINI_API_KEY": "AIza" + "0" * 35}

#: A bridge call that did not wait for anything answers in milliseconds. This
#: is ~20x that and ~20x below the freezes above, so it separates the two
#: without being sensitive to a busy CI runner.
CHEAP_LIMIT = 0.5


# ---------------------------------------------------------------------------
#  Stand-ins
# ---------------------------------------------------------------------------

class HungListener:
    """Accepts every TCP connection, never answers, and counts them.

    As HTTPS_PROXY it makes every cloud request hang exactly like an endpoint
    that stopped answering -- and nothing, credentials included, leaves the
    machine: the key would only be sent inside a tunnel that never opens.
    """

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(64)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        self.connections = 0
        self._held: list[socket.socket] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with self._lock:
                self.connections += 1
                self._held.append(conn)

    def close(self):
        self.sock.close()
        with self._lock:
            for conn in self._held:
                try:
                    conn.close()
                except OSError:
                    pass


@pytest.fixture
def hung_endpoint():
    listener = HungListener()
    yield listener
    listener.close()


class _FakePyAudio:
    """PortAudio as seen through pyaudio: slow to initialise, and counted."""

    delay = 0.0
    constructed = 0
    devices = [{"name": "Fake Mic", "maxInputChannels": 1, "maxOutputChannels": 0},
               {"name": "Fake Speaker", "maxInputChannels": 0, "maxOutputChannels": 2}]

    def __init__(self):
        type(self).constructed += 1
        time.sleep(type(self).delay)

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, index):
        return dict(self.devices[index], index=index)

    def get_default_output_device_info(self):
        return {"index": 1, "name": "Fake Speaker"}

    def terminate(self):
        pass


@pytest.fixture
def fake_pyaudio(monkeypatch):
    fake = types.ModuleType("pyaudio")
    fake.PyAudio = type("PyAudio", (_FakePyAudio,), {"constructed": 0, "delay": 0.0})
    fake.paInt16 = 8
    monkeypatch.setitem(sys.modules, "pyaudio", fake)
    return fake.PyAudio


# ---------------------------------------------------------------------------
#  The running application, isolated per test
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def main_module():
    import core.main as module
    return module


class Bridge:
    """The live app's provider/status state, private to one test."""

    def __init__(self, main_module, monkeypatch, tmp_path):
        self.main = main_module
        self.monkeypatch = monkeypatch
        self.monitor = StatusMonitor()
        self.cache = provider_status.ProviderStatusCache()
        monkeypatch.setattr(ollama_service, "STATUS", self.monitor)
        monkeypatch.setattr(provider_status, "CACHE", self.cache)
        for name in _CLOUD_ENV:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("ALL_PROXY", raising=False)
        # Settings and secrets this test writes go to its own directory, and
        # the process's copies come back exactly as they were.
        monkeypatch.setattr(user_settings, "_PATH", tmp_path / "user_settings.json")
        monkeypatch.setattr(user_settings, "_cache", dict(user_settings.all_settings()))
        monkeypatch.setattr(secret_store, "_STORE_PATH", tmp_path / "secrets.dat")
        brain = main_module.brain
        # Everything a setter or a credential reload rebinds on the Brain.
        for attr in ("ollama_url", "ollama_model", "local_enabled", "provider_mode",
                     "preferred_cloud", "groq_model", "groq_fast_model", "groq_complex_model",
                     "google_fast_model", "google_complex_model",
                     "mistral_fast_model", "mistral_complex_model",
                     "groq_enabled", "client", "google_enabled", "google_client",
                     "mistral_enabled", "mistral_client"):
            monkeypatch.setattr(brain, attr, getattr(brain, attr, None), raising=False)
        config = main_module.CONFIG
        for key in ("provider_mode", "preferred_cloud", "groq_model",
                    "groq_fast_model", "groq_complex_model", "google_fast_model",
                    "google_complex_model", "mistral_fast_model", "mistral_complex_model"):
            monkeypatch.setitem(config, key, config.get(key))
        monkeypatch.setitem(config, "local", dict(config.get("local") or {}))
        # raising=False: these tests must also run -- and fail for the right
        # reason -- against a main.py that predates them.
        monkeypatch.setattr(main_module, "_SETTINGS_REFRESH_WAIT_SECONDS", 0.5, raising=False)
        monkeypatch.setattr(main_module, "OLLAMA_BOOT", dict(main_module.OLLAMA_BOOT))
        monkeypatch.setattr(main_module, "_OLLAMA_AUTOSTART", None, raising=False)
        monkeypatch.setattr(main_module, "_OLLAMA_STARTUP_THREAD", None, raising=False)
        # A stopped Ollama that fails fast on every OS, unless a test says otherwise.
        self.stopped_ollama = ClosingListener()
        self.use_mode(ProviderMode.AUTO)
        self.use_ollama(self.stopped_ollama.url)
        # The Brain and the UI must key the shared snapshot identically.
        brain.preferred_cloud = main_module.current_preferred_cloud()

    def close(self) -> None:
        self.stopped_ollama.close()

    def use_mode(self, mode: ProviderMode) -> None:
        user_settings._cache["provider_mode"] = mode.value
        self.main.brain.provider_mode = mode.value

    def use_ollama(self, url: str, *, local_enabled: bool = True) -> None:
        brain = self.main.brain
        brain.ollama_url = url + "/api/chat"
        brain.ollama_model = MODEL
        brain.local_enabled = local_enabled

    def configure_cloud(self, *names: str, via: str | None = None) -> None:
        """Fake keys for the named variables; ``via`` routes every HTTPS request."""
        for name in names:
            self.monkeypatch.setenv(name, _FAKE_KEYS[name])
        if via:
            self.monkeypatch.setenv("HTTPS_PROXY", via)


@pytest.fixture
def bridge(main_module, monkeypatch, tmp_path):
    state = Bridge(main_module, monkeypatch, tmp_path)
    yield state
    state.close()


def _suite_conftest():
    """The tests/conftest.py module pytest already loaded.

    Never ``import tests.conftest``: that would execute it a second time, and
    its isolation guard rightly refuses to run once core.app_paths exists.
    """
    target = (ROOT / "tests" / "conftest.py").resolve()
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if path and Path(path).resolve() == target:
            return module
    raise AssertionError("tests/conftest.py is not loaded")


def dispatch(*calls, gap: float = 0.03, timeout: float = 60.0):
    """Send ``(name, args)`` calls ``gap`` apart through eel's own dispatcher.

    Returns ``[(latency_seconds, value), ...]``, each latency measured from
    when the client SENT that call -- a blocked hub also delays the spawn of
    everything queued behind it, so timing from the spawn would hide a freeze.
    """
    import eel
    import gevent

    ws = _RecordingSocket()
    started = time.perf_counter()
    greenlets = []
    for index, (name, args) in enumerate(calls):
        if index:
            gevent.sleep(gap)
        message = {"call": index + 1, "name": name, "args": list(args)}
        greenlets.append(gevent.spawn(eel._process_message, message, ws))
    gevent.joinall(greenlets, timeout=timeout)
    out = []
    for index, (name, _args) in enumerate(calls):
        assert index + 1 in ws.replies, f"{name} never answered"
        at, payload = ws.replies[index + 1]
        assert payload["status"] == "ok", f"{name}: {payload.get('error')}"
        out.append((at - (started + index * gap), payload["value"]))
    return out


def _wait_until(predicate, timeout: float = 8.0, message: str = "condition never became true"):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, message
        time.sleep(0.01)


# ===========================================================================
#  1. Choosing a local model
# ===========================================================================

def test_choosing_a_local_model_never_freezes_the_bridge_while_ollama_hangs(
        bridge, main_module, hung_endpoint):
    """The measured defect: 2.0 s of set_local_model, and everything waited."""
    bridge.use_ollama(hung_endpoint.url)
    (chosen_s, chosen), (cheap_s, _) = dispatch(("set_local_model", [MODEL]),
                                                ("get_voice_diagnostics", []))
    assert cheap_s < CHEAP_LIMIT, (
        f"get_voice_diagnostics waited {cheap_s:.2f}s behind set_local_model")
    # The selection itself still waited for a verified answer -- off the hub --
    # and refused, because an Ollama that does not answer verifies nothing.
    assert chosen["ok"] is False
    assert chosen["error"] in {"ollama_unavailable", "ollama_status_unknown"}
    assert main_module.brain.ollama_model == MODEL
    assert "local_model" not in user_settings.all_settings()
    assert chosen_s < main_module._LOCAL_MODEL_CHECK_SECONDS + 2


def test_a_local_model_is_validated_against_one_fresh_inventory(bridge, main_module, monkeypatch):
    """Validation is not weakened, the choice applies at once, and the shared
    measurement is reused instead of asking Ollama again."""
    server = FakeOllama(models=(MODEL, "llama3.2:3b"))
    try:
        bridge.use_ollama(server.url)
        main_module.brain.ollama_model = "llama3.2:3b"
        invalidations = []
        real_invalidate = bridge.cache.invalidate
        monkeypatch.setattr(bridge.cache, "invalidate",
                            lambda key=None: invalidations.append(key) or real_invalidate(key))

        (_s, refused), = dispatch(("set_local_model", ["not-installed:1b"]))
        assert refused["ok"] is False and refused["error"] == "model_not_installed"
        assert "llama3.2:3b" in refused["detail"]
        assert main_module.brain.ollama_model == "llama3.2:3b"
        assert "local_model" not in user_settings.all_settings()

        (_s, chosen), = dispatch(("set_local_model", [MODEL]))
        assert chosen["ok"] is True and chosen["model"] == MODEL
        # Applied to the running Brain NOW, persisted, and the snapshot that
        # named the previous model is gone.
        assert main_module.brain.ollama_model == MODEL
        assert user_settings.all_settings()["local_model"] == MODEL
        assert main_module.CONFIG["local"]["model"] == MODEL
        assert invalidations == [None], "the snapshot naming the old model was not dropped"
        assert chosen["providers"]["ollama"]["state"] == ProviderState.READY.value
        assert chosen["providers"]["ollama"]["model"] == MODEL
        # ONE inventory for both choices: the second reused the fresh one.
        assert server.tags_requests == 1, f"{server.tags_requests} requests to /api/tags"
    finally:
        server.close()


def test_a_local_model_nobody_could_verify_is_never_accepted(bridge, main_module):
    def broken(_url):
        raise RuntimeError("synthetic probe failure")

    bridge.monitor._prober = broken
    (_s, result), = dispatch(("set_local_model", [MODEL]))
    assert result["ok"] is False and result["error"] == "ollama_status_unknown"
    assert "local_model" not in user_settings.all_settings()


def test_two_quick_choices_keep_the_latest_one(bridge, main_module):
    """Validation happens off the hub, so the second choice can finish first;
    the older one must not overwrite it when it lands."""
    server = FakeOllama(models=(MODEL, "llama3.2:3b"))
    gate = threading.Event()
    try:
        bridge.use_ollama(server.url)
        bridge.monitor._prober = lambda url: gate.wait(5) and ollama_service.probe(url)
        threading.Timer(0.3, gate.set).start()
        first, second = dispatch(("set_local_model", ["llama3.2:3b"]),
                                 ("set_local_model", [MODEL]))
        assert second[1]["ok"] is True
        assert first[1]["ok"] is False and first[1]["error"] == "superseded"
        assert main_module.brain.ollama_model == MODEL
        assert user_settings.all_settings()["local_model"] == MODEL
    finally:
        gate.set()
        server.close()


# ===========================================================================
#  2. Provider status: no bridge call waits on a cloud provider
# ===========================================================================

POLLS_AND_SETTINGS = [
    ("get_providers", []),
    ("get_settings", []),
    ("get_system_readiness", []),
    ("get_command_center_state", []),
    ("get_provider_health", []),
    ("get_health_status", []),
    ("get_local_model_status", []),
    ("set_provider_mode", ["AUTO"]),
    ("set_provider_mode", ["CLOUD"]),
    ("set_preferred_cloud_provider", ["groq"]),
    ("remove_cloud_api_key", ["mistral"]),
    ("update_setting", ["groq_fast_model", "openai/gpt-oss-20b"]),
]


@pytest.mark.parametrize("name,args", POLLS_AND_SETTINGS,
                         ids=[f"{n}{'-' + a[0] if a else ''}" for n, a in POLLS_AND_SETTINGS])
def test_no_status_call_waits_on_a_hung_cloud_provider(bridge, main_module, hung_endpoint,
                                                       name, args):
    """First snapshot, configured provider that never answers: nothing waits."""
    bridge.configure_cloud("GROQ_API_KEY", via=hung_endpoint.url)
    (slow_s, value), (cheap_s, _) = dispatch((name, args), ("get_voice_diagnostics", []))
    assert cheap_s < CHEAP_LIMIT, (
        f"get_voice_diagnostics waited {cheap_s:.2f}s behind {name}: the hub waited on Groq")
    # The call itself answers too: a settings change waits for its refresh at
    # most _SETTINGS_REFRESH_WAIT_SECONDS, cooperatively; a poll not at all.
    assert slow_s < main_module._SETTINGS_REFRESH_WAIT_SECONDS + 1.0
    payload = (value if name == "get_providers" else
               value.get("providers") if isinstance(value, dict) else None)
    if isinstance(payload, dict) and "groq" in payload and isinstance(payload["groq"], dict):
        # Honest while the probe is out: not measured, and not decided around.
        assert payload["groq"]["state"] in {ProviderState.UNKNOWN.value,
                                            ProviderState.DISABLED.value}
        assert payload["groq"]["secret"]["configured"] is True
        if payload["groq"]["state"] == ProviderState.UNKNOWN.value:
            assert payload["route"] is None and payload["routePending"] is True


def test_choosing_a_cloud_model_waits_for_the_account_off_the_hub(bridge, main_module,
                                                                 monkeypatch):
    """A validation the user is waiting on still waits -- just not on the hub."""
    gate = threading.Event()
    asked = []

    def slow_choices(provider_id):
        asked.append(provider_id)
        gate.wait(5)
        return ["model-a", "model-b"], None

    monkeypatch.setattr(providers, "cloud_model_choices", slow_choices)
    threading.Timer(1.0, gate.set).start()
    (chosen_s, chosen), (cheap_s, _) = dispatch(("set_cloud_model", ["mistral", "model-b"]),
                                                ("get_voice_diagnostics", []))
    assert cheap_s < CHEAP_LIMIT, f"the hub waited {cheap_s:.2f}s for the account"
    assert chosen_s >= 0.9, "the choice did not wait for its validation"
    assert chosen["ok"] is True and chosen["model"] == "model-b"
    assert main_module.brain.mistral_fast_model == "model-b"
    assert asked == ["mistral"]


def test_test_connection_waits_for_the_provider_off_the_hub(bridge, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(providers, "test_mistral",
                        lambda key=None: gate.wait(5) and {"ok": True, "detail": "ok", "models": []})
    threading.Timer(1.0, gate.set).start()
    (tested_s, tested), (cheap_s, _) = dispatch(("test_cloud_connection", ["mistral"]),
                                                ("get_voice_diagnostics", []))
    assert cheap_s < CHEAP_LIMIT
    assert tested_s >= 0.9 and tested["ok"] is True


# ===========================================================================
#  3. Single flight: simultaneous cold readers share one probe set
# ===========================================================================

def test_simultaneous_cold_readers_share_one_probe_set(bridge, main_module, monkeypatch):
    """Pollers, a settings page, the warm-up and the Brain, all at once, cold."""
    bridge.configure_cloud("GROQ_API_KEY", "MISTRAL_API_KEY", "GEMINI_API_KEY")
    gate = threading.Event()
    calls: list[str] = []
    lock = threading.Lock()

    def describer(provider_id):
        def _describe(fast="", strong=""):
            with lock:
                calls.append(provider_id)
            gate.wait(5)
            return {"id": provider_id, "name": provider_id, "kind": "cloud", "role": "cloud",
                    "state": ProviderState.READY.value, "model": fast or "m", "models": [fast or "m"],
                    "secret": {"configured": True, "masked": "", "source": "environment",
                               "encrypted": False},
                    "tiers": {"fast": fast or "m", "complex": strong or fast or "m"},
                    "detail": "ok"}
        return _describe

    for pid, attr in (("groq", "describe_groq"), ("mistral", "describe_mistral"),
                      ("google", "describe_google")):
        monkeypatch.setattr(providers, attr, describer(pid))

    # A thread that is allowed to block (startup's warm-up) and the Brain, both
    # already waiting when the UI's pollers arrive.
    warm_up = threading.Thread(target=main_module.describe_providers, daemon=True)
    router = threading.Thread(target=lambda: asyncio.run(main_module.brain.route_for_async("olá")),
                              daemon=True)
    warm_up.start()
    router.start()
    time.sleep(0.1)
    threading.Timer(0.5, gate.set).start()
    results = dispatch(*([("get_providers", [])] * 5 + [("get_settings", [])] * 2),
                       gap=0.01)
    warm_up.join(10)
    router.join(10)
    # Both blocking readers returned, so the refresh they shared has finished.
    assert all(latency < CHEAP_LIMIT for latency, _ in results), [round(l, 2) for l, _ in results]
    assert sorted(calls) == ["google", "groq", "mistral"], f"probes performed: {calls}"
    assert bridge.cache.refresh_count == 1


def test_every_access_pattern_shares_one_refresh():
    cache = provider_status.ProviderStatusCache(ttl_seconds=60.0)
    gate = threading.Event()
    runs = []

    def produce():
        runs.append(1)
        gate.wait(5)
        return {"state": "READY"}

    fresh_results = []
    waiters = [threading.Thread(target=lambda: fresh_results.append(cache.get_fresh("k", produce)))
               for _ in range(3)]
    for _ in range(10):
        started = time.perf_counter()
        assert cache.get_stale_ok("k", produce) is None      # cold: nothing to serve yet
        assert time.perf_counter() - started < 0.2, "a cold poll waited for the producer"
    for waiter in waiters:
        waiter.start()

    async def two_async():
        return await asyncio.gather(cache.get_async("k", produce), cache.get_async("k", produce))

    async_results: list = []
    runner = threading.Thread(target=lambda: async_results.extend(asyncio.run(two_async())))
    runner.start()
    assert cache.refresh("k", produce) is not None
    time.sleep(0.1)
    gate.set()
    for waiter in waiters:
        waiter.join(5)
    runner.join(5)
    assert fresh_results == [{"state": "READY"}] * 3
    assert async_results == [{"state": "READY"}] * 2
    assert len(runs) == 1, f"{len(runs)} producer runs for one question"
    assert cache.get_stale_ok("k", produce) == {"state": "READY"}


def test_a_refresh_that_started_before_an_invalidation_is_not_cached():
    """A key removed while a probe was out must not come back as READY."""
    cache = provider_status.ProviderStatusCache(ttl_seconds=60.0)
    gate = threading.Event()
    answers = iter([{"state": "READY", "key": "old"}, {"state": "SETUP_REQUIRED"}])

    def produce():
        gate.wait(5)
        return next(answers)

    assert cache.get_stale_ok("k", produce) is None
    cache.invalidate()                      # the user removed the key meanwhile
    gate.set()
    _wait_until(lambda: not cache._refreshes)
    assert "k" not in cache._entries, "a pre-invalidation snapshot was stored as current"
    assert cache.get_fresh("k", produce) == {"state": "SETUP_REQUIRED"}


# ===========================================================================
#  4/5. Privacy and scope: LOCAL asks no cloud, local-off asks no Ollama
# ===========================================================================

def _exercise_everything(main_module):
    for name, args in (("get_providers", []), ("get_settings", []),
                       ("get_system_readiness", []), ("get_command_center_state", []),
                       ("get_provider_health", []), ("get_health_status", []),
                       ("get_local_model_status", []), ("get_voice_diagnostics", [])):
        dispatch((name, args))
    asyncio.run(main_module.brain.route_for_async("olá"))
    time.sleep(0.3)                           # let any background refresh start


def test_local_mode_sends_nothing_to_any_cloud_provider(bridge, main_module, hung_endpoint):
    bridge.configure_cloud(*_FAKE_KEYS, via=hung_endpoint.url)
    bridge.use_mode(ProviderMode.LOCAL)
    _exercise_everything(main_module)
    dispatch(("set_provider_mode", ["LOCAL"]), ("set_preferred_cloud_provider", ["mistral"]))
    time.sleep(0.3)
    assert hung_endpoint.connections == 0, "LOCAL mode opened a connection towards a cloud provider"
    shown = main_module.describe_providers(stale_ok=True)
    for pid in providers.CLOUD_PROVIDER_IDS:
        assert shown[pid]["state"] == ProviderState.DISABLED.value

    # Positive control: the same harness DOES see AUTO asking the providers,
    # so the zero above is a measurement and not a blind spot.
    bridge.use_mode(ProviderMode.AUTO)
    dispatch(("get_providers", []))
    _wait_until(lambda: hung_endpoint.connections >= 1, message="AUTO never probed a provider")


def test_local_models_off_send_nothing_to_ollama(bridge, main_module, monkeypatch):
    """With local models off, no status, readiness, provider, routing, model
    choice or startup path asks Ollama anything.

    Judged by probes STARTED -- counted the moment a probe is claimed, before
    any network I/O -- and by what this test's Ollama received. It used to be
    judged by probe_count, which counts probes that have FINISHED, and that is
    what made it fail on Ubuntu only: a snapshot refresh left over from the
    previous test, released when that test's hung provider closed, started a
    probe for the previous test's Ollama on this test's monitor. Linux refuses
    a closed loopback port at once, so that probe finished before the
    assertion; Windows takes ~2 s, so it finished just after. The leftover
    probe was a real defect -- a refresh acting on settings that no longer
    held -- and is gone (see the two tests below); counting starts is what
    keeps the refusal speed of the OS out of the verdict.
    """
    server = FakeOllama()
    started_urls: list[str] = []
    bridge.monitor._prober = lambda url: started_urls.append(url) or ollama_service.probe(url)
    try:
        bridge.use_ollama(server.url, local_enabled=False)
        _exercise_everything(main_module)
        (_s, chosen), = dispatch(("set_local_model", [MODEL]))
        assert chosen["ok"] is False and chosen["error"] == "local_disabled"
        assert main_module._start_ollama_in_background() is None
        assert main_module.get_system_readiness()["model"]["state"] == OllamaState.DISABLED
        assert bridge.monitor.probes_started == 0, (
            f"Ollama probes started while local models are off: {started_urls}")
        assert server.tags_requests == 0, f"{server.tags_requests} requests to a disabled Ollama"
    finally:
        server.close()


def _held_groq(gate: threading.Event):
    """A Groq describer that answers READY only once ``gate`` opens."""
    def describe_groq(fast="", strong=""):
        assert gate.wait(10), "the test never released the held provider"
        return {"id": "groq", "name": "Groq", "kind": "cloud", "role": "primary",
                "state": ProviderState.READY.value, "model": fast, "models": [fast],
                "secret": {"configured": True, "masked": "", "source": "environment",
                           "encrypted": False},
                "tiers": {"fast": fast, "complex": strong or fast}, "detail": "ok"}
    return describe_groq


def _settle_monitor(monitor: StatusMonitor) -> None:
    """Until no probe is in flight on ``monitor`` -- so a probe that WAS started
    has also reached the network and the fake server before anything is judged."""
    _wait_until(lambda: not monitor._inflight, message="an Ollama probe never finished")


def test_a_refresh_that_outlives_its_settings_starts_no_ollama_probe(bridge, main_module,
                                                                    monkeypatch):
    """The Ubuntu-only CI failure, replayed deterministically.

    A snapshot refresh captures the mode, the Ollama URL and the local-models
    flag when it starts, and may finish long afterwards behind a cloud provider
    that does not answer. It then described Ollama through whatever shared
    monitor was current, with what it had captured -- so a refresh begun under
    one configuration started a probe under another. In CI that was the
    previous test's refresh probing the previous test's Ollama on the next
    test's monitor. Here the refresh is held on purpose, everything it
    captured is replaced -- monitor, Ollama, the local-models flag -- and then
    it is let go.
    """
    earlier, current = FakeOllama(), FakeOllama()
    gate = threading.Event()
    bridge.configure_cloud("GROQ_API_KEY")
    monkeypatch.setattr(providers, "describe_groq", _held_groq(gate))
    try:
        bridge.use_ollama(earlier.url)                      # local models on
        dispatch(("get_providers", []))                     # refresh starts, held on Groq
        _settle_monitor(bridge.monitor)                     # the poll's own live read: done
        requests_to_earlier = earlier.tags_requests

        started: list[str] = []
        after = StatusMonitor(prober=lambda url: started.append(url) or ollama_service.probe(url))
        monkeypatch.setattr(ollama_service, "STATUS", after)
        bridge.use_ollama(current.url, local_enabled=False)

        gate.set()
        main_module.describe_providers()     # the blocking form JOINS the held refresh
        _settle_monitor(after)
        assert started == [], f"a finished refresh started Ollama probes: {started}"
        assert after.probes_started == 0 and after.probe_count == 0
        assert earlier.tags_requests == requests_to_earlier, "the old Ollama was asked again"
        assert current.tags_requests == 0
    finally:
        gate.set()
        earlier.close()
        current.close()


def test_switching_to_cloud_while_a_refresh_is_out_sends_nothing_to_ollama(bridge, main_module,
                                                                          monkeypatch):
    """The same defect as the user met it: a refresh begun in AUTO, still out
    behind a slow provider when the user switches to CLOUD, used to ask Ollama
    for its status after the switch -- in the mode that promises not to."""
    server = FakeOllama()
    gate = threading.Event()
    bridge.configure_cloud("GROQ_API_KEY")
    monkeypatch.setattr(providers, "describe_groq", _held_groq(gate))
    try:
        bridge.use_ollama(server.url)
        dispatch(("get_providers", []))                     # AUTO refresh, held on Groq
        _settle_monitor(bridge.monitor)
        bridge.monitor.invalidate()          # nothing fresh left, as ~10 s later
        auto_key = provider_status.cache_key(
            ProviderMode.AUTO, main_module.brain.cloud_tiers(), main_module.brain.ollama_model,
            main_module.current_preferred_cloud())
        requests = server.tags_requests

        (_s, switched), = dispatch(("set_provider_mode", ["CLOUD"]))
        assert switched["ok"] is True and switched["mode"] == "CLOUD"
        gate.set()
        bridge.cache.get_fresh(auto_key, lambda: pytest.fail("the AUTO refresh was not in flight"))
        _settle_monitor(bridge.monitor)
        assert server.tags_requests == requests, "CLOUD mode sent Ollama a status request"
        # Exactly one probe in the whole test: the AUTO poll's own, before the switch.
        assert bridge.monitor.probes_started == 1
    finally:
        gate.set()
        server.close()


def test_cloud_mode_sends_nothing_to_ollama(bridge, main_module):
    server = FakeOllama()
    try:
        bridge.use_ollama(server.url)
        bridge.use_mode(ProviderMode.CLOUD)
        main_module.brain.provider_mode = "CLOUD"
        dispatch(("get_providers", []), ("get_settings", []))
        asyncio.run(main_module.brain.route_for_async("olá"))
        time.sleep(0.3)
        assert server.tags_requests == 0
        assert main_module.describe_providers(stale_ok=True)["ollama"]["state"] == \
            ProviderState.DISABLED.value
    finally:
        server.close()


# ===========================================================================
#  6. Unmeasured is never routed, and never decided around
# ===========================================================================

def _payload(state):
    return {"state": state, "model": "m", "tiers": {"fast": "m", "complex": "m"},
            "detail": state.lower()}


UNKNOWN, READY, DOWN = (ProviderState.UNKNOWN.value, ProviderState.READY.value,
                        ProviderState.UNAVAILABLE.value)


ROUTING_CASES = [
    (ProviderMode.LOCAL, DOWN, DOWN, UNKNOWN, True, None),
    (ProviderMode.CLOUD, UNKNOWN, READY, READY, True, None),
    (ProviderMode.AUTO, UNKNOWN, UNKNOWN, UNKNOWN, True, None),
    # The preferred provider is unmeasured, so "use Mistral" is not decided yet.
    (ProviderMode.AUTO, UNKNOWN, READY, READY, True, "mistral"),
    # The preferred one is READY: nothing unmeasured can change the answer.
    (ProviderMode.AUTO, READY, UNKNOWN, UNKNOWN, False, "groq"),
    (ProviderMode.AUTO, DOWN, DOWN, READY, False, "ollama"),
]


@pytest.mark.parametrize("mode,groq,mistral,ollama,_pending,routed_to", ROUTING_CASES)
def test_unknown_is_never_routed_to(mode, groq, mistral, ollama, _pending, routed_to):
    route = providers.resolve_route(mode, _payload(groq), _payload(ollama),
                                    mistral=_payload(mistral), preferred="groq")
    if route["usable"]:
        chosen = {"groq": groq, "mistral": mistral, "ollama": ollama}[route["provider"]]
        assert chosen == READY, f"routed to {route['provider']} in state {chosen}"
    if routed_to is not None:
        assert route["provider"] == routed_to


@pytest.mark.parametrize("mode,groq,mistral,ollama,pending,_routed_to", ROUTING_CASES)
def test_a_decision_taken_around_an_unmeasured_provider_is_pending(mode, groq, mistral, ollama,
                                                                  pending, _routed_to):
    assert providers.route_pending(mode, _payload(groq), _payload(ollama),
                                   mistral=_payload(mistral), preferred="groq") is pending


def test_a_placeholder_is_never_what_the_router_routes_on(bridge, main_module, monkeypatch):
    bridge.configure_cloud("GROQ_API_KEY")
    gate = threading.Event()
    monkeypatch.setattr(providers, "describe_groq", lambda fast="", strong="": gate.wait(5) and {
        "id": "groq", "name": "Groq", "kind": "cloud", "role": "primary",
        "state": ProviderState.READY.value, "model": fast, "models": [fast],
        "secret": {"configured": True, "masked": "", "source": "environment", "encrypted": False},
        "tiers": {"fast": fast, "complex": strong or fast}, "detail": "ok"})
    (shown_s, shown), = dispatch(("get_providers", []))
    assert shown_s < CHEAP_LIMIT, f"get_providers waited {shown_s:.2f}s for Groq"
    assert shown["groq"]["state"] == ProviderState.UNKNOWN.value
    assert shown["route"] is None and shown["routePending"] is True
    assert bridge.cache._entries == {}, "a placeholder was stored as a snapshot"
    threading.Timer(0.3, gate.set).start()
    route = asyncio.run(main_module.brain.route_for_async("olá"))
    assert route["provider"] == "groq" and route["usable"] is True, route["reason"]


# ===========================================================================
#  7. The measurement becomes visible by itself
# ===========================================================================

def test_the_background_measurement_becomes_visible_to_the_poll(bridge, main_module,
                                                               monkeypatch):
    bridge.configure_cloud("MISTRAL_API_KEY")
    gate = threading.Event()
    probes = []

    def describe_mistral(fast="", strong=""):
        probes.append(1)
        gate.wait(5)
        return {"id": "mistral", "name": "Mistral", "kind": "cloud", "role": "cloud",
                "state": ProviderState.READY.value, "model": "mistral-small",
                "models": ["mistral-small"], "records": [],
                "secret": {"configured": True, "masked": "", "source": "environment",
                           "encrypted": False},
                "tiers": {"fast": "mistral-small", "complex": "mistral-small"}, "detail": "ok"}

    monkeypatch.setattr(providers, "describe_mistral", describe_mistral)
    main_module.brain.preferred_cloud = "mistral"
    user_settings._cache["preferred_cloud"] = "mistral"
    (first_s, first), = dispatch(("get_providers", []))
    assert first_s < CHEAP_LIMIT
    assert first["mistral"]["state"] == ProviderState.UNKNOWN.value and first["route"] is None

    gate.set()
    deadline = time.monotonic() + 5
    while True:
        (_s, shown), = dispatch(("get_providers", []))
        if shown["mistral"]["state"] == ProviderState.READY.value:
            break
        assert time.monotonic() < deadline, "the measurement never became visible"
        time.sleep(0.05)
    assert shown["route"]["provider"] == "mistral" and shown["route"]["usable"] is True
    assert shown["routePending"] is False
    assert len(probes) == 1


# ===========================================================================
#  8. The suite never runs against the developer's real profile
# ===========================================================================

def test_the_suite_runs_against_an_isolated_profile(main_module):
    from core import app_paths, logger as nano_logger, memory as nano_memory

    suite = _suite_conftest()
    data_dir = Path(app_paths.DATA_DIR).resolve()
    assert data_dir != app_paths.default_data_root().resolve()
    assert data_dir == Path(os.environ["NANO_DATA_DIR"]).resolve() == suite.NANO_TEST_PROFILE
    # And every store core.main opened at import lives inside it.
    for path in (nano_memory.DB_PATH, main_module.task_engine.db_path,
                 main_module.permission_manager._policy_store_path, nano_logger.LOG_PATH,
                 secret_store._STORE_PATH, user_settings._PATH):
        assert data_dir in Path(path).resolve().parents, f"{path} is outside the test profile"


def test_the_conftest_knows_where_the_real_profile_is():
    """Its copy of default_data_root must not drift from the real one."""
    from core import app_paths

    assert _suite_conftest()._real_profile() == app_paths.default_data_root().resolve()


def test_report_the_profile_in_use():
    """Helper for the subprocess test below; does nothing in a normal run."""
    target = os.environ.get("NANO_ISOLATION_REPORT")
    if not target:
        pytest.skip("only meaningful inside test_a_bare_pytest_run_is_isolated")
    from core import app_paths

    Path(target).write_text(json.dumps({
        "data_dir": str(Path(app_paths.DATA_DIR).resolve()),
        "real_profile": str(app_paths.default_data_root().resolve()),
    }), encoding="utf-8")


@pytest.mark.parametrize("override", ["none", "real_profile", "chosen"])
def test_a_bare_pytest_run_is_isolated(tmp_path, override):
    """Run pytest the way a developer does, and ask it where Nano's data is.

    Only core.app_paths is imported in that run, so even a regression here
    could not write anything into the real profile.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("NANO_DATA_DIR", "HELIOS_DATA_DIR")}
    from core import app_paths

    real = app_paths.default_data_root().resolve()
    chosen = (tmp_path / "chosen").resolve()
    if override == "real_profile":
        env["NANO_DATA_DIR"] = str(real)
    elif override == "chosen":
        env["NANO_DATA_DIR"] = str(chosen)
    report = tmp_path / "report.json"
    env["NANO_ISOLATION_REPORT"] = str(report)
    # Default verbosity on purpose: -q would hide the header being checked.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
         "tests/test_bridge_responsiveness.py::test_report_the_profile_in_use"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    seen = json.loads(report.read_text(encoding="utf-8"))
    data_dir = Path(seen["data_dir"])
    assert data_dir != real, "a bare pytest run used the real Nano profile"
    if override == "chosen":
        assert data_dir == chosen, "an explicit scratch profile was not respected"
    else:
        assert (Path(tempfile.gettempdir()) / "nano-pytest-profiles").resolve() in data_dir.parents
    assert "nano data isolated in" in result.stdout


# ===========================================================================
#  9. Startup: the window never waits for Ollama, and the state stays honest
# ===========================================================================

class _FakeOllamaProcess:
    """ensure_running's view of Ollama: probe, executable, spawn."""

    def __init__(self, monkeypatch, *, running=False, installed=True, starts_after=None):
        self.up = threading.Event()
        if running:
            self.up.set()
        self.spawned = 0
        self.starts_after = starts_after
        monkeypatch.setattr(ollama_service, "api_available", self.api_available)
        monkeypatch.setattr(ollama_service, "find_executable",
                            lambda: r"C:\fake\ollama.exe" if installed else None)
        monkeypatch.setattr(ollama_service.subprocess, "Popen", self.popen)

    def api_available(self, _url, *, timeout=2.0):
        if self.up.is_set():
            return True
        time.sleep(min(0.05, timeout))      # a failed probe is never instant
        return False

    def popen(self, *_a, **_k):
        self.spawned += 1
        if self.starts_after is not None:
            threading.Timer(self.starts_after, self.up.set).start()
        return object()

    def prober(self, _url):
        if self.up.is_set():
            return {"reachable": True, "installed": [MODEL], "executable": None}
        return {"reachable": False, "installed": [], "executable": True}


@pytest.fixture
def startup(bridge, main_module, monkeypatch):
    monkeypatch.setattr(main_module, "_report", lambda *a, **k: None)
    bridge.use_ollama("http://127.0.0.1:11434")
    return bridge


def test_the_window_does_not_wait_for_an_ollama_that_must_be_started(startup, main_module,
                                                                    monkeypatch):
    process = _FakeOllamaProcess(monkeypatch, starts_after=1.5)
    startup.monitor._prober = process.prober
    main_module.CONFIG["local"]["autostart"] = True

    started = time.perf_counter()
    thread = main_module._start_ollama_in_background()
    assert time.perf_counter() - started < 0.2, "startup waited for Ollama"

    # While it starts: honest, measured by nobody else, and the bridge is free.
    (ready_s, readiness), (cheap_s, _) = dispatch(("get_system_readiness", []),
                                                  ("get_voice_diagnostics", []))
    assert ready_s < CHEAP_LIMIT and cheap_s < CHEAP_LIMIT
    assert readiness["model"]["state"] == OllamaState.UNKNOWN
    assert readiness["model"]["local"]["online"] is False
    assert "arrancar" in readiness["model"]["detail"]
    assert main_module.get_local_model_status()["boot"].get("pending") is True
    assert startup.monitor.probe_count == 0, "a reader probed underneath the launch"

    thread.join(10)
    assert not thread.is_alive()
    assert process.spawned == 1
    assert main_module.OLLAMA_BOOT["started"] is True and main_module.OLLAMA_BOOT["available"]
    assert main_module.probe_local_model()["state"] == OllamaState.READY
    assert startup.monitor.probe_count == 1      # the one inventory read


@pytest.mark.parametrize("case", ["running", "stopped", "missing", "disabled"])
def test_startup_cases_end_in_their_honest_state(startup, main_module, monkeypatch, case):
    process = _FakeOllamaProcess(monkeypatch, running=(case == "running"),
                                 installed=(case != "missing"))
    startup.monitor._prober = process.prober
    main_module.CONFIG["local"]["autostart"] = False
    if case == "disabled":
        main_module.brain.local_enabled = False

    thread = main_module._start_ollama_in_background()
    if thread is not None:
        thread.join(10)
    expected = {"running": OllamaState.READY, "stopped": OllamaState.OLLAMA_UNAVAILABLE,
                "missing": OllamaState.NOT_INSTALLED, "disabled": OllamaState.DISABLED}[case]
    assert main_module.probe_local_model()["state"] == expected
    assert process.spawned == 0
    assert main_module.OLLAMA_BOOT.get("reused") is (case == "running")
    assert not ollama_service.STATUS.starting(startup.main.brain.ollama_url.removesuffix("/api/chat"))


def test_the_router_waits_for_a_starting_ollama_only_when_it_needs_it(startup, main_module,
                                                                     monkeypatch, hung_endpoint):
    """LOCAL needs Ollama and waits for the launch; AUTO with a cloud provider
    ready does not wait for Ollama at all -- not even for a hung one."""
    base_url = main_module.brain.ollama_url.removesuffix("/api/chat")
    startup.monitor.begin_startup(base_url)
    startup.use_mode(ProviderMode.LOCAL)
    routes = []
    router = threading.Thread(
        target=lambda: routes.append(asyncio.run(main_module.brain.route_for_async("olá"))),
        daemon=True)
    router.start()
    time.sleep(0.3)
    assert router.is_alive(), "LOCAL routed before Ollama's startup had answered"
    startup.monitor.finish_startup(base_url, {"reachable": True, "installed": [MODEL],
                                              "executable": None})
    router.join(5)
    assert routes and routes[0]["provider"] == "ollama" and routes[0]["usable"] is True

    # AUTO, Groq ready, Ollama hung and never measured: no wait for Ollama.
    startup.use_ollama(hung_endpoint.url)
    startup.use_mode(ProviderMode.AUTO)
    startup.configure_cloud("GROQ_API_KEY")
    monkeypatch.setattr(providers, "describe_groq", lambda fast="", strong="": {
        "id": "groq", "name": "Groq", "kind": "cloud", "role": "primary",
        "state": ProviderState.READY.value, "model": fast, "models": [fast],
        "secret": {"configured": True, "masked": "", "source": "environment", "encrypted": False},
        "tiers": {"fast": fast, "complex": strong or fast}, "detail": "ok"})
    started = time.perf_counter()
    route = asyncio.run(main_module.brain.route_for_async("olá"))
    assert time.perf_counter() - started < 1.0, "AUTO waited for Ollama with Groq ready"
    assert route["provider"] == "groq" and route["usable"] is True


# ===========================================================================
#  10. Shutdown and cancellation leave nothing behind
# ===========================================================================

def test_a_cancelled_start_never_spawns_a_server(monkeypatch):
    process = _FakeOllamaProcess(monkeypatch)
    control = ollama_service.Autostart()
    control.cancel()
    result = ollama_service.ensure_running("http://127.0.0.1:11434", control=control)
    assert result.get("cancelled") is True and process.spawned == 0


def test_cancel_during_the_first_probe_means_no_spawn(monkeypatch):
    process = _FakeOllamaProcess(monkeypatch)
    probing, release = threading.Event(), threading.Event()

    def slow_probe(_url, *, timeout=2.0):
        probing.set()
        release.wait(5)
        return False

    monkeypatch.setattr(ollama_service, "api_available", slow_probe)
    control = ollama_service.Autostart()
    results = []
    worker = threading.Thread(target=lambda: results.append(
        ollama_service.ensure_running("http://127.0.0.1:11434", control=control)), daemon=True)
    worker.start()
    probing.wait(5)
    control.cancel()                         # shutdown() arrives mid-probe
    release.set()
    worker.join(5)
    assert results[0].get("cancelled") is True
    assert process.spawned == 0


def test_shutdown_ends_the_wait_for_the_port_at_once(startup, main_module, monkeypatch):
    """Spawned, never answering: shutdown must not wait out the 25 s window."""
    process = _FakeOllamaProcess(monkeypatch, starts_after=None)
    startup.monitor._prober = process.prober
    main_module.CONFIG["local"]["autostart"] = True
    thread = main_module._start_ollama_in_background()
    _wait_until(lambda: process.spawned == 1, message="the server was never spawned")
    assert thread.daemon, "the startup thread could keep a closing Nano alive"

    # The real shutdown(), with what it tears down stubbed out: this test is
    # about the order and the cancel, not about stopping the shared singletons.
    for owner, attr in ((main_module.background_worker, "stop"), (main_module.memory_stack, "drain"),
                        (main_module.memory_stack, "stop"), (main_module.voice, "shutdown")):
        monkeypatch.setattr(owner, attr, lambda *a, **k: None)
    monkeypatch.setattr(main_module, "stop_plugin_services", lambda: None)
    monkeypatch.setattr(main_module, "_release_single_instance", lambda: None)
    monkeypatch.setattr(main_module, "_EVENT_LOOP", None)
    monkeypatch.setattr(main_module, "_DESKTOP_BRIDGE", None)
    started = time.perf_counter()
    main_module.shutdown()
    thread.join(2)
    assert not thread.is_alive(), "the start kept polling after shutdown"
    assert time.perf_counter() - started < 2.0
    assert main_module.OLLAMA_BOOT.get("cancelled") is True
    assert process.spawned == 1, "a second server was spawned"
    # Nobody is left reading "a arrancar", and nothing false was recorded.
    assert not startup.monitor.starting("http://127.0.0.1:11434")
    assert main_module.probe_local_model()["state"] != OllamaState.READY


def test_every_status_worker_is_a_daemon_and_none_spawns_a_process(bridge, main_module,
                                                                  monkeypatch, hung_endpoint):
    """Every thread these paths start, caught while it is still working.

    The property is "nothing the interpreter will wait for at exit", judged on
    EVERY new thread rather than on a naming convention. Two kinds qualify: a
    non-daemon thread, and a concurrent.futures worker -- joined at exit
    WHATEVER its daemon flag (measured: a daemon-flagged executor worker in a
    3 s call held the exit for 3.15 s; a plain daemon thread, 0.34 s). The
    provider snapshot used such an executor, so an exit with a probe stuck on
    a provider that does not answer waited out that provider's 10 s timeout.
    """
    from concurrent.futures import thread as futures_thread

    def no_process(*_a, **_k):
        raise AssertionError("a status path started a process")

    monkeypatch.setattr(subprocess, "Popen", no_process)
    before = set(threading.enumerate())
    bridge.use_ollama(hung_endpoint.url)
    bridge.configure_cloud("GROQ_API_KEY", via=hung_endpoint.url)
    dispatch(("get_system_readiness", []))            # the Ollama status probe
    # A snapshot refresh stuck on Groq, from a thread allowed to block (the
    # warm-up's form), and a local-model choice waiting on a hung Ollama.
    refresher = threading.Thread(target=main_module.describe_providers, daemon=True)
    chooser = threading.Thread(target=lambda: dispatch(("set_local_model", [MODEL])), daemon=True)
    refresher.start()
    chooser.start()
    time.sleep(0.5)
    started_here = [t for t in threading.enumerate() if t not in before and t.is_alive()]
    joined_at_exit = set(getattr(futures_thread, "_threads_queues", {}).keys())
    offenders = sorted(t.name for t in started_here if not t.daemon or t in joined_at_exit)
    assert offenders == [], f"workers the interpreter would wait for at exit: {offenders}"
    names = {t.name for t in started_here}
    assert any(n.startswith("nano-ollama") for n in names), names
    assert any(n.startswith("nano-provider") for n in names), names
    # Leave nothing of THIS test running into the next one: release what the
    # workers are stuck on, then wait for the threads the test itself started.
    # `refresher` finishes by reading Ollama's status through whatever shared
    # monitor is current, so it must not outlive this test's.
    hung_endpoint.close()
    chooser.join(15)
    refresher.join(15)
    assert not chooser.is_alive() and not refresher.is_alive()


# ===========================================================================
#  The periodic readiness blip, and the settings path's microphone lock
# ===========================================================================

def test_an_expired_device_list_is_refreshed_off_the_bridge(bridge, main_module, monkeypatch,
                                                           fake_pyaudio):
    """The 35-40 ms readiness call: PortAudio re-enumerated inline every 30 s."""
    from core import voice as voice_mod

    provider_cls = voice_mod.AudioInputProvider
    monkeypatch.setattr(main_module.voice.input_provider, "_available", True)
    monkeypatch.setattr(provider_cls, "_device_cache",
                        [{"index": 0, "name": "Old Mic", "maxInputChannels": 1}])
    monkeypatch.setattr(provider_cls, "_device_cache_at", time.monotonic() - 3600)
    monkeypatch.setattr(provider_cls, "_device_refreshing", False, raising=False)
    monkeypatch.setattr(provider_cls, "_device_refresh_tried_at", float("-inf"), raising=False)
    assert not voice_mod._MIC_STREAM_OPEN.is_set(), "a capture stream is open; nothing to measure"
    fake_pyaudio.delay = 0.4

    results = dispatch(("get_system_readiness", []), ("get_command_center_state", []),
                       ("get_voice_diagnostics", []))
    assert all(latency < 0.3 for latency, _ in results), [round(l, 3) for l, _ in results]
    _wait_until(lambda: provider_cls._device_cache[0]["name"] == "Fake Mic",
                message="the refreshed device list never arrived")
    assert fake_pyaudio.constructed == 1, "more than one enumeration for one expiry"


def test_settings_never_waits_for_the_microphone(bridge, main_module, fake_pyaudio):
    """A one-shot capture holds the PortAudio lock for the whole recording."""
    from core import voice as voice_mod

    held, release = threading.Event(), threading.Event()

    def capture():
        with voice_mod._PORTAUDIO_LOCK:
            held.set()
            release.wait(5)

    threading.Thread(target=capture, daemon=True).start()
    held.wait(5)
    try:
        (settings_s, settings), (cheap_s, _) = dispatch(("get_settings", []),
                                                        ("get_voice_diagnostics", []))
    finally:
        release.set()
    assert settings_s < CHEAP_LIMIT and cheap_s < CHEAP_LIMIT, (settings_s, cheap_s)
    assert settings["devices"]["outputs"] == []


# ===========================================================================
#  Voice diagnostics: proven to read memory only (no change was needed)
# ===========================================================================

def test_voice_diagnostics_touches_no_network_process_database_or_device(bridge, main_module,
                                                                        monkeypatch):
    """The audit's proof, kept as a guard: passes against the code as it was.

    core/voice_diagnostics.py -- the CLI report with its own async Ollama
    probe -- is not imported by core.main at all; the bridge function reads
    the wake engine's in-memory counters and the voice turn's state.
    """
    import sqlite3

    def forbidden(*_a, **_k):
        raise AssertionError("get_voice_diagnostics reached outside memory")

    from core import voice as voice_mod

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(voice_mod.AudioInputProvider, "_enumerate_devices", classmethod(
        lambda cls: forbidden()), raising=False)
    monkeypatch.setattr(voice_mod.AudioInputProvider, "list_devices", forbidden)
    (latency, value), = dispatch(("get_voice_diagnostics", []))
    assert latency < CHEAP_LIMIT
    assert {"state", "audio", "voiceTurn", "counters"} <= set(value)
