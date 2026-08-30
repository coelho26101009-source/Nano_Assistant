"""The control channel between the Electron desktop shell and this backend.

WHY A PIPE AND NOT A LOCALHOST PORT
-----------------------------------
The global hotkey lives in the Electron main process, but the voice turn lives
here. Something has to carry "the user pressed Ctrl+Shift+Space" across that
boundary, and it has to keep working when the main window is hidden, minimised
or not rendered at all -- which rules out routing through the renderer and the
eel websocket, because there may be no renderer.

The obvious answer is an authenticated loopback endpoint. This module
deliberately does NOT do that. Electron already spawns this process, so a pipe
between parent and child exists before any request is made, and it is a
strictly smaller attack surface:

    loopback port                       stdin/stdout pipe
    ------------------------------      --------------------------------
    listens on a real socket            no socket, no port, nothing to scan
    reachable by every local process    reachable only by the parent process
    needs a shared secret               the OS handle *is* the authorisation
    secret must be generated, passed,   nothing to generate, pass, store,
      stored, compared, never logged      log by accident, or leak
    survives Nano's death as a stale    dies exactly when the process does
      listener until the port is freed

So the trust model is: **the operating system decides who may talk to this
channel, and the answer is "the process that spawned Nano".** There is no
token, because there is nothing a token could add -- a local attacker able to
write to another process's stdin has already won by an easier route.

WHAT THE CHANNEL MAY DO
-----------------------
Nothing that is not on ``handlers``. The dispatcher looks the operation up in
an explicit mapping supplied by the host; an unknown name is refused. There is
no eval, no "call this function by name", no shell, no path, no code. An
operation is a *name Nano already implements*, and the payload it receives is
data. In particular ``start_voice_turn`` only starts a voice turn: whatever the
user then says is resolved by the Brain and travels the normal
request -> policy -> permission -> execution pipeline, exactly as a typed
message does. The channel confers no privilege at all.

Failure is closed in every direction: an unparseable line is dropped, an
unknown operation is refused, a handler that raises returns an error, and if
the channel cannot start, Nano runs without it (the desktop shell then reports
the hotkey as unavailable rather than pretending it works).

THE WIRE FORMAT
---------------
One JSON object per line, tagged so it cannot be confused with log output
sharing the same stream::

    parent -> Nano   @@NANO_IPC@@{"id":"7","op":"start_voice_turn","args":{}}
    Nano  -> parent  @@NANO_IPC@@{"id":"7","ok":true,"result":{...}}
    Nano  -> parent  @@NANO_IPC@@{"event":"voice_phase","payload":{...}}

Lines without the tag are ignored by both sides, so ordinary prints and
tracebacks on the same stream are harmless.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from typing import Any, Callable, Mapping, TextIO

logger = logging.getLogger("nano.desktop_bridge")

#: Marks a line as belonging to this protocol. Chosen so it cannot occur in
#: ordinary log output, and so a partially written line is simply not matched.
PROTOCOL_TAG = "@@NANO_IPC@@"

#: A request bigger than this is refused unread. The parent is trusted, but a
#: bound means a malformed stream cannot make Nano allocate without limit.
MAX_LINE_BYTES = 64 * 1024

Handler = Callable[[dict], Any]


def encode(message: dict) -> str:
    """One protocol line, terminated. Never raises on unserialisable payloads."""
    try:
        body = json.dumps(message, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        body = json.dumps({"ok": False, "error": "unserialisable_payload"})
    return f"{PROTOCOL_TAG}{body}\n"


def decode(line: str) -> dict | None:
    """Parse one protocol line, or None if it is not one (or is malformed)."""
    if not line:
        return None
    index = line.find(PROTOCOL_TAG)
    if index < 0:
        return None
    try:
        parsed = json.loads(line[index + len(PROTOCOL_TAG):].strip())
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


class DesktopBridge:
    """Reads control requests from the parent process and answers them.

    The host supplies the operations; this class supplies the transport, the
    framing and the refusal of everything not supplied. Both halves are needed
    for the channel to do anything, which is the point: no operation can appear
    here by accident.
    """

    def __init__(
        self,
        handlers: Mapping[str, Handler],
        *,
        reader: TextIO | None = None,
        writer: TextIO | None = None,
    ) -> None:
        # A copy, so the caller cannot widen the surface after start().
        self._handlers: dict[str, Handler] = dict(handlers)
        self._reader = reader
        self._writer = writer
        self._write_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = False

    # ------------------------------------------------------------- lifecycle

    @property
    def running(self) -> bool:
        return self._started and not self._stop.is_set()

    @property
    def operations(self) -> tuple[str, ...]:
        """Exactly what this channel will answer. Everything else is refused."""
        return tuple(sorted(self._handlers))

    def start(self) -> bool:
        """Begin reading. Returns False if there is no usable input stream."""
        if self._started:
            return True
        reader = self._reader if self._reader is not None else sys.stdin
        if reader is None or getattr(reader, "closed", False):
            logger.info("Desktop control channel not started: no input stream.")
            return False
        self._reader = reader
        self._started = True
        self._thread = threading.Thread(
            target=self._pump, name="nano-desktop-bridge", daemon=True)
        self._thread.start()
        logger.info("Desktop control channel ready: %s", ", ".join(self.operations))
        return True

    def stop(self) -> None:
        self._stop.set()

    # ----------------------------------------------------------------- output

    def emit(self, event: str, payload: dict | None = None) -> None:
        """Push an unsolicited event to the parent. Never raises."""
        if not self._started:
            return
        self._write({"event": str(event), "payload": payload or {}})

    def _write(self, message: dict) -> None:
        writer = self._writer if self._writer is not None else sys.stdout
        if writer is None:
            return
        line = encode(message)
        try:
            # One lock, one whole line, one flush: another thread printing to
            # the same stream can appear before or after this line, but never
            # inside it.
            with self._write_lock:
                writer.write(line)
                writer.flush()
        except (OSError, ValueError):
            # The parent went away. Nothing to recover: the child is about to
            # be terminated anyway, and a broken pipe must not kill the turn.
            logger.debug("Desktop control channel write failed", exc_info=True)

    # ------------------------------------------------------------------ input

    def _pump(self) -> None:
        reader = self._reader
        # Bounded at the read call itself, not just checked afterwards: an
        # unbounded `readline()` would buffer an entire pathological line --
        # gigabytes, with no newline -- before the length check below ever
        # ran, which is exactly the unbounded allocation this bound exists to
        # rule out.
        read_line = lambda: reader.readline(MAX_LINE_BYTES + 1)
        try:
            for raw in iter(read_line, ""):
                if self._stop.is_set():
                    break
                if len(raw) > MAX_LINE_BYTES:
                    logger.warning("Desktop control line refused: too large.")
                    continue
                request = decode(raw)
                if request is None:
                    continue
                self.handle(request)
        except (OSError, ValueError):
            logger.debug("Desktop control channel read ended", exc_info=True)
        finally:
            self._stop.set()
            logger.info("Desktop control channel closed.")

    def handle(self, request: dict) -> dict:
        """Dispatch one decoded request and answer it. Also the test seam."""
        request_id = request.get("id")
        op = request.get("op")
        args = request.get("args")
        if not isinstance(args, dict):
            args = {}

        handler = self._handlers.get(op) if isinstance(op, str) else None
        if handler is None:
            # Fails closed, and says which names exist so a mismatch between
            # the two halves is obvious in the desktop log rather than silent.
            response = {"id": request_id, "ok": False, "error": "unknown_operation",
                        "op": op, "supported": list(self.operations)}
            logger.warning("Desktop control refused unknown operation: %r", op)
            self._write(response)
            return response

        try:
            result = handler(args)
        except Exception as exc:  # noqa: BLE001 - a handler must never kill the pump
            logger.exception("Desktop control operation %r failed", op)
            response = {"id": request_id, "ok": False, "error": "operation_failed",
                        "op": op, "detail": str(exc)}
            self._write(response)
            return response

        response = {"id": request_id, "ok": True, "op": op,
                    "result": result if isinstance(result, dict) else {"value": result}}
        self._write(response)
        return response


__all__ = ["MAX_LINE_BYTES", "PROTOCOL_TAG", "DesktopBridge", "decode", "encode"]
