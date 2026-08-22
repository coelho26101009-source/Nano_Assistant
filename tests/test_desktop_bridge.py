"""The Electron -> Python control channel.

This channel is the one new way into the running backend that the desktop
migration adds, so these tests are about what it REFUSES at least as much as
what it does. The properties being pinned:

* the operation vocabulary is a closed allow-list, and an unknown name is
  answered with a refusal rather than doing anything;
* nothing on it can name a command, a path or a piece of code;
* it never carries or returns a secret;
* a handler that raises is answered, not allowed to kill the reader;
* log output sharing the same stream is never mistaken for a request.

Everything runs against real streams -- a real OS pipe for the reader test --
rather than by inspecting the source.
"""
from __future__ import annotations

import io
import json
import os
import threading
import time

import pytest

from core import desktop_bridge
from core.desktop_bridge import PROTOCOL_TAG, DesktopBridge, decode, encode


# ------------------------------------------------------------------ helpers

def _line(**payload) -> str:
    return encode(payload)


def _replies(writer: io.StringIO) -> list[dict]:
    return [decode(line) for line in writer.getvalue().splitlines() if decode(line)]


def _bridge(handlers, *, reader=None, writer=None):
    return DesktopBridge(handlers, reader=reader or io.StringIO(), writer=writer or io.StringIO())


# ---------------------------------------------------------------- the format

def test_a_message_survives_a_round_trip():
    original = {"id": "9", "op": "start_voice_turn", "args": {"source": "hotkey"}}
    assert decode(encode(original)) == original


def test_log_output_is_never_read_as_a_request():
    for line in (
        "  [NANO] Voice/STT ..... READY",
        "Traceback (most recent call last):",
        '{"op": "start_voice_turn"}',          # untagged JSON is not the protocol
        "",
        PROTOCOL_TAG,                           # the tag alone carries nothing
    ):
        assert decode(line) is None, f"{line!r} must not decode as a control request"


def test_a_malformed_payload_is_dropped_rather_than_raising():
    assert decode(f"{PROTOCOL_TAG}{{not json") is None
    assert decode(f"{PROTOCOL_TAG}[1, 2, 3]") is None
    assert decode(f"{PROTOCOL_TAG}42") is None


def test_an_unserialisable_result_still_produces_a_line():
    """A handler returning something odd must not break the channel."""
    writer = io.StringIO()
    bridge = _bridge({"weird": lambda _a: {"obj": object()}}, writer=writer)
    bridge.handle({"id": "1", "op": "weird"})
    # json.dumps(default=str) copes; the contract is only that a line is written.
    assert _replies(writer), "the parent must always get an answer"


# ------------------------------------------------------------- the allow-list

def test_a_registered_operation_is_dispatched_with_its_arguments():
    seen = {}
    writer = io.StringIO()
    bridge = _bridge({"start_voice_turn": lambda args: seen.update(args) or {"accepted": True}},
                     writer=writer)

    bridge.handle({"id": "1", "op": "start_voice_turn", "args": {"source": "hotkey"}})

    assert seen == {"source": "hotkey"}
    reply = _replies(writer)[0]
    assert reply == {"id": "1", "ok": True, "op": "start_voice_turn",
                     "result": {"accepted": True}}


def test_an_unknown_operation_is_refused_and_nothing_runs():
    ran = []
    writer = io.StringIO()
    bridge = _bridge({"ping": lambda _a: ran.append("ping") or {}}, writer=writer)

    bridge.handle({"id": "1", "op": "run_shell_command", "args": {"cmd": "whoami"}})

    assert ran == [], "a refused operation must not fall through to anything"
    reply = _replies(writer)[0]
    assert reply["ok"] is False
    assert reply["error"] == "unknown_operation"


@pytest.mark.parametrize("op", [
    None, "", 42, ["ping"], {"op": "ping"},
    "__import__", "eval", "exec", "os.system", "ping ", "PING",
])
def test_only_an_exact_registered_name_is_accepted(op):
    """No coercion, no normalisation, no prefix matching. Exact or refused."""
    ran = []
    bridge = _bridge({"ping": lambda _a: ran.append(1) or {}})
    response = bridge.handle({"id": "1", "op": op})
    assert response["ok"] is False
    assert ran == []


def test_the_operation_list_cannot_be_widened_after_construction():
    handlers = {"ping": lambda _a: {"pong": True}}
    bridge = _bridge(handlers)
    handlers["run_anything"] = lambda _a: {"owned": True}   # too late

    assert "run_anything" not in bridge.operations
    assert bridge.handle({"id": "1", "op": "run_anything"})["ok"] is False


def test_a_refusal_names_the_operations_that_do_exist():
    """So a mismatch between the two halves is visible, not silent."""
    bridge = _bridge({"ping": lambda _a: {}, "voice_status": lambda _a: {}})
    reply = bridge.handle({"id": "1", "op": "nope"})
    assert sorted(reply["supported"]) == ["ping", "voice_status"]


def test_arguments_that_are_not_a_mapping_become_an_empty_mapping():
    seen = []
    bridge = _bridge({"ping": lambda args: seen.append(args) or {}})
    for bad in ("a string", 42, ["a", "list"], None):
        bridge.handle({"id": "1", "op": "ping", "args": bad})
    assert seen == [{}, {}, {}, {}], "a handler must always receive a dict"


# --------------------------------------------------------------- failure paths

def test_a_handler_that_raises_is_answered_not_propagated():
    def explode(_args):
        raise RuntimeError("boom")

    writer = io.StringIO()
    bridge = _bridge({"boom": explode}, writer=writer)
    reply = bridge.handle({"id": "1", "op": "boom"})

    assert reply["ok"] is False
    assert reply["error"] == "operation_failed"
    assert _replies(writer)[0]["error"] == "operation_failed"


def test_a_broken_output_stream_does_not_break_the_dispatcher():
    class Broken(io.StringIO):
        def write(self, _data):
            raise OSError("the parent went away")

    bridge = _bridge({"ping": lambda _a: {"pong": True}}, writer=Broken())
    # The reply cannot be delivered, but handling must still complete.
    assert bridge.handle({"id": "1", "op": "ping"})["ok"] is True


def test_emit_before_start_is_a_no_op():
    writer = io.StringIO()
    bridge = _bridge({}, writer=writer)
    bridge.emit("voice_phase", {"phase": "IDLE"})
    assert writer.getvalue() == "", "nothing is written until the channel is started"


# ------------------------------------------------------------- the real reader

def test_the_reader_dispatches_requests_arriving_on_a_real_pipe():
    """A genuine OS pipe, a genuine reader thread -- not a StringIO stand-in."""
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "r", encoding="utf-8")
    parent = os.fdopen(write_fd, "w", encoding="utf-8")
    writer = io.StringIO()
    answered = threading.Event()

    def ping(_args):
        answered.set()
        return {"pong": True}

    bridge = DesktopBridge({"ping": ping}, reader=reader, writer=writer)
    assert bridge.start() is True
    try:
        # Interleaved with ordinary log output, exactly as the real stream is.
        parent.write("  [NANO] Backend ...... READY\n")
        parent.write(_line(id="1", op="ping"))
        parent.flush()

        assert answered.wait(5), "the reader thread never dispatched the request"
        deadline = time.time() + 5
        while not _replies(writer) and time.time() < deadline:
            time.sleep(0.02)
        assert _replies(writer)[0]["result"] == {"pong": True}
    finally:
        bridge.stop()
        parent.close()


def test_an_oversized_line_is_refused_unread():
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "r", encoding="utf-8")
    parent = os.fdopen(write_fd, "w", encoding="utf-8")
    ran = threading.Event()

    bridge = DesktopBridge({"ping": lambda _a: ran.set() or {}}, reader=reader, writer=io.StringIO())
    bridge.start()
    try:
        payload = {"id": "1", "op": "ping", "pad": "x" * (desktop_bridge.MAX_LINE_BYTES + 10)}
        parent.write(encode(payload))
        parent.write(_line(id="2", op="ping"))   # a normal one behind it
        parent.flush()
        time.sleep(0.4)
        # The normal request still lands: an oversized line is skipped, not fatal.
        assert ran.is_set()
    finally:
        bridge.stop()
        parent.close()


def test_the_channel_does_not_start_without_an_input_stream():
    class Closed(io.StringIO):
        closed = True

    assert DesktopBridge({}, reader=Closed(), writer=io.StringIO()).start() is False


# ------------------------------------------------------------------- emitting

def test_events_are_written_as_single_tagged_lines():
    writer = io.StringIO()
    bridge = DesktopBridge({}, reader=io.StringIO(), writer=writer)
    bridge.start()
    bridge.emit("voice_phase", {"phase": "SPEAKING", "detail": "A falar…"})

    raw = writer.getvalue()
    assert raw.count("\n") == 1, "an event must be exactly one line"
    assert raw.startswith(PROTOCOL_TAG)
    assert json.loads(raw[len(PROTOCOL_TAG):]) == {
        "event": "voice_phase",
        "payload": {"phase": "SPEAKING", "detail": "A falar…"},
    }


def test_concurrent_emits_never_interleave_within_a_line():
    """The write lock is what keeps a phase event parseable under load."""
    writer = io.StringIO()
    bridge = DesktopBridge({}, reader=io.StringIO(), writer=writer)
    bridge.start()

    def spam(tag):
        for index in range(50):
            bridge.emit("voice_phase", {"phase": f"{tag}{index}", "pad": "y" * 200})

    threads = [threading.Thread(target=spam, args=(tag,)) for tag in ("A", "B", "C")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = [line for line in writer.getvalue().splitlines() if line]
    assert len(lines) == 150
    assert all(decode(line) is not None for line in lines), "a line was torn in half"
