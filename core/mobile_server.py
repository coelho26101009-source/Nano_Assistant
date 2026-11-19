"""The network side of NANO on the phone: a small HTTP server on the home network.

Three POST routes, all carrying sealed JSON (see ``core.mobile_link``):

    /v1/pair   a phone that read the QR asks to pair, then asks whether the PC said yes
    /v1/rpc    a paired phone calls one of the registered methods
    (nothing else; every other path is 404, every other verb 405)

The server is an ordinary thread-per-request ``ThreadingHTTPServer`` on its own
OS threads. It never runs on eel's gevent hub, so a phone waiting 25 seconds for
the next event does not hold up the PC window. It starts only when "Phone
access" is on, and stops when it is turned off.

CORS: the native app's requests are made by Android, not by a web page, so they
carry no Origin. A browser Origin is answered only when it is a localhost page
(the app's own WebView, or a developer's preview); since every request is
sealed with a key a web page cannot have, CORS is a convenience here, not the
lock.
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from core.mobile_link import LinkError, MobileLink

logger = logging.getLogger("nano.mobile.server")

MAX_BODY_BYTES = 256 * 1024
_ROUTES = {"/v1/pair", "/v1/rpc"}
_ALLOWED_ORIGIN_HOSTS = {"localhost", "127.0.0.1"}


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    parts = urlsplit(origin)
    return parts.scheme in {"http", "https", "capacitor"} and parts.hostname in _ALLOWED_ORIGIN_HOSTS


def _handler_for(link: MobileLink):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NANO"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet: never log bodies
            logger.debug("%s %s", self.client_address[0], fmt % args)

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            origin = self.headers.get("Origin")
            if _origin_allowed(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            try:
                self.end_headers()
                self.wfile.write(body)
            except ConnectionError:
                # The phone went away before the answer (screen locked, app
                # sent to the background, Wi-Fi changed). Normal on a phone,
                # and nothing to answer anymore: seen on a real Android +
                # Windows pairing, where it filled the log with tracebacks.
                self.close_connection = True

        def do_OPTIONS(self) -> None:  # noqa: N802 - http.server naming
            origin = self.headers.get("Origin")
            self.send_response(204 if _origin_allowed(origin) else 403)
            if _origin_allowed(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "POST")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Max-Age", "600")
                self.send_header("Vary", "Origin")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            self._send(405, {"error": "method_not_allowed"})

        def do_POST(self) -> None:  # noqa: N802
            client = self.client_address[0]
            path = urlsplit(self.path).path
            if path not in _ROUTES:
                self._send(404, {"error": "not_found"})
                return
            try:
                link.check_client(client)
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > MAX_BODY_BYTES:
                    raise LinkError("bad_request", 413 if length > MAX_BODY_BYTES else 400)
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise LinkError("bad_request") from exc
                reply = link.handle_pair(body) if path == "/v1/pair" else link.handle_rpc(body)
                self._send(200, reply)
            except LinkError as exc:
                if exc.status in (400, 401, 410):
                    link.note_failure(client)
                self._send(exc.status, {"error": exc.code})
            except Exception:
                logger.exception("Phone request failed")
                self._send(500, {"error": "internal_error"})

    return Handler


class MobileServer:
    """Start/stop wrapper. ``start`` is idempotent; ``stop`` waits for the socket."""

    def __init__(self, link: MobileLink, port: int, host: str = "0.0.0.0"):
        self.link = link
        self.port = int(port)
        self.host = host
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.error: str = ""
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> bool:
        with self._lock:
            if self._httpd is not None:
                return True
            try:
                httpd = ThreadingHTTPServer((self.host, self.port), _handler_for(self.link))
            except OSError as exc:
                self.error = "port_in_use" if getattr(exc, "errno", None) in (98, 48, 10048) else "cannot_listen"
                logger.warning("Phone access could not listen on port %s: %s", self.port, exc)
                return False
            httpd.daemon_threads = True
            self.port = httpd.server_address[1]
            self._httpd = httpd
            self.error = ""
            self._thread = threading.Thread(target=httpd.serve_forever, name="nano-mobile",
                                            kwargs={"poll_interval": 0.5}, daemon=True)
            self._thread.start()
            logger.info("Phone access listening on port %s", self.port)
            return True

    def stop(self) -> None:
        with self._lock:
            httpd, self._httpd = self._httpd, None
            if httpd is None:
                return
            httpd.shutdown()
            httpd.server_close()
            logger.info("Phone access stopped")


__all__ = ["MAX_BODY_BYTES", "MobileServer"]
