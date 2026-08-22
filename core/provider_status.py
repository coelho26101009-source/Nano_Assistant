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


def describe_pair(
    mode: providers.ProviderMode,
    *,
    groq_fast_model: str,
    groq_complex_model: str,
    ollama_model: str,
    ollama_base_url: str,
    local_enabled: bool = True,
) -> tuple[dict, dict]:
    """Describe both providers, probing only those the mode can actually use.

    In CLOUD mode Ollama is never contacted and in LOCAL mode Groq is never
    contacted -- not even for a status probe. That is a privacy property, not an
    optimisation: in LOCAL mode nothing at all leaves the machine.
    """
    if mode == providers.ProviderMode.CLOUD:
        groq = providers.describe_groq(groq_fast_model, groq_complex_model)
        ollama = {
            "id": "ollama", "name": "Ollama", "kind": "local", "role": "fallback",
            "state": providers.ProviderState.DISABLED.value,
            "model": ollama_model, "models": [],
            "secret": {"configured": True, "masked": "", "source": "none", "encrypted": False},
            "detail": "Modo Cloud: o Ollama não é contactado.", "url": ollama_base_url,
        }
        return groq, ollama

    if mode == providers.ProviderMode.LOCAL:
        groq = {
            "id": "groq", "name": "Groq", "kind": "cloud", "role": "primary",
            "state": providers.ProviderState.DISABLED.value,
            "model": groq_fast_model, "models": [],
            "secret": {"configured": False, "masked": "", "source": "none", "encrypted": False},
            "tiers": {"fast": groq_fast_model, "complex": groq_complex_model},
            "detail": "Modo Local: o Groq não é contactado.",
        }
        ollama = providers.describe_ollama(ollama_model, ollama_base_url, local_enabled=local_enabled)
        return groq, ollama

    groq = providers.describe_groq(groq_fast_model, groq_complex_model)
    ollama = providers.describe_ollama(ollama_model, ollama_base_url, local_enabled=local_enabled)
    return groq, ollama


def cache_key(mode: providers.ProviderMode, groq_fast_model: str, groq_complex_model: str,
              ollama_model: str) -> str:
    """Snapshots are per mode AND per configured model.

    Keying on mode alone meant changing the conversation model in Settings kept
    reporting the previous model's availability until the TTL expired.
    """
    return f"{mode.value}|{groq_fast_model}|{groq_complex_model}|{ollama_model}"


__all__ = [
    "CACHE",
    "DEFAULT_TTL_SECONDS",
    "ProviderStatusCache",
    "cache_key",
    "describe_pair",
]
