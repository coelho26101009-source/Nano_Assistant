"""One cached, off-the-event-loop view of provider status.

WHY THIS MODULE EXISTS
----------------------
Describing a provider is not free: ``providers.describe_groq()`` performs a
synchronous ``httpx.get`` against api.groq.com with a 10 second timeout, and
describing Ollama means asking the local API, which a stopped Ollama makes a
two-second refused connection on Windows. (Ollama's half now has its own shared
measurement, ``ollama_service.STATUS``; see ``describe_all(ollama_wait=...)``.)
Before this module there were two independent callers and two independent
problems.

1. ``Brain._describe_providers`` held its own 45 s snapshot and, on a miss, ran
   those synchronous calls from inside ``async def chat`` -- freezing the shared
   event loop for the whole round trip roughly every third or fourth message.

2. ``main.get_settings`` had no cache at all, and the Settings page polls it
   once per second so live microphone levels stay live. That was a fresh,
   blocking call to the Groq API every second the page was open.

Both are solved the same way: a single snapshot, keyed by provider mode, shared
by every caller, refreshed off-thread, and never recomputed on the hot path.
Sharing it also *reduces* outbound calls, because the Brain and the UI no longer
probe the same account separately.

FOUR ACCESS PATTERNS, ONE CACHE, ONE REFRESH
--------------------------------------------
``get_async``    awaits a worker thread on a miss.
``get_fresh``    blocks on a miss. For threads that are allowed to block --
                 startup's warm-up, tests.
``get_within``   blocks on a miss FOR AT MOST ``wait_seconds``, and returns
                 early once the caller has what it needs. For the chat
                 router, on a worker thread: see "What the router waits for".
``get_stale_ok`` NEVER waits. Fresh: the snapshot. Expired: the expired
                 snapshot, and a refresh starts in the background. Nothing
                 cached at all: the caller's ``placeholder`` -- a status built
                 without asking anyone, in which every provider that would
                 have to be asked is UNKNOWN -- and a refresh starts in the
                 background. The only form eel's hub may use.

WHAT THE ROUTER WAITS FOR
-------------------------
The router used ``get_async``, so a cold or expired snapshot made a chat turn
wait for EVERY configured cloud provider's probe -- the slowest one included,
whether or not it could change the decision. Measured with one configured
provider that accepted connections and never answered: every such turn waited
its full 10 s probe timeout -- 10.5 s in AUTO and 10.6 s in CLOUD, where that
provider can never be used at all -- although the preferred provider had
answered READY at once and was the one that answered.

``get_within`` bounds that. Each probe publishes its result the moment it
lands (``describe_all`` reports into the refresh it is producing), so the
router can stop waiting as soon as its decision is determined -- CLOUD needs
only the preferred provider -- and stops at ``ROUTE_WAIT_SECONDS`` at the
latest. Whatever has not answered by then is UNKNOWN for that one turn, which
routing already never treats as ready; the refresh itself keeps running in
the background and lands in the snapshot for the next turn and for the UI.

The cold case is the one that used to go wrong. ``get_stale_ok`` ran the
producer inline when nothing was cached, so the first Settings poll after
startup, after a saved key or after a mode change probed every configured
cloud provider ON eel's hub: measured at 0.6 s with healthy providers and
10.4 s with one that did not answer, during which every other bridge call
waited. A placeholder costs a dictionary and a secret-store read.

WHAT IS GUARANTEED
------------------
* Single flight. At most one producer runs per key at a time, whichever of
  the three patterns started it: a poller, the Brain and a settings change
  asking together share one probe set -- they used to run one each.
* A placeholder is never stored. It is not a measurement, so the router, which
  never reads the placeholder path, can never route on one.
* An invalidation is final. A refresh that started before ``invalidate()``
  describes the state before the change -- a removed key, a different model --
  so its result is handed to whoever was waiting for it and then discarded
  rather than cached as current.

Nothing here holds a secret. ``providers.describe_groq`` returns only a masked
hint and booleans, and this module never inspects the payloads it caches.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable, NamedTuple

from core import model_defaults, providers, secret_store

logger = logging.getLogger("nano.provider_status")

# How long a snapshot is considered current. Short enough that saving an API key
# or starting Ollama shows up almost immediately; long enough that an active
# conversation does not re-probe on every message.
DEFAULT_TTL_SECONDS = 45.0

#: The longest a chat turn waits for provider status before deciding on what
#: has been measured (see "What the router waits for" above). Healthy probes
#: answer in 0.3-0.6 s here -- the figure the settings path's wait was chosen
#: from, before ~200 ms of each was removed by core.http_tls -- so this only
#: ever cuts off a provider that is not answering, and it caps what one such
#: provider can add to a turn at this instead of its 10 s probe timeout.
ROUTE_WAIT_SECONDS = 3.0

#: How long a caller that is allowed to block waits for a refresh SOMEONE ELSE
#: started. The producer is bounded by the providers' own HTTP timeouts, so
#: this only matters if the refreshing thread died without saying so.
_JOIN_TIMEOUT_SECONDS = 60.0

_MISSING = object()


class _Refresh:
    """One producer run for one key. Everyone who asks while it runs shares it."""

    __slots__ = ("done", "generation", "value", "error", "parts", "changed", "version")

    def __init__(self, generation: int):
        self.done = threading.Event()
        self.generation = generation
        self.value: Any = _MISSING
        self.error: BaseException | None = None
        #: Results the producer published before it finished, by name -- one
        #: per cloud provider, as each probe lands. See get_within.
        self.parts: dict[str, Any] = {}
        #: Signalled on every publish and once more when the run ends; version
        #: counts those signals, so a reader that looked away cannot miss one.
        self.changed = threading.Condition()
        self.version = 0

    def publish(self, name: str, value: Any) -> None:
        with self.changed:
            self.parts[name] = value
            self.version += 1
            self.changed.notify_all()

    def finished(self) -> None:
        with self.changed:
            self.version += 1
            self.changed.notify_all()


#: The refresh the CURRENT THREAD is producing, if any. Set by
#: ProviderStatusCache._run around the producer call, so describe_all can
#: report each probe into the refresh it belongs to without every producer
#: having to pass it along; module-private on purpose.
_PRODUCING = threading.local()


def _producing() -> _Refresh | None:
    return getattr(_PRODUCING, "refresh", None)


class Reading(NamedTuple):
    """What ``get_within`` found.

    ``source`` is one of:

    ``cached``    a snapshot inside its TTL; ``age_seconds`` says how old.
    ``measured``  a refresh that completed while the caller waited.
    ``partial``   the wait ended first: ``value`` is None, and ``parts`` holds
                  what had been measured by then.
    """

    value: Any
    parts: dict
    source: str
    age_seconds: float | None
    waited_seconds: float


class ProviderStatusCache:
    """A TTL cache with ONE single-flight refresh per key, for every reader."""

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS, *,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._lock = threading.RLock()
        self._entries: dict[str, tuple[float, Any]] = {}
        # The refresh in flight for each key, so a burst of pollers -- and the
        # Brain asking at the same moment -- produces exactly one probe set.
        self._refreshes: dict[str, _Refresh] = {}
        # Bumped by invalidate(): a refresh started under an older generation
        # may finish, but may not store what it found.
        self._generation = 0
        #: Producer runs actually performed. Diagnostics and tests read it.
        self.refresh_count = 0

    # ------------------------------------------------------------- internals

    def _peek(self, key: str) -> tuple[Any, bool]:
        """Return (value, is_fresh). value is _MISSING when nothing is cached."""
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return _MISSING, False
        stored_at, value = entry
        return value, (self._clock() - stored_at) < self.ttl_seconds

    def _claim(self, key: str) -> tuple[_Refresh, bool]:
        """(refresh, True) for the caller that must run it; (refresh, False) to share it."""
        with self._lock:
            current = self._refreshes.get(key)
            if current is not None and current.generation == self._generation:
                return current, False
            refresh = _Refresh(self._generation)
            self._refreshes[key] = refresh
            return refresh, True

    def _run(self, key: str, producer: Callable[[], Any], refresh: _Refresh) -> None:
        outer = _producing()
        _PRODUCING.refresh = refresh
        try:
            refresh.value = producer()
        except Exception as exc:
            # A failed refresh keeps the previous snapshot rather than replacing
            # a usable status with nothing; whoever waited gets the error.
            refresh.error = exc
            logger.debug("Provider refresh failed for %r", key, exc_info=True)
        finally:
            _PRODUCING.refresh = outer
            with self._lock:
                self.refresh_count += 1
                if refresh.value is not _MISSING and refresh.generation == self._generation:
                    self._entries[key] = (self._clock(), refresh.value)
                if self._refreshes.get(key) is refresh:
                    del self._refreshes[key]
            refresh.done.set()
            refresh.finished()

    def _start_in_background(self, key: str, producer: Callable[[], Any]) -> _Refresh:
        refresh, owner = self._claim(key)
        if owner:
            try:
                threading.Thread(target=self._run, args=(key, producer, refresh),
                                 name="nano-provider-refresh", daemon=True).start()
            except RuntimeError as exc:
                # Interpreter shutting down: release the claim so nothing waits on it.
                with self._lock:
                    if self._refreshes.get(key) is refresh:
                        del self._refreshes[key]
                refresh.error = exc
                refresh.done.set()
        return refresh

    def _join(self, key: str, producer: Callable[[], Any]) -> Any:
        """Share the refresh in flight, or run one on this thread. BLOCKING."""
        refresh, owner = self._claim(key)
        if owner:
            self._run(key, producer, refresh)
        elif not refresh.done.wait(_JOIN_TIMEOUT_SECONDS):
            logger.warning("Provider refresh for %r did not finish; asking directly.", key)
            return producer()
        if refresh.error is not None:
            raise refresh.error
        if refresh.value is _MISSING:
            raise RuntimeError("the provider refresh ended without a result")
        return refresh.value

    # ---------------------------------------------------------------- access

    def get_fresh(self, key: str, producer: Callable[[], Any]) -> Any:
        """Cached value if fresh, otherwise the refresh in flight or a new one. MAY BLOCK."""
        value, fresh = self._peek(key)
        if fresh:
            return value
        return self._join(key, producer)

    async def get_async(self, key: str, producer: Callable[[], Any]) -> Any:
        """Cached value if fresh, otherwise a refresh awaited on a worker thread.

        The await is what keeps the calling event loop responsive; the producer
        itself is ordinary blocking code and stays that way.
        """
        value, fresh = self._peek(key)
        if fresh:
            return value
        return await asyncio.to_thread(self._join, key, producer)

    def get_within(self, key: str, producer: Callable[[], Any], *,
                   wait_seconds: float,
                   enough: Callable[[dict], bool] | None = None,
                   start: bool = True) -> Reading:
        """The snapshot, waiting for a refresh AT MOST ``wait_seconds``. MAY BLOCK.

        Fresh: the snapshot, at once. Otherwise the refresh in flight is shared
        -- or one is started in the background -- and waited for until it
        completes, until ``enough(parts)`` says the caller has what it needs,
        or until ``wait_seconds`` have passed, whichever is first. The refresh
        is never run on this thread, so ending the wait never abandons it: it
        finishes and is cached as usual.

        ``start=False`` never starts a refresh, so it can never make a
        provider be contacted: it shares the one in flight, or else answers
        with whatever is cached -- at any age -- or with nothing. For a caller
        acting on a decision already taken (failover inside a turn), which
        must not probe on its own account.

        The partial answer is the caller's to interpret; this cache never
        stores it, for the same reason it never stores a placeholder.
        """
        with self._lock:
            entry = self._entries.get(key)
            in_flight = self._refreshes.get(key)
            if in_flight is not None and in_flight.generation != self._generation:
                in_flight = None
        age = max(0.0, self._clock() - entry[0]) if entry is not None else None
        if entry is not None and age < self.ttl_seconds:
            return Reading(entry[1], {}, "cached", age, 0.0)
        if not start and in_flight is None:
            if entry is not None:
                return Reading(entry[1], {}, "cached", age, 0.0)
            return Reading(None, {}, "partial", None, 0.0)

        started = time.monotonic()
        deadline = started + max(0.0, float(wait_seconds))
        refresh = self._start_in_background(key, producer) if start else in_flight
        # The caller's predicate runs OUTSIDE the refresh's lock, so a slow one
        # can never hold up a probe that is trying to publish. The version it
        # was evaluated against is what makes that safe: a publish that lands
        # in between is seen at the next check instead of being slept through.
        with refresh.changed:
            parts, seen = dict(refresh.parts), refresh.version
        while not refresh.done.is_set():
            if enough is not None and enough(parts):
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            with refresh.changed:
                if refresh.version == seen and not refresh.done.is_set():
                    refresh.changed.wait(remaining)
                parts, seen = dict(refresh.parts), refresh.version
        with refresh.changed:
            parts = dict(refresh.parts)
        waited = time.monotonic() - started
        if refresh.done.is_set():
            if refresh.error is not None:
                raise refresh.error
            if refresh.value is not _MISSING:
                return Reading(refresh.value, parts, "measured", 0.0, waited)
        return Reading(None, parts, "partial", None, waited)

    def get_stale_ok(self, key: str, producer: Callable[[], Any],
                     placeholder: Callable[[], Any] | None = None) -> Any:
        """Answer NOW, whatever is cached; refresh in the background. Never waits.

        With nothing cached at all this returns ``placeholder()`` -- or None
        without one -- and never runs the producer on the calling thread.
        """
        value, fresh = self._peek(key)
        if fresh:
            return value
        self._start_in_background(key, producer)
        if value is not _MISSING:
            return value
        return placeholder() if placeholder is not None else None

    def refresh(self, key: str, producer: Callable[[], Any]) -> threading.Event | None:
        """Make sure a current value is on its way. Never waits.

        Returns None when the cached value is already fresh, otherwise the
        event that is set when the refresh in flight -- shared, or started
        here -- has finished. For a caller that wants to wait on its own terms,
        e.g. cooperatively on eel's hub.
        """
        _value, fresh = self._peek(key)
        if fresh:
            return None
        return self._start_in_background(key, producer).done

    def invalidate(self, key: str | None = None) -> None:
        """Drop a key (or everything) so the next read re-probes.

        Called when the credential, the model or the mode changes: a new key
        must be reflected immediately, not when the TTL happens to expire --
        and not when a refresh that started before the change finishes, which
        is why the generation moves too.
        """
        with self._lock:
            self._generation += 1
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)


# The process-wide cache. One instance so the Brain and the UI share both the
# data and the cost of obtaining it.
CACHE = ProviderStatusCache()


def _disabled(provider_id: str, model: str, detail: str,
              *, kind: str = "cloud", role: str = "cloud",
              complex_model: str = "", url: str = "") -> dict:
    """A provider the current mode forbids contacting, described without asking.

    This is what makes LOCAL a privacy guarantee rather than a preference: the
    payload is synthesised locally, so nothing leaves the machine -- not even a
    status probe. The secret block reports configured=False on purpose; the UI
    must not imply a key was read in a mode where the provider is not used.
    """
    payload = {
        "id": provider_id, "name": providers.provider_name(provider_id),
        "kind": kind, "role": role,
        "state": providers.ProviderState.DISABLED.value,
        "model": model, "models": [], "records": [],
        "secret": {"configured": False, "masked": "", "source": "none", "encrypted": False},
        "tiers": {"fast": model, "complex": complex_model or model},
        # The model this provider WOULD use, reported with the same vocabulary
        # as a live payload so the UI never has to special-case a mode it was
        # not allowed to probe in.
        "model_source": (model_defaults.SOURCE_CONFIGURED if str(model or "").strip()
                         else model_defaults.SOURCE_NONE),
        "detail": detail,
    }
    if url:
        payload["url"] = url
    return payload


def _tiers_for(cloud_tiers: dict[str, tuple[str, str]], provider_id: str) -> tuple[str, str]:
    fast, complex_model = cloud_tiers.get(provider_id, ("", ""))
    return str(fast or ""), str(complex_model or fast or "")


def describe_all(
    mode: providers.ProviderMode,
    *,
    cloud_tiers: dict[str, tuple[str, str]],
    ollama_model: str,
    ollama_base_url: str,
    local_enabled: bool = True,
    only: tuple[str, ...] | None = None,
    ollama_wait: bool = True,
) -> tuple[dict[str, dict], dict]:
    """Describe every provider, probing only those the mode can actually use.

    Returns ``(clouds, ollama)``, where ``clouds`` maps every id in
    ``providers.CLOUD_PROVIDER_IDS`` to its payload. A MAPPING RATHER THAN A
    TUPLE, and the change paid for itself immediately: the previous
    ``(google, groq, ollama)`` return meant every caller and every test had to
    be edited to unpack one more provider, so the shape of the return value was
    a tax on adding one. Callers now index by the same id the router, the
    cooldown registry and the settings surface are keyed on.

    ``cloud_tiers`` is ``{provider_id: (fast_model, complex_model)}``. A
    provider absent from it is still described -- with no model configured,
    which is what SETUP_REQUIRED means -- because "you have not chosen a model
    yet" is a state the user has to be able to see and fix.

    In CLOUD mode Ollama is never contacted and in LOCAL mode NO cloud provider
    is contacted -- not even for a status probe. That is a privacy property,
    not an optimisation: in LOCAL mode nothing at all leaves the machine, and
    adding a third cloud provider must not quietly weaken it.

    ``only`` restricts which cloud providers are probed at all. The others are
    reported as not evaluated rather than as unavailable, because "we did not
    ask" and "we asked and it is down" are different facts.

    ``ollama_wait`` is passed to ``providers.describe_ollama``. A caller on the
    eel hub MUST pass False: the Ollama half is then the shared measurement as
    it stands (see ollama_service.STATUS) -- no wait, and no probe started
    either. The snapshots built with False are produced on a background thread
    from settings captured when they began, and can finish long afterwards
    behind a cloud provider that does not answer; every consumer replaces
    their Ollama half with a live read, and it is that read, made with the
    settings of the moment, that starts a probe when one is due. Starting one
    from here instead acted on settings that no longer held -- measured: a
    snapshot begun in AUTO sent Ollama a request after the switch to CLOUD.
    """
    ids = tuple(providers.CLOUD_PROVIDER_IDS)
    clouds: dict[str, dict] = {}

    if mode == providers.ProviderMode.LOCAL:
        for provider_id in ids:
            fast, strong = _tiers_for(cloud_tiers, provider_id)
            clouds[provider_id] = _disabled(
                provider_id, fast,
                f"Modo Local: o {providers.provider_name(provider_id)} não é contactado.",
                role=("primary" if provider_id == providers.ProviderId.GROQ.value else "cloud"),
                complex_model=strong)
        ollama = providers.describe_ollama(ollama_model, ollama_base_url,
                                           local_enabled=local_enabled, wait=ollama_wait,
                                           start_probe=ollama_wait)
        return clouds, ollama

    # Every describe_* short-circuits on an absent key with no network call, so
    # there is nothing to gate here: an unconfigured install gets an honest
    # SETUP_REQUIRED payload (and a place to paste a key) rather than a
    # "disabled" state it never chose.
    #
    # THE CLOUD PROBES RUN CONCURRENTLY, AND THAT MATTERS.
    #
    # Each is a synchronous httpx call with a 10 second timeout. Run in
    # sequence, a cold snapshot with three providers configured could block for
    # thirty seconds -- and on a cache miss that block happens on whichever
    # thread asked, which for the Settings poller is eel's single cooperative
    # hub. A hub that stalls is a UI that is frozen, which this project has
    # already shipped once (see core.audio_feedback.prewarm). One thread per
    # provider keeps the worst case at one timeout instead of the sum of them.
    #
    # DAEMON THREADS, NOT A ThreadPoolExecutor. The interpreter joins every
    # executor worker at exit, whatever its daemon flag (measured: 3.15 s to
    # exit behind a 3 s call, against 0.34 s with a daemon thread), so a normal
    # exit with a probe in flight -- a provider that does not answer -- waited
    # out that provider's whole 10 s timeout before the process could end.
    wanted = [pid for pid in ids if only is None or pid in only]
    skipped = [pid for pid in ids if pid not in wanted]

    for provider_id in skipped:
        fast, strong = _tiers_for(cloud_tiers, provider_id)
        clouds[provider_id] = _disabled(provider_id, fast, "Não avaliado nesta consulta.",
                                        complex_model=strong)

    if wanted:
        results: dict[str, Any] = {}
        errors: dict[str, BaseException] = {}
        # When this runs as a cache refresh, each probe reports into it the
        # moment it lands, so a reader bounded by get_within can decide on the
        # providers that have answered without waiting for the one that has
        # not. Outside a refresh there is nobody to tell.
        sink = _producing()

        def _describe(provider_id: str, fast: str, strong: str) -> None:
            try:
                results[provider_id] = providers.describe_cloud(provider_id, fast, strong)
            except BaseException as exc:  # noqa: BLE001 - re-raised below, in order
                errors[provider_id] = exc
            else:
                if sink is not None:
                    sink.publish(provider_id, results[provider_id])

        probes = []
        for provider_id in wanted:
            fast, strong = _tiers_for(cloud_tiers, provider_id)
            probe = threading.Thread(target=_describe, args=(provider_id, fast, strong),
                                     name=f"nano-provider-probe-{provider_id}", daemon=True)
            probe.start()
            probes.append((provider_id, probe))
        for provider_id, probe in probes:
            probe.join()
            if provider_id in errors:
                raise errors[provider_id]
            clouds[provider_id] = results[provider_id]

    if mode == providers.ProviderMode.CLOUD:
        ollama = _disabled("ollama", ollama_model,
                           "Modo Cloud: o Ollama não é contactado.",
                           kind="local", role="fallback", url=ollama_base_url)
        return clouds, ollama

    ollama = providers.describe_ollama(ollama_model, ollama_base_url,
                                       local_enabled=local_enabled, wait=ollama_wait,
                                       start_probe=ollama_wait)
    return clouds, ollama


def _unmeasured(provider_id: str, fast: str, strong: str) -> dict:
    """A cloud provider nobody has asked yet, described without asking it.

    UNKNOWN, which routing never treats as ready. The secret block is real --
    it is local, and the Settings page renders the stored-key state from it --
    and a provider with no key at all is described by its own describer,
    which answers SETUP_REQUIRED from that same local read without touching
    the network (the contract describe_all already relies on above).
    """
    name = providers.provider_name(provider_id)
    try:
        secret = secret_store.describe(providers.CLOUD_SECRET_NAMES[provider_id])
    except Exception:
        logger.debug("Could not describe the %s credential", provider_id, exc_info=True)
        secret = {"configured": False, "masked": "", "source": "none", "encrypted": False}
    if not secret.get("configured"):
        return providers.describe_cloud(provider_id, fast, strong)
    return {
        "id": provider_id, "name": name, "kind": "cloud",
        "role": "primary" if provider_id == providers.ProviderId.GROQ.value else "cloud",
        "state": providers.ProviderState.UNKNOWN.value,
        "model": fast, "models": [], "records": [],
        "secret": secret,
        "tiers": {"fast": fast, "complex": strong or fast},
        "model_source": (model_defaults.SOURCE_CONFIGURED if str(fast or "").strip()
                         else model_defaults.SOURCE_NONE),
        "detail": f"O estado do {name} ainda não foi verificado; a verificação está em curso.",
    }


def describe_unmeasured(
    mode: providers.ProviderMode,
    *,
    cloud_tiers: dict[str, tuple[str, str]],
    ollama_model: str,
    ollama_base_url: str,
    local_enabled: bool = True,
    only: tuple[str, ...] | None = None,
) -> tuple[dict[str, dict], dict]:
    """describe_all's shape, built WITHOUT contacting anyone. Hub-safe.

    What a reader that must not wait is given when no snapshot exists yet:
    every cloud provider the mode would have to ask is UNKNOWN (with its real,
    local credential state), every one the mode forbids is DISABLED exactly as
    describe_all reports it, and Ollama is its shared measurement as it stands
    -- no wait, no probe started. Nothing here contacts anyone, so LOCAL keeps
    its privacy guarantee and CLOUD still never contacts Ollama.

    LOCAL contacts no cloud provider in describe_all either, so there the
    placeholder IS the real answer, and is returned as such.
    """
    if mode == providers.ProviderMode.LOCAL:
        return describe_all(mode, cloud_tiers=cloud_tiers, ollama_model=ollama_model,
                            ollama_base_url=ollama_base_url, local_enabled=local_enabled,
                            only=only, ollama_wait=False)

    clouds: dict[str, dict] = {}
    for provider_id in providers.CLOUD_PROVIDER_IDS:
        fast, strong = _tiers_for(cloud_tiers, provider_id)
        if only is not None and provider_id not in only:
            clouds[provider_id] = _disabled(provider_id, fast, "Não avaliado nesta consulta.",
                                            complex_model=strong)
        else:
            clouds[provider_id] = _unmeasured(provider_id, fast, strong)

    if mode == providers.ProviderMode.CLOUD:
        ollama = _disabled("ollama", ollama_model,
                           "Modo Cloud: o Ollama não é contactado.",
                           kind="local", role="fallback", url=ollama_base_url)
    else:
        ollama = providers.describe_ollama(ollama_model, ollama_base_url,
                                           local_enabled=local_enabled, wait=False,
                                           start_probe=False)
    return clouds, ollama


def describe_pair(
    mode: providers.ProviderMode,
    *,
    groq_fast_model: str,
    groq_complex_model: str,
    ollama_model: str,
    ollama_base_url: str,
    local_enabled: bool = True,
) -> tuple[dict, dict]:
    """``(groq, ollama)`` and nothing else. For callers that only need the pair.

    The other cloud providers are not merely omitted from the return value --
    they are never described at all, so this cannot cost a request to an
    account the caller did not ask about.
    """
    clouds, ollama = describe_all(
        mode,
        cloud_tiers={providers.ProviderId.GROQ.value: (groq_fast_model, groq_complex_model)},
        ollama_model=ollama_model,
        ollama_base_url=ollama_base_url,
        local_enabled=local_enabled,
        only=(providers.ProviderId.GROQ.value,),
    )
    return clouds[providers.ProviderId.GROQ.value], ollama


def cache_key(mode: providers.ProviderMode, cloud_tiers: dict[str, tuple[str, str]],
              ollama_model: str, preferred_cloud: str = "") -> str:
    """Snapshots are per mode AND per configured model AND per preference.

    Keying on mode alone meant changing the conversation model in Settings kept
    reporting the previous model's availability until the TTL expired. Every
    provider's models and the preferred provider join the key for the same
    reason: a snapshot taken while Groq was preferred describes a different
    decision than one taken after the user switched.

    The cloud half is built from ``CLOUD_PROVIDER_IDS`` rather than from the
    mapping's own keys, so two callers that pass the same models in a different
    insertion order share one snapshot instead of probing twice.
    """
    parts = [mode.value, ollama_model, preferred_cloud]
    for provider_id in providers.CLOUD_PROVIDER_IDS:
        fast, strong = _tiers_for(cloud_tiers, provider_id)
        parts.extend((provider_id, fast, strong))
    return "|".join(parts)


__all__ = [
    "CACHE",
    "DEFAULT_TTL_SECONDS",
    "ProviderStatusCache",
    "ROUTE_WAIT_SECONDS",
    "Reading",
    "cache_key",
    "describe_all",
    "describe_pair",
    "describe_unmeasured",
]
