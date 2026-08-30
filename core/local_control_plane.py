"""Origin enforcement for Nano's local control plane.

WHAT THIS DEFENDS AGAINST, CONCRETELY.

Nano's UI talks to Python over eel, which is Bottle plus a WebSocket at
``/eel``. Every ``@eel.expose``d function is reachable through that socket --
about seventy of them, including ``confirm_action``, ``resolve_permission``,
``set_permission_policy``, ``set_autonomy_mode`` and ``set_emergency_stop``.
That is the whole approval surface: the mechanism by which a human says yes to
a PC Control action.

eel does no Origin validation whatsoever -- there is not one occurrence of the
string "origin" anywhere in the library. A WebSocket handshake is NOT subject
to the same-origin policy, and browsers send it without a CORS preflight, so
any web page the user happens to have open could open

    ws://localhost:<port>/eel?page=index.html

and call those functions. That is Cross-Site WebSocket Hijacking. A page could
approve its own pending permission request, switch the provider to CLOUD to get
prompts sent off the machine, disable the emergency stop, or drive the
assistant through ``send_message``. The ephemeral port raises the cost of
finding the server but is not a security control: a page can scan.

THE FIX, AND ITS EXACT LIMITS.

A Bottle ``before_request`` hook rejects any WebSocket upgrade whose ``Origin``
is not this very server. Browsers always send ``Origin`` on a WebSocket
handshake and cannot forge it, so this closes the web-page vector completely.

It deliberately does NOT attempt to authenticate the caller. A native process
running as the same user can send any Origin it likes, so this is not a defence
against local malware -- and it is not trying to be: a process at that
privilege level can already read the DPAPI store, modify Nano's own files, or
keylog the approval dialog. Closing the browser vector is the part that
genuinely changes an attacker's reach. See docs/SECURITY.md for the residual
risk, stated plainly rather than papered over.

Only the ``/eel`` RPC route is guarded. Static assets are left alone on
purpose: serving index.html to a curious local process discloses nothing, and
tightening it would break nothing useful while adding a way to accidentally
brick the UI.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger("nano.control_plane")

#: The RPC route. Static asset routes are intentionally not guarded.
WEBSOCKET_PATH = "/eel"

#: Loopback names the UI may legitimately be served from. A page loaded from
#: any of these on OUR port is the real Nano UI; anything else is not.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def allowed_origins(port: int) -> frozenset[str]:
    """Every spelling of "this server" a browser might legitimately send."""
    return frozenset(f"http://{host}:{int(port)}" for host in _LOOPBACK_HOSTS)


def is_origin_allowed(origin: str | None, port: int) -> bool:
    """True when this Origin is our own loopback server on our own port.

    A MISSING Origin is refused. Browsers always send one on a WebSocket
    handshake, so absence means the caller is not the UI; allowing it would
    leave the exact hole this function exists to close, reachable by anything
    that simply omits the header.
    """
    if not origin:
        return False

    parts = urlsplit(origin.strip())
    # Scheme and host must both be right. Comparing the raw string alone would
    # accept "http://localhost:1234.evil.com" on a sloppy prefix match, so the
    # comparison is on parsed components.
    if parts.scheme != "http":
        return False
    if parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return False
    try:
        return int(parts.port or 0) == int(port)
    except (TypeError, ValueError):
        return False


def install_origin_guard(port: int, app: Any = None) -> Any:
    """Refuse non-Nano WebSocket upgrades on the local control plane.

    Returns the hook it installed, so a test can call it directly rather than
    standing up a real server.
    """
    import bottle

    target = app if app is not None else bottle.default_app()

    def _guard() -> None:
        request = bottle.request
        if request.path != WEBSOCKET_PATH:
            return
        origin = request.get_header("Origin")
        if is_origin_allowed(origin, port):
            return
        logger.warning(
            "Local control channel connection refused: origin %r not authorized.",
            origin or "<missing>",
        )
        raise bottle.HTTPError(403, "forbidden_origin")

    target.add_hook("before_request", _guard)
    logger.info("Local control channel restricted to its own origin (port %s).", port)
    return _guard


__all__ = [
    "WEBSOCKET_PATH",
    "allowed_origins",
    "install_origin_guard",
    "is_origin_allowed",
]
