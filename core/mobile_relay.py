"""NANO on the phone, away from home: the PC's side of the encrypted relay.

WHY THIS EXISTS
---------------
At home the phone talks to this PC over the Wi-Fi (``core.mobile_server``).
Away from home it cannot: the PC is behind the router, and NANO will not ask
anyone to open a port, set up a VPN or put the PC on the internet. Instead the
PC keeps ONE OUTGOING connection to a small relay (``relay/`` in this
repository, a Cloudflare Worker), and the phone connects to the same relay.
The relay copies messages between the two. Nothing ever connects IN to the PC.

WHY THE RELAY CANNOT READ ANYTHING
----------------------------------
Every message through it is the same sealed envelope the phone sends on the
home Wi-Fi: AES-256-GCM with the phone's own key, bound to the device, the
request id and the direction (``core.mobile_link``). The relay never has that
key. It sees a room name, sizes and times -- not a word of a conversation --
and it cannot change or replay a message, because the PC opens each one with
the paired phone's key and accepts each request id once. Pairing over the relay
is protected the same way: the key is sealed to the phone's own public key and
nothing is issued until the person says yes ON THE PC.

THE ROOM
--------
The PC's room is named by SHA-256 of a random 256-bit token kept in the OS
secret store (DPAPI on Windows). The PC proves the token when it connects, so
nobody else can sit in its room; the relay keeps no account and stores nothing.
Phones learn the room name from the pairing QR code. Removing the last paired
phone throws the token away: the next pairing uses a fresh room.

WHEN IT CONNECTS
----------------
Only while phone access is on, "Away from home" is on (the default), a relay
address is known, and there is something to serve: a paired phone or a QR code
on the screen. Off means off: turning phone access off closes the connection.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from urllib.parse import urlsplit

from core import secret_store
from core.mobile_link import LinkError, MobileLink

logger = logging.getLogger("nano.mobile.relay")

#: Where NANO's relay runs: relay/ deployed by .github/workflows/relay.yml.
#: ``NANO_RELAY_URL`` overrides it, for a self-hosted relay or a test.
DEFAULT_RELAY_URL = "https://nano-relay.coelho26101009.workers.dev"
RELAY_URL_ENV = "NANO_RELAY_URL"

#: Same limit as a request on the home Wi-Fi (core/mobile_server.py).
MAX_BODY_BYTES = 256 * 1024
#: The relay refuses larger frames (relay/src/index.js MAX_FRAME).
MAX_FRAME = 300 * 1024
#: Requests handled at once. A phone's event long-poll holds one for up to 25 s.
MAX_WORKERS = 16
#: How often an idle connection is checked by a ping, and how long the PC waits
#: before trying again after a failure (doubling up to the maximum).
HEARTBEAT_SECONDS = 30
RETRY_MIN_SECONDS = 2
RETRY_MAX_SECONDS = 60
#: A connection that lasted this long was a real one: reconnect at once after
#: it. A shorter one (accepted, then closed straight away) backs off instead.
STABLE_SECONDS = 30
#: The relay closes with this code when ANOTHER NANO took this PC's room (the
#: source copy and the installed app both running with the same token).
REPLACED_CLOSE_CODE = 4000

_TOKEN_SECRET = "mobile_relay_token"
_ROOM_PREFIX = b"nano-relay-room-v1|"
_KINDS = frozenset({"rpc", "pair"})


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def room_for_token(token: bytes) -> str:
    """The room a token owns: base64url(SHA-256(prefix + token)). 43 characters."""
    return _b64e(hashlib.sha256(_ROOM_PREFIX + token).digest())


def clean_relay_url(value: Any) -> str:
    """An https:// relay address, or "" when it is not one NANO will use.

    Plain http:// is accepted only for this computer itself (a developer's
    local relay or a test): anywhere else it would be a downgrade for nothing,
    and the relay in front of the phone must be the real one.
    """
    text = str(value or "").strip().rstrip("/")
    if not text or len(text) > 200:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if parts.username or parts.password or parts.query or parts.fragment:
        return ""
    if parts.path not in ("",):
        return ""
    host = parts.hostname or ""
    if parts.scheme == "https" and host:
        return text
    if parts.scheme == "http" and host in ("127.0.0.1", "localhost"):
        return text
    return ""


def relay_url() -> str:
    """The relay this PC uses: the environment override, else NANO's own."""
    return clean_relay_url(os.getenv(RELAY_URL_ENV) or DEFAULT_RELAY_URL)


def websocket_url(base: str, role: str, room: str) -> str:
    scheme = "wss" if base.startswith("https://") else "ws"
    return f"{scheme}://{base.split('://', 1)[1]}/v1/{role}/{room}"


class RelayToken:
    """The PC's room token, in the OS secret store."""

    def __init__(self, secret_name: str = _TOKEN_SECRET):
        self._secret_name = secret_name
        self._lock = threading.Lock()
        self._cache: bytes | None = None

    def get(self) -> bytes:
        """The token, created the first time it is needed."""
        with self._lock:
            if self._cache is not None:
                return self._cache
            raw = secret_store.get_secret(self._secret_name) or ""
            token = b""
            try:
                token = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)) if raw else b""
            except (ValueError, TypeError):
                token = b""
            if len(token) != 32:
                token = os.urandom(32)
                if not secret_store.set_secret(self._secret_name, _b64e(token)):
                    raise LinkError("store_failed", 500)
            self._cache = token
            return token

    def room(self) -> str:
        return room_for_token(self.get())

    def forget(self) -> None:
        """Throw the token away: the next connection uses a new room."""
        with self._lock:
            self._cache = None
            secret_store.delete_secret(self._secret_name)


def _valid_frame(message: Any) -> dict | None:
    if not isinstance(message, dict):
        return None
    kind, number, conn, body = message.get("t"), message.get("q"), message.get("c"), message.get("b")
    if kind not in _KINDS:
        return None
    if not isinstance(number, int) or isinstance(number, bool) or number < 0:
        return None
    if not isinstance(conn, str) or len(conn) != 32 or not all(ch in "0123456789abcdef" for ch in conn):
        return None
    if not isinstance(body, dict):
        return None
    return {"t": kind, "q": number, "c": conn, "b": body}


class RelayClient:
    """Keeps the PC's outgoing connection to the relay and answers what comes in.

    ``wanted`` says whether the PC should be connected right now; it is checked
    before every connection and every few seconds while connected. Requests are
    answered on worker threads by the same ``MobileLink.handle_pair`` /
    ``handle_rpc`` the home Wi-Fi server uses, so pairing, approvals, replay
    protection and revocation behave identically on both paths.
    """

    def __init__(self, link: MobileLink, wanted: Callable[[], bool],
                 token: RelayToken | None = None,
                 url: Callable[[], str] = relay_url):
        self.link = link
        self._wanted = wanted
        self.token = token or RelayToken()
        self._url = url
        self.state = "off"          # off | connecting | connected | offline | unavailable
        self.error = ""
        self._close_code: int | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._stopping = False
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="nano-relay")

    # control, from any thread
    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self.poke()
                return
            self._stopping = False
            self._thread = threading.Thread(target=self._run, name="nano-relay", daemon=True)
            self._thread.start()

    def poke(self) -> None:
        """Re-check ``wanted`` now (a phone was paired, access was switched...)."""
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                pass

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._stopping = True
            thread = self._thread
        self.poke()
        if thread is not None:
            thread.join(timeout)
        self.state = "off"

    def info(self) -> dict:
        url = self._url()
        return {"available": bool(url), "state": self.state if url else "unavailable",
                "host": urlsplit(url).hostname if url else "", "error": self.error}

    def room(self) -> str | None:
        """The room name for the QR code, or None when the relay is not in use."""
        if not self._url():
            return None
        try:
            return self.token.room()
        except LinkError:
            return None

    # the connection, on its own thread with its own event loop
    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception:
            logger.exception("Relay connection loop stopped")
            self.state = "offline"

    async def _pause(self, seconds: float) -> None:
        assert self._wake is not None
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        retry = RETRY_MIN_SECONDS
        try:
            while not self._stopping:
                url = self._url()
                if not url:
                    self.state = "unavailable"
                    await self._pause(30)
                    continue
                if not self._wanted():
                    self.state = "off"
                    retry = RETRY_MIN_SECONDS
                    await self._pause(10)
                    continue
                self.state = "connecting"
                began = time.monotonic()
                self._close_code = None
                served = await self._connect_once(url)
                if self._stopping:
                    break
                if served and time.monotonic() - began >= STABLE_SECONDS \
                        and self._close_code != REPLACED_CLOSE_CODE:
                    retry = RETRY_MIN_SECONDS
                    continue
                if served and self._close_code == REPLACED_CLOSE_CODE:
                    # Two NANOs replacing each other in one room looped as fast
                    # as TLS allowed, using up the relay's free daily quota.
                    retry = RETRY_MAX_SECONDS
                    self.error = "replaced"
                self.state = "offline"
                await self._pause(retry)
                retry = min(retry * 2, RETRY_MAX_SECONDS)
        finally:
            self.state = "off"
            self._loop = None
            self._wake = None

    async def _connect_once(self, url: str) -> bool:
        """One connection, served until it ends. True when it was established."""
        import aiohttp
        try:
            token = self.token.get()
        except LinkError:
            self.error = "store_failed"
            return False
        target = websocket_url(url, "pc", room_for_token(token))
        timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_connect=15)
        try:
            async with aiohttp.ClientSession(trust_env=True, timeout=timeout) as session:
                async with session.ws_connect(
                        target, headers={"Authorization": f"Bearer {_b64e(token)}"},
                        heartbeat=HEARTBEAT_SECONDS, max_msg_size=MAX_FRAME,
                        autoping=True) as ws:
                    self.state, self.error = "connected", ""
                    logger.info("Away-from-home relay connected")
                    try:
                        await self._serve(ws)
                    finally:
                        self._close_code = ws.close_code
                    return True
        except aiohttp.WSServerHandshakeError as exc:
            self.error = "refused" if exc.status in (401, 403) else f"http_{exc.status}"
            logger.warning("The relay refused the connection (%s)", exc.status)
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
            self.error = "unreachable"
            logger.debug("Relay unreachable: %s", type(exc).__name__)
        return False

    async def _serve(self, ws) -> None:
        import aiohttp
        loop = asyncio.get_running_loop()
        pending: set[asyncio.Task] = set()

        async def watch() -> None:
            # Off means off: close as soon as the PC should no longer be here.
            while not ws.closed:
                await self._pause(5)
                if self._stopping or not self._wanted() or not self._url():
                    await ws.close()
                    return

        watcher = asyncio.create_task(watch())
        try:
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    if message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                        break
                    continue
                try:
                    frame = _valid_frame(json.loads(message.data))
                except (ValueError, TypeError):
                    frame = None
                if frame is None:
                    continue
                task = asyncio.create_task(self._answer(ws, loop, frame))
                pending.add(task)
                task.add_done_callback(pending.discard)
        finally:
            watcher.cancel()
            for task in list(pending):
                task.cancel()
            logger.info("Away-from-home relay disconnected")

    async def _answer(self, ws, loop, frame: dict) -> None:
        status, body = await loop.run_in_executor(self._pool, self.dispatch, frame)
        if ws.closed:
            return
        try:
            await ws.send_str(json.dumps({"q": frame["q"], "c": frame["c"], "s": status, "b": body},
                                         ensure_ascii=False, separators=(",", ":")))
        except (ConnectionError, RuntimeError):
            pass                  # the connection ended under the answer

    def dispatch(self, frame: dict) -> tuple[int, dict]:
        """Answer one phone frame exactly as the home Wi-Fi server would."""
        client = "relay:" + frame["c"]
        if self._stopping or not self._wanted():
            return 503, {"error": "access_off"}
        try:
            self.link.check_client(client)
            body = frame["b"]
            if len(json.dumps(body, separators=(",", ":"))) > MAX_BODY_BYTES:
                raise LinkError("bad_request", 413)
            if frame["t"] == "pair":
                return 200, self.link.handle_pair(body)
            return 200, self.link.handle_rpc(body)
        except LinkError as exc:
            if exc.status in (400, 401, 410):
                self.link.note_failure(client)
            return exc.status, {"error": exc.code}
        except Exception:
            logger.exception("Phone request through the relay failed")
            return 500, {"error": "internal_error"}


__all__ = ["DEFAULT_RELAY_URL", "RELAY_URL_ENV", "RelayClient", "RelayToken",
           "clean_relay_url", "relay_url", "room_for_token", "websocket_url"]
