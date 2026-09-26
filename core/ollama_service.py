"""Detect, and if necessary start, the local Ollama server.

Design rules this module exists to enforce:

* **Reuse before start.** The API is probed first. If Ollama is already running
  — started by the user or by a previous Nano session — we attach to it and
  never spawn a second server.
* **Server only, never a model.** Starting `ollama serve` brings up the HTTP API
  and nothing else. We never send a warm-up inference, never `pull`, and never
  preload a model. On a 16 GB machine the idle cost of the server is tens of
  megabytes; an 8B model is several gigabytes, so it must only load when a real
  user request needs it.
* **Never download.** No `ollama pull`, ever. A missing model is reported, not
  fetched.
* **Nano does not own the user's Ollama.** We do not kill it on shutdown: it is
  a shared background service, other tools may be using it, and the model
  unloads by itself via OLLAMA_KEEP_ALIVE.
* **Measured once, read everywhere.** Asking the API is a network round trip,
  and a stopped Ollama makes it an expensive one: on Windows a connection to a
  closed port on 127.0.0.1 is not refused at once -- the stack retries the SYN
  and gives up after about two seconds. ``STATUS`` holds the latest measurement
  and is the one place the UI and the router ask. See StatusMonitor.
"""
from __future__ import annotations

import logging
import os
import shutil
import ssl
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import httpx

logger = logging.getLogger("nano.ollama")

DEFAULT_BASE_URL = "http://127.0.0.1:11434"

# How long a model stays resident after its last use. Keeping this modest
# matters on 16 GB: an idle 8B model otherwise holds gigabytes indefinitely.
DEFAULT_KEEP_ALIVE = "5m"

#: One status probe's budget. Connecting is the part a stopped server makes
#: slow; a server that accepted the connection lists its models in
#: milliseconds, so the read keeps list_models' old budget.
PROBE_TIMEOUT = httpx.Timeout(4.0, connect=2.0)

#: How long one measurement answers every reader without a new probe. The UI's
#: readiness cadence, so a window that is polling costs at most one probe per
#: ten seconds however many panels ask.
STATUS_TTL_SECONDS = 10.0

#: Beyond this age a measurement is history, not status: readers get UNKNOWN
#: while a new one is taken, never a state nobody has confirmed for a minute.
STATUS_MAX_AGE_SECONDS = 60.0

#: How long a caller that is allowed to wait will wait for SOMEONE ELSE's probe.
#: A probe is bounded by PROBE_TIMEOUT plus the executable lookup, so this only
#: matters if the probing thread died without saying so.
_JOIN_TIMEOUT_SECONDS = 10.0

#: How long ``ensure_running`` waits for a server it spawned to answer.
AUTOSTART_TIMEOUT_SECONDS = 25.0

#: How long a caller that is allowed to wait will wait for a STARTUP in
#: progress: the first probe, the spawn and the full autostart window, plus
#: the inventory read that ends it.
_STARTUP_JOIN_TIMEOUT_SECONDS = AUTOSTART_TIMEOUT_SECONDS + 10.0

# Where Ollama installs itself on Windows, beyond whatever is on PATH.
_WINDOWS_CANDIDATES = (
    r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe",
    r"%PROGRAMFILES%\Ollama\ollama.exe",
    r"%PROGRAMFILES(X86)%\Ollama\ollama.exe",
    r"%USERPROFILE%\AppData\Local\Programs\Ollama\ollama.exe",
)


class OllamaState:
    """States the UI may display. Nothing here is assumed — each is measured."""

    READY = "READY"                          # API up and the configured model is installed
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"  # API up, configured model not installed
    OLLAMA_UNAVAILABLE = "OLLAMA_UNAVAILABLE"  # binary exists but the API is not answering
    NOT_INSTALLED = "OLLAMA_NOT_INSTALLED"   # no ollama executable found
    DISABLED = "DISABLED"                    # local models switched off in config
    # Not measured yet, or the last measurement is too old to report. The one
    # state that is NOT a measurement, and it says so rather than guessing.
    UNKNOWN = "UNKNOWN"


def find_executable() -> str | None:
    """Locate ollama.exe on PATH or in the standard Windows install locations."""
    found = shutil.which("ollama")
    if found:
        return found
    if os.name == "nt":
        for raw in _WINDOWS_CANDIDATES:
            candidate = Path(os.path.expandvars(raw))
            if candidate.exists():
                return str(candidate)
    return None


_TLS_CONTEXT: ssl.SSLContext | None = None
_TLS_LOCK = threading.Lock()


def _tls() -> ssl.SSLContext:
    """httpx's default verification context, built once instead of per request.

    ``httpx.get`` builds a new client for every call, and every new client loads
    the whole CA bundle into a fresh SSL context -- measured at ~195 ms on
    Windows, for a request to plain http://127.0.0.1 that never uses TLS. That
    was most of what a status check cost with Ollama RUNNING. Verification is
    unchanged: this is exactly the context httpx would otherwise rebuild.
    """
    global _TLS_CONTEXT
    with _TLS_LOCK:
        if _TLS_CONTEXT is None:
            _TLS_CONTEXT = httpx.create_ssl_context()
        return _TLS_CONTEXT


def api_available(base_url: str = DEFAULT_BASE_URL, *, timeout: float = 2.0) -> bool:
    """One cheap GET. Used where the question is only 'is the server up yet'."""
    try:
        response = httpx.get(base_url.rstrip("/") + "/api/tags", timeout=timeout, verify=_tls())
        return response.is_success
    except Exception:
        return False


def list_models(base_url: str = DEFAULT_BASE_URL, *, timeout: float = 4.0) -> list[str]:
    """Installed models. Read-only: /api/tags never loads anything into RAM."""
    try:
        response = httpx.get(base_url.rstrip("/") + "/api/tags", timeout=timeout, verify=_tls())
        response.raise_for_status()
        return [str(item.get("name")) for item in response.json().get("models", []) if item.get("name")]
    except Exception:
        return []


def probe(base_url: str = DEFAULT_BASE_URL) -> dict:
    """ONE request: is the API answering, and which models are installed.

    Returns ``{"reachable": bool, "installed": [...], "executable": bool|None}``.
    ``executable`` is only looked up when the API is not answering, because it
    is what separates "installed but stopped" from "not installed".

    Status used to ask /api/tags twice -- api_available() for "is it up", then
    list_models() for the same list again -- so a stopped Ollama cost the
    two-second refusal twice. One answer carries both facts.

    BLOCKING. Never call this on the eel hub; read ``STATUS`` there instead.
    """
    try:
        response = httpx.get(_normalize(base_url) + "/api/tags", timeout=PROBE_TIMEOUT,
                             verify=_tls())
    except Exception:
        response = None
    if response is None or not response.is_success:
        return {"reachable": False, "installed": [],
                "executable": find_executable() is not None}
    try:
        installed = [str(item.get("name")) for item in response.json().get("models", [])
                     if item.get("name")]
    except Exception:
        installed = []
    return {"reachable": True, "installed": installed, "executable": None}


def _normalize(base_url: str) -> str:
    return str(base_url or DEFAULT_BASE_URL).rstrip("/")


def model_installed(model: str, installed: list[str]) -> bool:
    """Match a configured tag against installed names, tolerating ':latest'."""
    if not model:
        return False
    wanted = {model, f"{model}:latest"}
    if model.endswith(":latest"):
        wanted.add(model.removesuffix(":latest"))
    return any(name in wanted for name in installed)


class Autostart:
    """A cancellable "start Ollama if it is not running", for one Nano session.

    Startup runs ``ensure_running`` on a worker thread so the window does not
    wait for Ollama, which raises the question a synchronous start never had:
    what if Nano is closed while that thread is still working? The answer is
    this object. ``cancel()`` and the spawn in ``ensure_running`` take the same
    lock, so once ``cancel()`` has returned no server can be spawned by this
    session -- not "probably not", because the check and the spawn cannot
    interleave with it. A cancelled wait for the port also ends at once rather
    than at its next poll.

    A server spawned BEFORE the cancel is left running, deliberately and as
    before: it is detached, shared with every other tool that uses Ollama, and
    unloads its model by itself (see the module docstring).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def sleep(self, seconds: float) -> bool:
        """Sleep that wakes the moment the start is cancelled. True if it was."""
        return self._cancelled.wait(max(0.0, seconds))


def _cancelled_result(executable: str | None) -> dict:
    return {
        "available": False, "started": False, "reused": False, "executable": executable,
        "cancelled": True,
        "detail": "O arranque do Ollama foi cancelado porque o Nano está a encerrar.",
    }


def ensure_running(
    base_url: str = DEFAULT_BASE_URL,
    *,
    autostart: bool = True,
    timeout_seconds: float = AUTOSTART_TIMEOUT_SECONDS,
    keep_alive: str = DEFAULT_KEEP_ALIVE,
    control: Autostart | None = None,
) -> dict:
    """Make the Ollama API available, starting the server only if needed.

    Returns a dict describing what happened:
        available   bool   the API answers now
        started     bool   True only if THIS call spawned the server
        reused      bool   True if it was already running
        executable  str|None
        detail      str    human-readable explanation
        cancelled   bool   present and True only if ``control`` was cancelled

    BLOCKING, for seconds when Ollama is stopped: the first probe alone is a
    ~2 s refused connection on Windows. Startup calls it on a worker thread.
    """
    if control is not None and control.cancelled:
        return _cancelled_result(None)
    if api_available(base_url):
        # Already up — attach to it. This is what stops duplicate servers when
        # the user has Ollama Desktop open or restarts Nano.
        logger.info("Ollama já está a correr em %s (a reutilizar).", base_url)
        return {
            "available": True, "started": False, "reused": True,
            "executable": find_executable(),
            "detail": "Ollama já estava a correr; a reutilizar a instância existente.",
        }

    executable = find_executable()
    if executable is None:
        return {
            "available": False, "started": False, "reused": False, "executable": None,
            "detail": "O Ollama não está instalado (ollama.exe não encontrado). Instala em https://ollama.com para usar modelos locais.",
        }

    if not autostart:
        return {
            "available": False, "started": False, "reused": False, "executable": executable,
            "detail": "Ollama está instalado mas não está a correr, e o arranque automático está desligado.",
        }

    logger.info("Ollama não está a responder; a arrancar o servidor: %s serve", executable)
    env = dict(os.environ)
    # Applies to models this server loads later; the server itself loads none.
    env.setdefault("OLLAMA_KEEP_ALIVE", keep_alive)

    spawn_lock = control._lock if control is not None else threading.Lock()
    try:
        creation_flags = 0
        if os.name == "nt":
            # No console window, and detached from Nano's process group so that
            # closing Nano does not take the user's model server down with it.
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        # The check and the spawn under the lock cancel() takes: a Nano that
        # is shutting down never starts a server it would then walk away from.
        with spawn_lock:
            if control is not None and control.cancelled:
                return _cancelled_result(executable)
            subprocess.Popen(
                [executable, "serve"],
                # This detached shared service outlives Nano. Inheriting Nano's
                # resources directory as cwd locks that directory on Windows and
                # prevents a later upgrade/uninstall from removing the binaries.
                cwd=str(Path(executable).resolve().parent),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
    except Exception as exc:
        logger.warning("Falha ao arrancar o Ollama: %s", exc)
        return {
            "available": False, "started": False, "reused": False, "executable": executable,
            "detail": f"Não foi possível arrancar o Ollama: {exc}",
        }

    # `ollama serve` binds its port in a few seconds; poll rather than sleep.
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if control is not None and control.cancelled:
            return {**_cancelled_result(executable), "started": True}
        if api_available(base_url, timeout=1.0):
            elapsed = timeout_seconds - (deadline - time.monotonic())
            logger.info("Ollama pronto ao fim de %.1fs (nenhum modelo carregado).", elapsed)
            return {
                "available": True, "started": True, "reused": False, "executable": executable,
                "detail": "Ollama arrancado pelo Nano. Nenhum modelo carregado até ser preciso.",
            }
        if control is not None:
            control.sleep(0.5)
        else:
            time.sleep(0.5)

    return {
        "available": False, "started": True, "reused": False, "executable": executable,
        "detail": f"O Ollama foi arrancado mas a API não respondeu em {timeout_seconds:.0f}s.",
    }


def describe_measurement(model: str, base_url: str, measurement: dict | None, *,
                         local_enabled: bool = True) -> dict:
    """The honest status of ``model`` given one measurement. Pure: no I/O.

    ``measurement`` is what probe() returned, or None when there is none worth
    reporting -- which is UNKNOWN, never a guess in either direction.
    """
    if not local_enabled:
        return {
            "state": OllamaState.DISABLED, "ollamaUp": False, "modelReady": False,
            "model": model, "url": base_url, "installed": [],
            "detail": "Modelos locais desativados na configuração.",
        }

    if measurement is None:
        return {
            "state": OllamaState.UNKNOWN, "ollamaUp": False, "modelReady": False,
            "model": model, "url": base_url, "installed": [],
            "detail": "O estado do Ollama ainda não foi confirmado; a verificação está em curso.",
        }

    if not measurement.get("reachable"):
        installed_binary = bool(measurement.get("executable"))
        state = OllamaState.OLLAMA_UNAVAILABLE if installed_binary else OllamaState.NOT_INSTALLED
        detail = (
            f"O Ollama está instalado mas a API não responde em {base_url}."
            if installed_binary
            else "O Ollama não está instalado; os modelos locais não estão disponíveis."
        )
        return {
            "state": state, "ollamaUp": False, "modelReady": False,
            "model": model, "url": base_url, "installed": [], "detail": detail,
        }

    installed = [str(name) for name in measurement.get("installed") or []]
    if not model_installed(model, installed):
        return {
            "state": OllamaState.MODEL_UNAVAILABLE, "ollamaUp": True, "modelReady": False,
            "model": model, "url": base_url, "installed": installed,
            # Never silently substitute a different model.
            "detail": (
                f"O Ollama está a correr mas o modelo '{model}' não está instalado. "
                f"Instalados: {', '.join(installed) or 'nenhum'}. Instala com: ollama pull {model}"
            ),
        }

    return {
        "state": OllamaState.READY, "ollamaUp": True, "modelReady": True,
        "model": model, "url": base_url, "installed": installed,
        "detail": f"Ollama disponível com '{model}'.",
    }


def describe(model: str, base_url: str = DEFAULT_BASE_URL, *, local_enabled: bool = True) -> dict:
    """Full, honest status, measured NOW. Performs no start and no download.

    Blocking: one probe. Nothing that answers the UI calls this -- it reads
    ``STATUS``, which shares one measurement between every reader.
    """
    if not local_enabled:
        return describe_measurement(model, base_url, None, local_enabled=False)
    return describe_measurement(model, base_url, probe(base_url))


class StatusMonitor:
    """The latest measurement of the Ollama API, shared by everything that asks.

    WHY THIS EXISTS -- A MEASURED FREEZE.
    eel serves every bridge call from ONE cooperative gevent hub, and nothing in
    Nano is monkey-patched, so a blocking socket call inside any handler stops
    every other handler until it returns. Readiness (polled every 10 s) asked
    Ollama twice in a row, the command center (every 4 s) asked again, and a
    provider snapshot miss asked once more -- each time on the hub. With Ollama
    stopped each ask is a ~2 s refused connection on Windows, so readiness took
    3.9 s and a cheap call issued 30 ms after it waited 3.87 s behind it.

    TWO WAYS TO READ, ONE MEASUREMENT.
    ``read()``     never blocks on the network. It answers from the latest
                   measurement and, when that is stale, starts ONE probe on a
                   worker thread. For the eel hub.
    ``measure()``  returns a measurement no older than ``max_age``, taking one if
                   needed -- or waiting for the one already in flight rather
                   than starting a second. For threads that are allowed to wait
                   (startup, the router's worker thread).

    WHAT IT GUARANTEES.
    * At most one probe per base URL in flight, however many readers ask.
    * At most one probe per TTL while a measurement is fresh.
    * A measurement older than ``max_age_seconds`` is reported as UNKNOWN, never
      as the state it used to be. UNKNOWN is never READY.
    * Local models disabled: DISABLED, and no probe at all.
    * Nothing probes unless something asks. No timer, no resident thread: a
      hidden window that stops polling stops the probes too.
    * While startup is checking or launching the server (``begin_startup`` to
      ``finish_startup``) nobody else probes it. Readers get UNKNOWN saying so
      -- a probe of their own would race the launch and could record "not
      answering" about a server seconds away from answering -- and a caller
      of ``measure()`` waits for startup's answer instead.
    """

    def __init__(self, *, ttl_seconds: float = STATUS_TTL_SECONDS,
                 max_age_seconds: float = STATUS_MAX_AGE_SECONDS,
                 prober: Callable[[str], dict] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = float(ttl_seconds)
        self.max_age_seconds = max(float(max_age_seconds), self.ttl_seconds)
        # None means the module-level probe(), looked up at call time.
        self._prober = prober
        self._clock = clock
        self._lock = threading.Lock()
        self._measurements: dict[str, tuple[float, dict]] = {}
        self._inflight: dict[str, threading.Event] = {}
        # Base URLs whose startup is in progress, each with the event that
        # finish_startup() sets.
        self._starting: dict[str, threading.Event] = {}
        #: Probes actually performed. Diagnostics and tests read it.
        self.probe_count = 0

    # ---------------------------------------------------------------- reads

    def read(self, model: str, base_url: str = DEFAULT_BASE_URL, *,
             local_enabled: bool = True) -> dict:
        """The latest status, without waiting for the network. Hub-safe."""
        if not local_enabled:
            return describe_measurement(model, base_url, None, local_enabled=False)
        key = _normalize(base_url)
        measurement, age = self._latest(key)
        if measurement is None or age > self.ttl_seconds:
            if self._startup_event(key) is not None:
                return self._starting_status(model, base_url)
            self._refresh_in_background(key)
        return self._status(model, base_url, measurement, age)

    def measure(self, model: str, base_url: str = DEFAULT_BASE_URL, *,
                local_enabled: bool = True, max_age: float | None = None) -> dict:
        """A status no older than ``max_age`` (default: the TTL). MAY BLOCK.

        Joins a probe already in flight instead of starting a second one, and
        waits for a startup in progress instead of probing underneath it.
        """
        if not local_enabled:
            return describe_measurement(model, base_url, None, local_enabled=False)
        key = _normalize(base_url)
        limit = self.ttl_seconds if max_age is None else max(0.0, float(max_age))
        limit = min(limit, self.max_age_seconds)
        measurement, age = self._latest(key)
        if measurement is None or age > limit:
            starting = self._startup_event(key)
            if starting is not None:
                starting.wait(_STARTUP_JOIN_TIMEOUT_SECONDS)
                measurement, age = self._latest(key)
        if measurement is None or age > limit:
            self._probe_and_wait(key)
            measurement, age = self._latest(key)
        return self._status(model, base_url, measurement, age)

    def starting(self, base_url: str = DEFAULT_BASE_URL) -> bool:
        """Whether startup is checking or launching this server right now."""
        return self._startup_event(_normalize(base_url)) is not None

    # --------------------------------------------------------------- writes

    def record(self, base_url: str, measurement: dict) -> None:
        """Store a measurement taken elsewhere -- startup already asked."""
        with self._lock:
            self._measurements[_normalize(base_url)] = (self._clock(), dict(measurement))

    def begin_startup(self, base_url: str) -> None:
        """Startup owns this server's status until finish_startup(). Idempotent."""
        with self._lock:
            self._starting.setdefault(_normalize(base_url), threading.Event())

    def probe_now(self, base_url: str) -> dict | None:
        """ONE probe on the calling thread, counted but not recorded. BLOCKING.

        For startup, which holds the gate and passes the result to
        finish_startup(): the model inventory it needs after bringing the
        server up, taken without anyone else probing in between.
        """
        key = _normalize(base_url)
        try:
            measurement = (self._prober or probe)(key)
        except Exception:
            logger.debug("Ollama startup probe failed for %s", key, exc_info=True)
            measurement = None
        with self._lock:
            self.probe_count += 1
        return measurement if isinstance(measurement, dict) else None

    def finish_startup(self, base_url: str, measurement: dict | None) -> None:
        """Record what startup learned and hand the status back to readers.

        ``None`` records nothing: readers then get UNKNOWN and take their own
        measurement, which is the honest outcome of a startup that could not
        tell.
        """
        key = _normalize(base_url)
        with self._lock:
            if isinstance(measurement, dict):
                self._measurements[key] = (self._clock(), dict(measurement))
            event = self._starting.pop(key, None)
        if event is not None:
            event.set()

    def invalidate(self, base_url: str | None = None) -> None:
        with self._lock:
            if base_url is None:
                self._measurements.clear()
            else:
                self._measurements.pop(_normalize(base_url), None)

    # ------------------------------------------------------------ internals

    def _latest(self, key: str) -> tuple[dict | None, float | None]:
        with self._lock:
            entry = self._measurements.get(key)
        if entry is None:
            return None, None
        measured_at, measurement = entry
        return measurement, max(0.0, self._clock() - measured_at)

    def _status(self, model: str, base_url: str, measurement: dict | None,
                age: float | None) -> dict:
        if measurement is not None and age is not None and age > self.max_age_seconds:
            measurement, age = None, None
        status = describe_measurement(model, base_url, measurement)
        status["ageSeconds"] = round(age, 1) if measurement is not None and age is not None else None
        return status

    def _startup_event(self, key: str) -> threading.Event | None:
        with self._lock:
            return self._starting.get(key)

    def _starting_status(self, model: str, base_url: str) -> dict:
        """UNKNOWN, saying why: startup is still checking or launching Ollama."""
        status = describe_measurement(model, base_url, None)
        status["detail"] = ("O Nano está a verificar ou a arrancar o Ollama; o modelo local "
                            "fica disponível assim que o Ollama responder.")
        status["starting"] = True
        status["ageSeconds"] = None
        return status

    def _claim(self, key: str) -> tuple[threading.Event, bool]:
        """(event, True) for the caller that must probe; (event, False) to wait."""
        with self._lock:
            event = self._inflight.get(key)
            if event is not None:
                return event, False
            event = threading.Event()
            self._inflight[key] = event
            return event, True

    def _run(self, key: str, event: threading.Event) -> None:
        measurement = None
        try:
            measurement = (self._prober or probe)(key)
        except Exception:
            # A probe that fails outright keeps the previous measurement, which
            # keeps ageing and turns UNKNOWN past max_age. Nothing is invented.
            logger.debug("Ollama status probe failed for %s", key, exc_info=True)
        finally:
            with self._lock:
                self.probe_count += 1
                if isinstance(measurement, dict):
                    self._measurements[key] = (self._clock(), measurement)
                self._inflight.pop(key, None)
            event.set()

    def _refresh_in_background(self, key: str) -> None:
        event, owner = self._claim(key)
        if not owner:
            return
        try:
            # A real OS thread: the process is not monkey-patched, so the probe's
            # blocking connect happens here and never on the eel hub.
            threading.Thread(target=self._run, args=(key, event),
                             name="nano-ollama-status", daemon=True).start()
        except RuntimeError:
            # Interpreter shutting down. Release the claim so nothing waits on it.
            with self._lock:
                self._inflight.pop(key, None)
            event.set()

    def _probe_and_wait(self, key: str) -> None:
        event, owner = self._claim(key)
        if owner:
            self._run(key, event)
        else:
            event.wait(_JOIN_TIMEOUT_SECONDS)


#: The process-wide monitor. Readiness, the command center, the provider panel,
#: startup and the router all read this one, so they share both the answer and
#: the cost of getting it.
STATUS = StatusMonitor()


__all__ = [
    "AUTOSTART_TIMEOUT_SECONDS",
    "Autostart",
    "DEFAULT_BASE_URL",
    "DEFAULT_KEEP_ALIVE",
    "OllamaState",
    "PROBE_TIMEOUT",
    "STATUS",
    "STATUS_MAX_AGE_SECONDS",
    "STATUS_TTL_SECONDS",
    "StatusMonitor",
    "api_available",
    "describe",
    "describe_measurement",
    "ensure_running",
    "find_executable",
    "list_models",
    "model_installed",
    "probe",
]
