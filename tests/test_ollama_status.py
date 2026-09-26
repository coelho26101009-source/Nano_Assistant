"""A stopped, slow or absent Ollama must never freeze the eel bridge.

THE DEFECT THESE TESTS GUARD, AS IT WAS MEASURED.
eel serves every bridge call from ONE cooperative gevent hub, and nothing in Nano
is monkey-patched, so a blocking socket call inside any exposed function stops
every other exposed function until it returns. Readiness (polled every 10 s)
asked Ollama twice in a row on that hub, the command center (every 4 s) asked
again, and a provider-snapshot miss asked once more. With Ollama stopped each ask
is a ~2 s refused connection on Windows: readiness took 3.9 s, and a cheap call
sent 30 ms after it waited 3.87 s behind it.

HOW THEY TEST IT.
Behaviourally, on every OS. A "hung" Ollama -- a socket that accepts the TCP
connection and never answers -- is the slow case everywhere (a refused
connection is only slow on Windows). The real exposed functions are dispatched
through eel's own ``_process_message`` on the gevent hub, exactly as eel does
for a websocket message, and the reply times are measured from when the client
would have sent the call. A counting fake Ollama answers /api/tags so the number
of requests Nano really makes can be asserted.

Every test swaps in its own ``StatusMonitor`` and provider cache, so a probe
started by another test can never be counted here.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import ollama_service, provider_status, providers
from core.ollama_service import OllamaState, StatusMonitor
from core.providers import ProviderMode, ProviderState

MODEL = "qwen3:8b"


# ---------------------------------------------------------------------------
#  Local stand-ins for Ollama
# ---------------------------------------------------------------------------

class _TagsHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        server = self.server
        with server.lock:
            server.requests.append(self.path)
        if self.path.rstrip("/") != "/api/tags":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"models": [{"name": name} for name in server.models]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class FakeOllama:
    """A running Ollama that counts every /api/tags request it receives."""

    def __init__(self, models=(MODEL,)):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _TagsHandler)
        self.server.daemon_threads = True
        self.server.lock = threading.Lock()
        self.server.requests = []
        self.server.models = list(models)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def tags_requests(self) -> int:
        with self.server.lock:
            return sum(1 for path in self.server.requests if path.rstrip("/") == "/api/tags")

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ClosingListener:
    """An Ollama that is not answering: every connection is accepted and dropped.

    Counts connection attempts, which a closed port cannot do, and fails fast on
    every OS so the test measures Nano rather than the TCP stack.
    """

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(32)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        self.connections = 0
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            conn.close()

    def close(self):
        self._stop = True
        self.sock.close()


@pytest.fixture
def fake_ollama():
    server = FakeOllama()
    yield server
    server.close()


@pytest.fixture
def hung_ollama():
    """Accepts the TCP connection and never answers -- slow on every OS."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    sock.close()


def _settle(monitor: StatusMonitor, probes: int, timeout: float = 8.0) -> None:
    """Wait until ``probes`` probes have completed and none is in flight."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with monitor._lock:
            idle = not monitor._inflight
        if monitor.probe_count >= probes and idle:
            return
        time.sleep(0.01)
    raise AssertionError(f"monitor did not settle: {monitor.probe_count} probe(s)")


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _reachable(*models):
    return {"reachable": True, "installed": list(models), "executable": None}


UNREACHABLE = {"reachable": False, "installed": [], "executable": True}


# ---------------------------------------------------------------------------
#  The monitor: cache, single flight, honesty
# ---------------------------------------------------------------------------

def test_read_answers_at_once_while_a_probe_is_in_flight_and_says_unknown():
    release = threading.Event()

    def slow_probe(_url):
        release.wait(5)
        return _reachable(MODEL)

    monitor = StatusMonitor(prober=slow_probe)
    started = time.perf_counter()
    status = monitor.read(MODEL, "http://ollama.test")
    elapsed = time.perf_counter() - started
    try:
        assert elapsed < 0.2, f"read() waited {elapsed:.2f}s for the network"
        # Honest while nothing is measured: UNKNOWN, and never up or ready.
        assert status["state"] == OllamaState.UNKNOWN
        assert status["ollamaUp"] is False and status["modelReady"] is False
        assert status["ageSeconds"] is None
        assert status["detail"]
    finally:
        release.set()
    _settle(monitor, 1)
    assert monitor.read(MODEL, "http://ollama.test")["state"] == OllamaState.READY


def test_every_reader_and_waiter_shares_one_probe():
    release = threading.Event()
    calls = []

    def slow_probe(url):
        calls.append(url)
        release.wait(5)
        return _reachable(MODEL)

    monitor = StatusMonitor(prober=slow_probe)
    results = []
    waiters = [threading.Thread(target=lambda: results.append(
        monitor.measure(MODEL, "http://ollama.test")["state"])) for _ in range(5)]
    for _ in range(10):
        monitor.read(MODEL, "http://ollama.test")
    monitor.read(MODEL, "http://ollama.test/")          # same server, other spelling
    for waiter in waiters:
        waiter.start()
    time.sleep(0.1)
    release.set()
    for waiter in waiters:
        waiter.join(5)
    assert results == [OllamaState.READY] * 5
    assert len(calls) == 1, f"{len(calls)} probes for one question"


def test_a_fresh_measurement_is_reused_and_a_stale_one_refreshed_in_background():
    clock = _Clock()
    answers = iter([_reachable(MODEL), UNREACHABLE])
    monitor = StatusMonitor(prober=lambda _url: next(answers), clock=clock)

    assert monitor.measure(MODEL, "http://ollama.test")["state"] == OllamaState.READY
    assert monitor.probe_count == 1

    clock.now += monitor.ttl_seconds - 1                 # still fresh
    assert monitor.read(MODEL, "http://ollama.test")["state"] == OllamaState.READY
    assert monitor.probe_count == 1, "a fresh measurement was probed again"

    clock.now += 2                                       # stale, still recent
    stale = monitor.read(MODEL, "http://ollama.test")
    # The last real measurement is still reported while a new one is taken --
    # not UNKNOWN, which would make every refresh flicker.
    assert stale["state"] == OllamaState.READY
    assert stale["ageSeconds"] == pytest.approx(monitor.ttl_seconds + 1)
    _settle(monitor, 2)
    assert monitor.read(MODEL, "http://ollama.test")["state"] == OllamaState.OLLAMA_UNAVAILABLE


def test_a_measurement_too_old_to_trust_is_reported_as_unknown_not_as_it_was():
    clock = _Clock()
    release = threading.Event()
    answers = iter([_reachable(MODEL)])

    def prober(_url):
        try:
            return next(answers)
        except StopIteration:
            release.wait(5)
            return UNREACHABLE

    monitor = StatusMonitor(prober=prober, clock=clock)
    assert monitor.measure(MODEL, "http://ollama.test")["state"] == OllamaState.READY
    clock.now += monitor.max_age_seconds + 1
    try:
        status = monitor.read(MODEL, "http://ollama.test")
        assert status["state"] == OllamaState.UNKNOWN, "an hour-old READY is not a status"
        assert status["modelReady"] is False
    finally:
        release.set()


def test_ollama_starting_and_stopping_both_become_visible():
    clock = _Clock()
    answers = iter([UNREACHABLE, _reachable(MODEL), UNREACHABLE])
    monitor = StatusMonitor(prober=lambda _url: next(answers), clock=clock)

    seen = [monitor.measure(MODEL, "http://ollama.test")["state"]]
    for expected_probes in (2, 3):
        clock.now += monitor.ttl_seconds + 1
        monitor.read(MODEL, "http://ollama.test")        # stale: refresh starts
        _settle(monitor, expected_probes)
        seen.append(monitor.read(MODEL, "http://ollama.test")["state"])
    assert seen == [OllamaState.OLLAMA_UNAVAILABLE, OllamaState.READY,
                    OllamaState.OLLAMA_UNAVAILABLE]


def test_local_models_disabled_never_probe():
    calls = []
    monitor = StatusMonitor(prober=lambda url: calls.append(url) or _reachable(MODEL))
    assert monitor.read(MODEL, "http://ollama.test", local_enabled=False)["state"] == OllamaState.DISABLED
    assert monitor.measure(MODEL, "http://ollama.test", local_enabled=False)["state"] == OllamaState.DISABLED
    time.sleep(0.05)
    assert calls == []


def test_a_probe_that_fails_outright_invents_nothing():
    def broken(_url):
        raise RuntimeError("synthetic failure")

    monitor = StatusMonitor(prober=broken)
    assert monitor.read(MODEL, "http://ollama.test")["state"] == OllamaState.UNKNOWN
    _settle(monitor, 1)
    after = monitor.measure(MODEL, "http://ollama.test")
    assert after["state"] == OllamaState.UNKNOWN
    assert after["ollamaUp"] is False and after["modelReady"] is False


def test_unknown_maps_to_an_unknown_provider_that_is_never_routed_to(monkeypatch):
    """No false healthy state reaches the router."""
    release = threading.Event()
    monitor = StatusMonitor(prober=lambda _url: release.wait(5) and _reachable(MODEL))
    monkeypatch.setattr(ollama_service, "STATUS", monitor)
    try:
        payload = providers.describe_ollama(MODEL, "http://never-measured.test", wait=False)
        assert payload["state"] == ProviderState.UNKNOWN.value
        route = providers.resolve_route(
            ProviderMode.LOCAL, {"state": ProviderState.SETUP_REQUIRED.value, "detail": ""}, payload)
        assert route["usable"] is False
        assert route["reason"] == payload["detail"]
    finally:
        release.set()


# ---------------------------------------------------------------------------
#  The probe itself: one request, same answers
# ---------------------------------------------------------------------------

def test_status_is_one_request_to_ollama_not_two(fake_ollama):
    ready = ollama_service.describe(MODEL, fake_ollama.url)
    assert ready["state"] == OllamaState.READY and ready["modelReady"] is True
    assert fake_ollama.tags_requests == 1, "describe() asked /api/tags more than once"

    missing = ollama_service.describe("other:1b", fake_ollama.url)
    assert missing["state"] == OllamaState.MODEL_UNAVAILABLE
    assert missing["installed"] == [MODEL]
    assert fake_ollama.tags_requests == 2


def test_building_the_model_router_asks_ollama_nothing_until_it_is_used(fake_ollama):
    """The Brain builds a ModelRouter at import time, i.e. on every startup.

    Its constructor used to discover Ollama's models there -- a second startup
    probe, a two-second refusal on Windows with Ollama stopped -- although every
    read re-discovers anyway.
    """
    from core.model_router import ModelRouter

    router = ModelRouter({"local": {"url": fake_ollama.url}, "groq_api_key": ""})
    assert fake_ollama.tags_requests == 0
    assert any(model.name == MODEL for model in router.models())   # use still discovers
    assert fake_ollama.tags_requests == 1


# ---------------------------------------------------------------------------
#  The real bridge functions, on the real eel dispatch path
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def main_module():
    import core.main as module
    return module


@pytest.fixture
def local_status(monkeypatch, main_module):
    """Point the running app at a given Ollama URL with a fresh, private monitor.

    Returns a function that performs the swap, so each test chooses the server.
    Cloud providers are stubbed so nothing here can leave the machine, and the
    shared provider snapshot is replaced so no other test's entry is served.
    """
    def _cloud(provider_id, fast, strong):
        return {"id": provider_id, "name": provider_id, "kind": "cloud", "role": "cloud",
                "state": ProviderState.SETUP_REQUIRED.value, "model": fast, "models": [],
                "secret": {"configured": False, "masked": "", "source": "none", "encrypted": False},
                "tiers": {"fast": fast, "complex": strong}, "detail": "stub"}

    monkeypatch.setattr(providers, "describe_cloud", _cloud)
    monkeypatch.setattr(provider_status, "CACHE", provider_status.ProviderStatusCache())
    monkeypatch.setattr(main_module, "current_provider_mode", lambda: ProviderMode.AUTO)

    def _use(url: str, *, local_enabled: bool = True) -> StatusMonitor:
        monitor = StatusMonitor()
        monkeypatch.setattr(ollama_service, "STATUS", monitor)
        monkeypatch.setattr(main_module.brain, "ollama_url", url + "/api/chat")
        monkeypatch.setattr(main_module.brain, "ollama_model", MODEL)
        monkeypatch.setattr(main_module.brain, "local_enabled", local_enabled)
        return monitor

    return _use


class _RecordingSocket:
    """What eel's _process_message writes the reply to."""

    def __init__(self):
        self.replies: dict = {}

    def send(self, message: str) -> None:
        payload = json.loads(message)
        self.replies[payload["return"]] = (time.perf_counter(), payload)


def _dispatch_like_eel(first: str, second: str, gap: float = 0.03):
    """``first``, then ``second`` sent ``gap`` seconds later, on eel's hub.

    Returns each call's latency measured the way the UI experiences it: from
    when the client SENT it to when the reply was written. Timing from the
    greenlet's spawn instead would hide the freeze, because a blocked hub also
    delays the spawn of everything queued behind it.
    """
    import eel
    import gevent

    ws = _RecordingSocket()
    sent_first = time.perf_counter()
    one = gevent.spawn(eel._process_message, {"call": 1, "name": first, "args": []}, ws)
    gevent.sleep(gap)
    sent_second = sent_first + gap
    two = gevent.spawn(eel._process_message, {"call": 2, "name": second, "args": []}, ws)
    gevent.joinall([one, two], timeout=30)
    assert 1 in ws.replies and 2 in ws.replies, "a bridge call never answered"
    for call_id in (1, 2):
        assert ws.replies[call_id][1]["status"] == "ok", ws.replies[call_id][1].get("error")
    return (ws.replies[1][0] - sent_first, ws.replies[2][0] - sent_second,
            ws.replies[1][1]["value"])


@pytest.mark.parametrize("polled", ["get_system_readiness", "get_command_center_state",
                                    "get_providers"])
def test_a_cheap_bridge_call_is_not_queued_behind_a_poll_while_ollama_hangs(
        polled, main_module, local_status, hung_ollama):
    """The measured defect: a cheap call waited 3.87 s behind readiness."""
    local_status(hung_ollama)
    polled_latency, cheap_latency, value = _dispatch_like_eel(polled, "get_voice_diagnostics")
    assert polled_latency < 1.0, f"{polled} took {polled_latency:.2f}s against a hung Ollama"
    assert cheap_latency < 0.5, (
        f"get_voice_diagnostics waited {cheap_latency:.2f}s behind {polled}: "
        "the eel hub was blocked on Ollama")
    if polled == "get_system_readiness":
        # Honest while the probe is still out: not measured, not online.
        assert value["model"]["state"] == OllamaState.UNKNOWN
        assert value["model"]["local"]["online"] is False
        assert value["providers"]["ollama"] == "unknown"


def test_polling_asks_a_running_ollama_once_per_ttl(main_module, local_status, fake_ollama):
    monitor = local_status(fake_ollama.url)
    polls = ("get_system_readiness", "get_command_center_state", "get_provider_health",
             "get_providers", "get_local_model_status", "get_health_status")
    for name in polls:
        getattr(main_module, name)()
    _settle(monitor, 1)
    for name in polls:
        getattr(main_module, name)()
    time.sleep(0.1)
    assert fake_ollama.tags_requests == 1, (
        f"{fake_ollama.tags_requests} requests to /api/tags for one TTL of polling")

    readiness = main_module.get_system_readiness()
    assert readiness["model"]["state"] == OllamaState.READY
    assert readiness["model"]["local"]["online"] is True
    assert readiness["providers"]["ollama"] == "online"
    assert main_module.get_providers()["ollama"]["state"] == ProviderState.READY.value


def test_ollama_stopping_becomes_visible_to_readiness(main_module, local_status, fake_ollama,
                                                      monkeypatch):
    clock = _Clock()
    monitor = local_status(fake_ollama.url)
    monitor._clock = clock
    main_module.get_system_readiness()
    _settle(monitor, 1)
    assert main_module.get_system_readiness()["model"]["state"] == OllamaState.READY

    fake_ollama.close()                                  # Ollama stops
    clock.now += monitor.ttl_seconds + 1
    main_module.get_system_readiness()                   # stale: one refresh
    _settle(monitor, 2)
    after = main_module.get_system_readiness()
    assert after["model"]["state"] in {OllamaState.OLLAMA_UNAVAILABLE, OllamaState.NOT_INSTALLED}
    assert after["model"]["local"]["online"] is False
    assert after["providers"]["ollama"] == "offline"


def test_local_models_disabled_means_no_request_to_ollama_at_all(main_module, local_status,
                                                                 fake_ollama):
    monitor = local_status(fake_ollama.url, local_enabled=False)
    readiness = main_module.get_system_readiness()
    for name in ("get_command_center_state", "get_provider_health", "get_providers",
                 "get_local_model_status", "get_health_status"):
        getattr(main_module, name)()
    time.sleep(0.2)
    assert fake_ollama.tags_requests == 0
    assert monitor.probe_count == 0
    assert readiness["model"]["state"] == OllamaState.DISABLED
    # It used to say "online" here, from a probe it had no reason to make.
    assert readiness["providers"]["ollama"] == "disabled"


def test_startup_asks_a_stopped_ollama_once(main_module, local_status, monkeypatch):
    listener = ClosingListener()
    try:
        monitor = local_status(listener.url)
        monkeypatch.setitem(main_module.CONFIG.setdefault("local", {}), "autostart", False)
        monkeypatch.setattr(main_module, "OLLAMA_BOOT", dict(main_module.OLLAMA_BOOT))

        def _never(*_a, **_k):
            raise AssertionError("the test must not start a real Ollama")

        monkeypatch.setattr(ollama_service.subprocess, "Popen", _never)
        main_module._start_ollama()
        banner = main_module.probe_local_model()          # what main() prints
        main_module.describe_providers()                  # the startup warm-up
        time.sleep(0.2)
        assert listener.connections == 1, f"startup asked Ollama {listener.connections} times"
        assert monitor.probe_count == 0, "the boot result was probed again"
        assert banner["state"] in {OllamaState.OLLAMA_UNAVAILABLE, OllamaState.NOT_INSTALLED}
    finally:
        listener.close()


def test_startup_with_ollama_running_checks_it_and_reads_the_models_once(
        main_module, local_status, fake_ollama, monkeypatch):
    local_status(fake_ollama.url)
    monkeypatch.setattr(main_module, "OLLAMA_BOOT", dict(main_module.OLLAMA_BOOT))

    def _never(*_a, **_k):
        raise AssertionError("the test must not start a real Ollama")

    monkeypatch.setattr(ollama_service.subprocess, "Popen", _never)
    main_module._start_ollama()
    banner = main_module.probe_local_model()
    main_module.describe_providers()
    main_module.get_system_readiness()
    time.sleep(0.1)
    # One liveness check (ensure_running: reuse before start) and one model
    # inventory. Everything after startup reads that measurement.
    assert fake_ollama.tags_requests == 2
    assert banner["state"] == OllamaState.READY


def test_the_router_measures_ollama_instead_of_trusting_an_unmeasured_snapshot(
        main_module, local_status, fake_ollama, monkeypatch):
    """The hub fills the shared snapshot without waiting, so its Ollama half can
    be UNKNOWN. A route must still be decided on a real measurement."""
    monitor = local_status(fake_ollama.url)
    monkeypatch.setattr(main_module, "current_provider_mode", lambda: ProviderMode.LOCAL)
    monkeypatch.setattr(main_module.brain, "provider_mode", "LOCAL")
    monkeypatch.setattr(main_module.brain, "preferred_cloud", main_module.current_preferred_cloud())
    # Hold the first probe so the UI's snapshot is certain to be taken before
    # any measurement exists -- the situation the router must not trust.
    gate = threading.Event()
    monitor._prober = lambda url: gate.wait(5) and ollama_service.probe(url)

    shown = main_module.describe_providers()
    assert shown["ollama"]["state"] == ProviderState.UNKNOWN.value
    # Nothing is decided on an unmeasured Ollama -- no usable route, and no
    # "LOCAL has no provider" either, which the UI renders as "configure a
    # provider": no route is presented at all until it is measured.
    assert shown["route"] is None and shown["complexRoute"] is None
    assert shown["routePending"] is True
    snapshot = provider_status.CACHE._entries
    assert len(snapshot) == 1
    assert next(iter(snapshot.values()))[1][1]["state"] == ProviderState.UNKNOWN.value

    gate.set()
    route = asyncio.run(main_module.brain.route_for_async("olá"))
    # The router read THAT snapshot (no second entry was produced for it)...
    assert len(provider_status.CACHE._entries) == 1
    # ...and still routed on a measurement: the probe in flight, joined.
    assert route["provider"] == "ollama"
    assert route["usable"] is True, route["reason"]
    assert fake_ollama.tags_requests == 1
    assert monitor.probe_count == 1
