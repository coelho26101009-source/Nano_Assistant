"""One cached, off-the-event-loop view of provider status.

WHY THIS MODULE EXISTS
----------------------
Describing a provider is not free: ``providers.describe_groq()`` performs a
synchronous ``httpx.get`` against api.groq.com with a 10 second timeout, and
``providers.describe_ollama()`` performs one or two more against the local API.
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

THREE ACCESS PATTERNS, ONE CACHE
--------------------------------
``get_async``   awaits a worker thread on a miss. For the chat path: the loop
                keeps turning while the probe runs.
``get_fresh``   blocks on a miss. For startup and explicit user actions, from a
                thread that is allowed to block.
``get_stale_ok`` returns whatever is cached immediately -- even if expired --
                and refreshes in the background. For high-frequency UI polling:
                a poller must never wait on the network, and a status that is a
                few seconds old is honest enough for a status panel.

Nothing here holds a secret. ``providers.describe_groq`` returns only a masked
hint and booleans, and this module never inspects the payloads it caches.
"""
from __future__ import annotations

import asyncio
import concurrent.futures as _futures
import logging
import threading
import time
from typing import Any, Callable

from core import providers

logger = logging.getLogger("nano.provider_status")

# How long a snapshot is considered current. Short enough that saving an API key
# or starting Ollama shows up almost immediately; long enough that an active
# conversation does not re-probe on every message.
DEFAULT_TTL_SECONDS = 45.0


class ProviderStatusCache:
    """A tiny TTL cache with a single-flight background refresh."""

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS):
        self.ttl_seconds = float(ttl_seconds)
        self._lock = threading.RLock()
        self._entries: dict[str, tuple[float, Any]] = {}
        # Keys with a refresh already in flight, so a burst of pollers produces
        # exactly one outbound probe rather than one per tick.
        self._refreshing: set[str] = set()

    # ------------------------------------------------------------- internals

    def _peek(self, key: str) -> tuple[Any | None, bool]:
        """Return (value, is_fresh). value is None only when nothing is cached."""
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None, False
        stored_at, value = entry
        return value, (time.monotonic() - stored_at) < self.ttl_seconds

    def _store(self, key: str, value: Any) -> Any:
        with self._lock:
            self._entries[key] = (time.monotonic(), value)
        return value

    def _refresh_in_background(self, key: str, producer: Callable[[], Any]) -> None:
        with self._lock:
            if key in self._refreshing:
                return
            self._refreshing.add(key)

        def _run() -> None:
            try:
                self._store(key, producer())
            except Exception:
                # A failed refresh keeps the previous snapshot rather than
                # replacing a usable status with nothing.
                logger.debug("Background provider refresh failed for %r", key, exc_info=True)
            finally:
                with self._lock:
                    self._refreshing.discard(key)

        threading.Thread(target=_run, name=f"nano-provider-refresh", daemon=True).start()

    # ---------------------------------------------------------------- access

    def get_fresh(self, key: str, producer: Callable[[], Any]) -> Any:
        """Cached value if fresh, otherwise produce one now. May block."""
        value, fresh = self._peek(key)
        if fresh:
            return value
        return self._store(key, producer())

    async def get_async(self, key: str, producer: Callable[[], Any]) -> Any:
        """Cached value if fresh, otherwise produce one on a worker thread.

        The await is what keeps the calling event loop responsive; the producer
        itself is ordinary blocking code and stays that way.
        """
        value, fresh = self._peek(key)
        if fresh:
            return value
        return self._store(key, await asyncio.to_thread(producer))

    def get_stale_ok(self, key: str, producer: Callable[[], Any]) -> Any:
        """Never block if anything is cached; refresh in the background.

        This is what a once-per-second UI poll must use. Only the very first
        call for a key pays the network cost.
        """
        value, fresh = self._peek(key)
        if value is None:
            return self._store(key, producer())
        if not fresh:
            self._refresh_in_background(key, producer)
        return value

    def invalidate(self, key: str | None = None) -> None:
        """Drop a key (or everything) so the next read re-probes.

        Called when the credential, the model or the mode changes: a new key
        must be reflected immediately, not when the TTL happens to expire.
        """
        with self._lock:
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
                                           local_enabled=local_enabled)
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
    wanted = [pid for pid in ids if only is None or pid in only]
    skipped = [pid for pid in ids if pid not in wanted]

    for provider_id in skipped:
        fast, strong = _tiers_for(cloud_tiers, provider_id)
        clouds[provider_id] = _disabled(provider_id, fast, "Não avaliado nesta consulta.",
                                        complex_model=strong)

    if wanted:
        with _futures.ThreadPoolExecutor(max_workers=len(wanted)) as pool:
            probes = {}
            for provider_id in wanted:
                fast, strong = _tiers_for(cloud_tiers, provider_id)
                probes[provider_id] = pool.submit(
                    providers.describe_cloud, provider_id, fast, strong)
            for provider_id, probe in probes.items():
                clouds[provider_id] = probe.result()

    if mode == providers.ProviderMode.CLOUD:
        ollama = _disabled("ollama", ollama_model,
                           "Modo Cloud: o Ollama não é contactado.",
                           kind="local", role="fallback", url=ollama_base_url)
        return clouds, ollama

    ollama = providers.describe_ollama(ollama_model, ollama_base_url,
                                       local_enabled=local_enabled)
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
    "cache_key",
    "describe_all",
    "describe_pair",
]
