"""ONE TLS verification context for every HTTP client Nano builds.

WHY THIS EXISTS -- THREE MEASURED STALLS WITH ONE CAUSE
-------------------------------------------------------
httpx gives every client it constructs a fresh ``ssl.SSLContext``, and
building one parses certifi's whole CA bundle: measured at ~195-200 ms per
client on Windows (Python 3.12), and twice that when a proxy is configured,
because the proxy transport builds a second one. That is invisible on a worker
thread -- the parse releases the GIL -- and very visible on the two threads
that must never wait:

* **eel's hub.** ``Brain.reload_cloud_credentials`` built a new ``AsyncGroq``
  inside the bridge call that saved or removed a key, so every other bridge
  call waited 200-530 ms behind it. That is the intermittent
  ``test_no_status_call_waits_on_a_hung_cloud_provider[remove_cloud_api_key-mistral]``
  failure: 0.53-0.60 s against a 0.5 s limit, with a proxy in play.
* **the shared asyncio loop.** ``GoogleChat.stream`` and ``MistralChat.stream``
  built a client for every streamed round -- every tool round of a turn -- and
  stalled the loop that streams answers and resolves confirmations by 269 and
  204 ms a time. ``Brain._ollama_fallback`` did the same once per local turn.

The context is identical every time, so it is built once and shared. Nothing
about verification changes: it is exactly the context httpx would build
itself -- ``httpx.create_ssl_context()``, which honours ``SSL_CERT_FILE`` and
``SSL_CERT_DIR`` the same way -- and every client still verifies every peer
against it. ``core.ollama_service`` made this move for its status probe first;
this module is that decision applied to every client, so the process holds
one context rather than one per call site.

SHARING IS SAFE
---------------
An ``SSLContext`` is configured once and afterwards only used to open
connections, which OpenSSL supports from any number of threads. httpcore does
write one thing to the context it is handed -- the ALPN list, on every new
connection -- and it always did, on the context all of one client's
connections share. Nano never enables HTTP/2, so every writer writes the same
``["http/1.1"]``.

WHEN IT IS BUILT
----------------
Whoever asks first builds it, under a lock; everyone else gets the same
object. ``core.main`` calls :func:`prewarm` before eel starts serving, so the
one parse happens at startup -- usually already done by then, on the Ollama or
provider warm-up thread that asked first -- and never inside a bridge call.
"""
from __future__ import annotations

import logging
import ssl
import threading

import httpx

logger = logging.getLogger("nano.http_tls")

_CONTEXT: ssl.SSLContext | None = None
_LOCK = threading.Lock()


def shared_context() -> ssl.SSLContext:
    """The process-wide verification context. Built on first use, then shared.

    Pass it as ``verify=`` to any httpx client: construction then costs
    microseconds instead of a CA-bundle parse.
    """
    context = _CONTEXT
    if context is not None:
        return context
    return _build()


def _build() -> ssl.SSLContext:
    global _CONTEXT
    with _LOCK:
        if _CONTEXT is None:
            _CONTEXT = httpx.create_ssl_context()
        return _CONTEXT


def is_ready() -> bool:
    """Whether the context exists already. Never builds it."""
    return _CONTEXT is not None


def prewarm() -> bool:
    """Build the context now, on this thread, if nobody has. Never raises.

    For startup: the parse must happen before eel serves, not in the first
    bridge call that needs a client. False means it could not be built here;
    the first client that needs it will try again and report the real error.
    """
    try:
        shared_context()
        return True
    except Exception:
        logger.warning("Could not prepare the shared TLS context", exc_info=True)
        return False


__all__ = ["is_ready", "prewarm", "shared_context"]
