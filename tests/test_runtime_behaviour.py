"""Behavioural tests for the runtime, not for the spelling of the source.

The suite is strong on regression coverage but a large block of it asserts on
the TEXT of source files -- ``assert "ack?.accepted" in source``. Those tests
pin an implementation's spelling: they pass when the component is broken but
the string survives, and fail on a harmless rename. They document past bugs
well and are worth keeping; they are not evidence that anything works.

These tests exercise real behaviour instead:

* the full ACK -> streaming chunks -> final response contract, driven through
  a stubbed provider, with the event loop's responsiveness measured while it
  runs;
* the execution authority's promise that a slow synchronous tool handler does
  not occupy the loop (audit H1);
* the permission manager's one-shot grant lifetime (audit H2);
* the task store's bounds under repeated updates (audit H3);
* the voice turn's non-blocking re-entrancy guard.

Nothing here touches the network, the microphone, or the user's data directory.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ===========================================================================
#  Event-loop responsiveness
# ===========================================================================

class LoopWatcher:
    """Measures whether an event loop kept running during an awaited call.

    A blocked loop cannot schedule anything, so a heartbeat that should tick
    every 10 ms ticks zero times. That is the whole signal, and it is the one
    property source-text assertions can never establish.
    """

    def __init__(self, interval: float = 0.01):
        self.interval = interval
        self.ticks = 0
        self.max_gap = 0.0
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "LoopWatcher":
        async def _beat() -> None:
            last = time.monotonic()
            while True:
                await asyncio.sleep(self.interval)
                now = time.monotonic()
                self.max_gap = max(self.max_gap, now - last)
                last = now
                self.ticks += 1

        self._task = asyncio.create_task(_beat())
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


# ===========================================================================
#  H1 -- a slow synchronous tool handler must not block the event loop
# ===========================================================================

BLOCKING_SECONDS = 0.6


def _executor_with_slow_tool(tmp_path, seconds: float = BLOCKING_SECONDS):
    from core.permission_manager import PermissionManager
    from core.tool_execution import RetryPolicy, ToolExecutor

    manager = PermissionManager(
        confirmation_callback=lambda *_: True,
        policy_store_path=tmp_path / "permissions.json",
    )
    executor = ToolExecutor(permission_manager=manager)

    calls: list[str] = []

    def slow_handler(args: dict) -> dict:
        # Deliberately synchronous and deliberately slow: this is what every
        # plugin handler and every subprocess/httpx built-in looks like.
        calls.append("start")
        time.sleep(seconds)
        calls.append("end")
        return {"ok": True, "success": True}

    executor.register_tool(
        "test.slow_sync",
        "A slow synchronous handler.",
        {"type": "object", "properties": {}},
        handler=slow_handler,
        risk="low",
        timeout=30,
        retry_policy=RetryPolicy.SAFE_TO_RETRY,
        capabilities=["project.inspect"],
    )
    return executor, calls


def test_a_slow_sync_tool_handler_does_not_block_the_event_loop(tmp_path):
    """H1: execute_tool_async must off-load synchronous handlers.

    Before the fix the handler body ran inline in the coroutine, so the loop
    stopped dead for the handler's whole duration -- up to shell.execute's
    180 s ceiling -- taking streamed chunks, eel callbacks and confirmation
    dialogs down with it.
    """
    executor, calls = _executor_with_slow_tool(tmp_path)

    async def scenario():
        async with LoopWatcher() as watcher:
            result = await executor.execute_tool_async("test.slow_sync", {})
        return result, watcher

    result, watcher = asyncio.run(scenario())

    assert result["success"] is True, result
    assert calls == ["start", "end"], "the handler did not run to completion"
    # The loop should have ticked ~60 times during a 0.6 s handler. Anything
    # above a handful proves it was never blocked.
    assert watcher.ticks > 10, (
        f"the event loop only ticked {watcher.ticks} times during a "
        f"{BLOCKING_SECONDS}s synchronous handler -- it was blocked"
    )
    assert watcher.max_gap < BLOCKING_SECONDS / 2, (
        f"the loop stalled for {watcher.max_gap:.2f}s in one go"
    )


def test_the_per_tool_timeout_actually_fires(tmp_path):
    """H1, second half: the declared timeout was decorative.

    asyncio.wait_for cannot fire while the loop is blocked, because a timeout
    callback has to be scheduled on the loop it is timing. With the handler
    off-thread the timeout becomes real.
    """
    executor, _calls = _executor_with_slow_tool(tmp_path, seconds=5.0)
    executor.registry["test.slow_sync"]["timeout"] = 0.3

    async def scenario():
        # Measured INSIDE the loop. The orphaned worker keeps running -- Python
        # cannot interrupt a thread -- so process teardown would still wait for
        # it; what matters is that the caller was released on time.
        started = time.monotonic()
        result = await executor.execute_tool_async("test.slow_sync", {})
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(scenario())

    assert result["success"] is False
    assert result["error"] == "tool_timeout", result
    assert elapsed < 2.0, f"the timeout took {elapsed:.1f}s to fire, so it did not fire"


def test_tool_handlers_do_not_run_on_the_shared_default_executor(tmp_path):
    """An orphaned tool worker must not delay unrelated asyncio.to_thread work.

    A timed-out handler keeps its thread. On asyncio's shared default executor
    that orphan would sit in the same pool every other to_thread caller uses,
    and would hold up interpreter shutdown while the loop joined it.
    """
    executor, _calls = _executor_with_slow_tool(tmp_path, seconds=1.5)
    executor.registry["test.slow_sync"]["timeout"] = 0.2

    async def scenario():
        await executor.execute_tool_async("test.slow_sync", {})   # leaves an orphan
        started = time.monotonic()
        # Unrelated off-thread work must not be stuck behind it.
        await asyncio.to_thread(lambda: None)
        return time.monotonic() - started

    elapsed = asyncio.run(scenario())
    assert elapsed < 0.5, (
        f"unrelated to_thread work waited {elapsed:.2f}s behind an orphaned tool worker"
    )


def test_two_slow_tools_run_concurrently_rather_than_in_series(tmp_path):
    """Brain.chat gathers tool calls; gathering inline serialised them."""
    executor, _calls = _executor_with_slow_tool(tmp_path)

    async def scenario():
        started = time.monotonic()
        await asyncio.gather(
            executor.execute_tool_async("test.slow_sync", {}),
            executor.execute_tool_async("test.slow_sync", {}),
        )
        return time.monotonic() - started

    elapsed = asyncio.run(scenario())
    assert elapsed < BLOCKING_SECONDS * 1.8, (
        f"two {BLOCKING_SECONDS}s tools took {elapsed:.2f}s -- they ran in series"
    )


# ===========================================================================
#  The bridge contract: ACK -> chunks -> final response
# ===========================================================================

class StubBrain:
    """A provider-shaped stub. Streams real chunks with real awaits."""

    def __init__(self, chunks, *, status: str | None = None, delay: float = 0.01):
        self._chunks = list(chunks)
        self._status = status
        self._delay = delay
        self.last_metadata = {
            "task": "SMALL_TALK", "tier": "FAST", "mode": "CLOUD",
            "provider": "groq", "model": "stub-model", "tools_offered": 0,
        }
        self.conversation: list[dict] = []

    async def chat(self, message: str, stream: bool = True):
        self.conversation.append({"role": "user", "content": message})
        if self._status:
            yield f"_thinking_:{self._status}"
        for chunk in self._chunks:
            await asyncio.sleep(self._delay)
            yield chunk
        self.conversation.append({"role": "assistant", "content": "".join(self._chunks)})


class RecordingBridge:
    """Stands in for the eel callbacks and records the event sequence."""

    def __init__(self):
        self.events: list[tuple[str, tuple]] = []

    def __getattr__(self, name: str):
        def _record(*args):
            self.events.append((name, args))
            # eel's real callback form: calling the returned object sends it.
            return lambda *_cb: None
        return _record

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def payloads(self, name: str) -> list[tuple]:
        return [args for event, args in self.events if event == name]


@pytest.fixture()
def message_pipeline(monkeypatch, tmp_path):
    """core.main's _process_message, wired to a stub brain and a fake bridge."""
    import core.main as main

    bridge = RecordingBridge()
    monkeypatch.setattr(main, "eel", bridge)

    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(main.memory, "save_message",
                        lambda role, content, *a, **k: saved.append((role, content)))
    # Never speak during a test.
    monkeypatch.setattr(main, "_should_speak", lambda source: False)
    return main, bridge, saved


def test_a_typed_turn_emits_start_then_chunks_then_end(message_pipeline, monkeypatch):
    """The whole streaming contract, end to end, through the real code path.

    send_message returns an ACK and the answer arrives on the stream. This is
    the contract three separate source-string tests describe; this one runs it.
    """
    main, bridge, saved = message_pipeline
    monkeypatch.setattr(main, "brain", StubBrain(["Olá", "! ", "Como ", "estás?"]))

    result = asyncio.run(main._process_message("Olá", msg_id="turn-1"))

    assert result["ok"] is True
    assert result["text"] == "Olá! Como estás?"
    assert result["msg_id"] == "turn-1"

    names = bridge.names()
    assert names[0] == "on_stream_start", names
    assert names[-1] == "on_stream_end", names
    assert names.count("on_stream_chunk") == 4, names

    # The chunks arrive in order and reassemble into the answer.
    streamed = "".join(args[1] for args in bridge.payloads("on_stream_chunk"))
    assert streamed == "Olá! Como estás?"

    # Both sides of the turn are persisted exactly once.
    assert saved == [("user", "Olá"), ("assistant", "Olá! Como estás?")]

    # The final payload carries safe diagnostics and no credential.
    final = bridge.payloads("on_stream_end")[0][1]
    assert final["meta"]["provider"] == "groq"
    assert not any("gsk_" in str(v) for v in final["meta"].values())


def test_thinking_updates_arrive_as_status_not_as_answer_text(message_pipeline, monkeypatch):
    """A _thinking_ marker is a status line; it must never land in the answer."""
    main, bridge, _saved = message_pipeline
    monkeypatch.setattr(main, "brain",
                        StubBrain(["pronto"], status="⚙️ system_stats..."))

    result = asyncio.run(main._process_message("estado", msg_id="turn-2"))

    assert result["text"] == "pronto"
    assert "_thinking_" not in result["text"]
    assert bridge.payloads("on_stream_status")[0][1] == "⚙️ system_stats..."
    assert result["status"] == ["⚙️ system_stats..."]


def test_the_ui_stays_responsive_while_a_turn_streams(message_pipeline, monkeypatch):
    """A streaming turn must not monopolise the loop the bridge runs on."""
    main, bridge, _saved = message_pipeline
    monkeypatch.setattr(main, "brain", StubBrain(["a", "b", "c", "d", "e"], delay=0.05))

    async def scenario():
        async with LoopWatcher() as watcher:
            await main._process_message("olá", msg_id="turn-3")
        return watcher

    watcher = asyncio.run(scenario())
    assert watcher.ticks > 10, (
        f"the loop only ticked {watcher.ticks} times while a turn was streaming"
    )


def test_a_failing_turn_reports_an_error_and_still_closes_the_stream(message_pipeline, monkeypatch):
    """A model failure must produce an error event AND a stream end.

    A stream that starts and never ends leaves the UI spinning forever.
    """
    main, bridge, _saved = message_pipeline

    class ExplodingBrain(StubBrain):
        async def chat(self, message, stream=True):
            yield "part"
            raise RuntimeError("provider exploded")

    monkeypatch.setattr(main, "brain", ExplodingBrain([]))

    result = asyncio.run(main._process_message("olá", msg_id="turn-4"))

    assert result["ok"] is False
    names = bridge.names()
    assert "on_stream_error" in names, names
    assert names[-1] == "on_stream_end", "the stream was left open after a failure"


def test_send_message_returns_an_ack_before_the_answer_exists(monkeypatch):
    """The ACK is a transport receipt, not the reply.

    Blocking here is what made a slow turn look like "Motor offline" while the
    backend was healthy and still working.
    """
    import core.main as main

    dispatched: list[str] = []
    monkeypatch.setattr(main.asyncio, "run_coroutine_threadsafe",
                        lambda coro, loop: (coro.close(), dispatched.append("dispatched"))[1])

    ack = main.send_message("Olá", "turn-5")

    assert ack == {"ok": True, "accepted": True, "request_id": "turn-5", "msg_id": "turn-5"}
    assert dispatched == ["dispatched"]


def test_an_empty_message_is_rejected_without_dispatching(monkeypatch):
    import core.main as main

    dispatched: list[str] = []
    monkeypatch.setattr(main.asyncio, "run_coroutine_threadsafe",
                        lambda coro, loop: dispatched.append("dispatched"))

    assert main.send_message("   ")["accepted"] is False
    assert dispatched == []


# ===========================================================================
#  H2 -- one-shot permission grants expire
# ===========================================================================

def _grant_once(manager, capability="filesystem.write", path="tmp/a.txt"):
    request_id = manager.request_permission(
        capability, {"path": path}, target=path, reason="test")
    assert manager.resolve_permission(request_id, "allow_once")["ok"] is True


def test_an_allow_once_grant_authorises_exactly_one_execution(tmp_path):
    from core.permission_manager import PermissionManager

    manager = PermissionManager(confirmation_callback=lambda *_: False,
                                policy_store_path=tmp_path / "p.json")
    _grant_once(manager)
    assert manager.ask_for_confirmation("filesystem.write", {"path": "tmp/a.txt"}) is True
    assert manager.ask_for_confirmation("filesystem.write", {"path": "tmp/a.txt"}) is False


def test_an_unconsumed_allow_once_grant_expires(tmp_path, monkeypatch):
    """H2: an approval the user gave must not outlive the moment they gave it.

    A grant that was approved but never consumed used to survive for the
    lifetime of the process and silently authorise the next identical call.
    """
    from core import permission_manager as pm

    manager = pm.PermissionManager(confirmation_callback=lambda *_: False,
                                   policy_store_path=tmp_path / "p.json")
    clock = {"now": 1000.0}
    monkeypatch.setattr(pm.PermissionManager, "_monotonic",
                        staticmethod(lambda: clock["now"]))

    _grant_once(manager)
    # Just before the deadline it is still good.
    clock["now"] += pm.ONCE_GRANT_TTL_SECONDS - 1
    assert manager._has_execution_grant("filesystem.write", {"path": "tmp/a.txt"}) is True

    _grant_once(manager)   # re-grant, then let it go stale
    clock["now"] += pm.ONCE_GRANT_TTL_SECONDS + 1
    assert manager._has_execution_grant("filesystem.write", {"path": "tmp/a.txt"}) is False
    assert manager.ask_for_confirmation("filesystem.write", {"path": "tmp/a.txt"}) is False


def test_an_expired_grant_is_dropped_and_audited(tmp_path, monkeypatch):
    """Expiry must not leave the collection growing without bound."""
    from core import permission_manager as pm

    manager = pm.PermissionManager(confirmation_callback=lambda *_: False,
                                   policy_store_path=tmp_path / "p.json")
    clock = {"now": 500.0}
    monkeypatch.setattr(pm.PermissionManager, "_monotonic",
                        staticmethod(lambda: clock["now"]))

    for index in range(5):
        _grant_once(manager, path=f"tmp/file-{index}.txt")
    assert len(manager._once_grants) == 5

    clock["now"] += pm.ONCE_GRANT_TTL_SECONDS + 1
    manager._purge_expired_once_grants()
    assert manager._once_grants == {}
    assert "PermissionGrantExpired" in [e["event"] for e in manager.get_audit_log()]


def test_a_grant_never_authorises_a_different_target(tmp_path):
    """Identity is capability + target; approving one file approves one file."""
    from core.permission_manager import PermissionManager

    manager = PermissionManager(confirmation_callback=lambda *_: False,
                                policy_store_path=tmp_path / "p.json")
    _grant_once(manager, path="tmp/approved.txt")
    assert manager._has_execution_grant("filesystem.write", {"path": "tmp/other.txt"}) is False
    assert manager._has_execution_grant("filesystem.delete", {"path": "tmp/approved.txt"}) is False


def test_ttl_expiry_does_not_weaken_task_grants(tmp_path, monkeypatch):
    """ALLOW_FOR_TASK is bounded by its TASK, not by the one-shot clock."""
    from core import permission_manager as pm

    manager = pm.PermissionManager(confirmation_callback=lambda *_: False,
                                   policy_store_path=tmp_path / "p.json")
    clock = {"now": 10.0}
    monkeypatch.setattr(pm.PermissionManager, "_monotonic",
                        staticmethod(lambda: clock["now"]))

    request_id = manager.request_permission(
        "filesystem.write", {"path": "tmp/task.txt"},
        task_id="task-1", target="tmp/task.txt", reason="test")
    assert manager.resolve_permission(request_id, "allow_for_task")["ok"] is True

    clock["now"] += pm.ONCE_GRANT_TTL_SECONDS * 10
    assert manager._has_execution_grant(
        "filesystem.write", {"path": "tmp/task.txt"}, task_id="task-1") is True
    assert manager.release_task_grants("task-1") == 1
    assert manager._has_execution_grant(
        "filesystem.write", {"path": "tmp/task.txt"}, task_id="task-1") is False


def test_persistent_allow_is_still_refused(tmp_path):
    """The TTL work must not have opened a persistent-permission door."""
    from core.permission_manager import PermissionManager

    manager = PermissionManager(confirmation_callback=lambda *_: False,
                                policy_store_path=tmp_path / "p.json")
    request_id = manager.request_permission(
        "filesystem.write", {"path": "tmp/a.txt"}, target="tmp/a.txt", reason="t")
    for decision in ("allow", "allow_persistent"):
        assert manager.resolve_permission(request_id, decision)["ok"] is False


# ===========================================================================
#  H3 -- task metadata and result stay bounded across repeated updates
# ===========================================================================

def _engine(tmp_path):
    from core.task_engine import TaskEngine

    return TaskEngine(tmp_path / "tasks.db")


def test_repeated_updates_cannot_grow_metadata_without_bound(tmp_path):
    """H3: update_task merges, so it is the write path that actually grows.

    The historic ~1.5 GB task database came from metadata that embedded its own
    previous value. create_task was capped; update_task was not, and update_task
    is the one that accumulates.
    """
    import json

    from core.task_engine import MAX_METADATA_BYTES

    engine = _engine(tmp_path)
    task_id = engine.create_task("grow", metadata={"seed": "x"})["id"]

    for index in range(30):
        current = engine.get_task(task_id)
        engine.update_task(task_id, metadata={
            "a": current["metadata"],       # the recursive shape
            "b": current["metadata"],
            "blob": "y" * 2000,
            "i": index,
        })

    stored = engine.get_task(task_id)["metadata"]
    encoded = len(json.dumps(stored, ensure_ascii=False).encode("utf-8"))
    assert encoded <= MAX_METADATA_BYTES, f"metadata grew to {encoded} bytes"

    db_bytes = (tmp_path / "tasks.db").stat().st_size
    assert db_bytes < 4 * 1024 * 1024, f"the task database grew to {db_bytes} bytes"


def test_a_large_result_is_bounded_but_keeps_its_small_keys(tmp_path):
    import json

    from core.task_engine import MAX_RESULT_BYTES

    engine = _engine(tmp_path)
    task_id = engine.create_task("res")["id"]
    engine.mark_complete(task_id, {
        "task_id": task_id,
        "status": "completed",
        "steps": [{"output": "z" * 20000} for _ in range(50)],
    })

    result = engine.get_task(task_id)["result"]
    encoded = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    assert encoded <= MAX_RESULT_BYTES
    assert result["_result_truncated"] is True, "truncation must be visible, not silent"
    assert result["task_id"] == task_id, "small useful keys must survive the trim"
    assert result["status"] == "completed"


def test_a_reference_cycle_in_metadata_does_not_raise(tmp_path):
    """json.dumps raises ValueError on a cycle; the depth prune breaks it first."""
    engine = _engine(tmp_path)
    task_id = engine.create_task("cyc")["id"]

    cyclic: dict = {"name": "loop"}
    cyclic["self"] = cyclic

    engine.update_task(task_id, metadata=cyclic)   # must not raise
    stored = engine.get_task(task_id)["metadata"]
    assert stored.get("name") == "loop"


def test_ordinary_metadata_is_stored_byte_for_byte(tmp_path):
    """Bounding must never corrupt data that was always within bounds."""
    engine = _engine(tmp_path)
    payload = {"plan": {"task_type": "instant", "steps": [1, 2, 3]}, "agent": "DesktopAgent"}
    task_id = engine.create_task("ok", metadata=payload)["id"]
    engine.update_task(task_id, metadata={"extra": ["a", "b"]})

    stored = engine.get_task(task_id)["metadata"]
    assert stored["plan"] == payload["plan"]
    assert stored["agent"] == "DesktopAgent"
    assert stored["extra"] == ["a", "b"]
    assert "_metadata_truncated" not in stored


# ===========================================================================
#  Voice turn -- non-blocking re-entrancy
# ===========================================================================

def _runtime():
    from core.voice import VoiceEngine, VoiceRuntime

    engine = VoiceEngine({"enabled": True})
    return VoiceRuntime(engine, config={"voice": {}})


def test_a_second_voice_turn_is_refused_immediately(monkeypatch):
    """Two triggers must never both hold the microphone.

    The guard is non-blocking on purpose: a queued second turn would start
    later, against a command the user is no longer speaking.
    """
    runtime = _runtime()
    monkeypatch.setattr(runtime, "_take_microphone", lambda: True)

    started = asyncio.Event()

    async def slow_turn(**_kwargs):
        started.set()
        await asyncio.sleep(0.3)
        return {"ok": True, "response": "pronto", "transcript": "olá"}

    monkeypatch.setattr(runtime, "process_wake_word_turn", slow_turn)
    monkeypatch.setattr(runtime, "speak_response", lambda *_a, **_k: _async_true())

    async def scenario():
        first = asyncio.create_task(runtime.run_voice_turn("hotkey", chime=False))
        await started.wait()
        elapsed = time.monotonic()
        second = await runtime.run_voice_turn("wake_phrase", chime=False)
        elapsed = time.monotonic() - elapsed
        return await first, second, elapsed

    first, second, elapsed = asyncio.run(scenario())

    assert first["ok"] is True
    assert second["busy"] is True
    assert second["error"] == "voice_turn_in_progress"
    assert second["active_source"] == "hotkey"
    assert elapsed < 0.2, f"the second trigger waited {elapsed:.2f}s instead of failing fast"


async def _async_true():
    return True


def test_the_turn_lock_is_released_after_a_failure(monkeypatch):
    """A crashed turn must not wedge voice for the rest of the session."""
    runtime = _runtime()
    monkeypatch.setattr(runtime, "_take_microphone", lambda: True)

    async def exploding(**_kwargs):
        raise RuntimeError("capture died")

    monkeypatch.setattr(runtime, "process_wake_word_turn", exploding)

    first = asyncio.run(runtime.run_voice_turn("hotkey", chime=False))
    assert first["ok"] is False
    assert runtime.turn_status()["active"] is False

    async def fine(**_kwargs):
        return {"ok": True, "response": "", "transcript": ""}

    monkeypatch.setattr(runtime, "process_wake_word_turn", fine)
    second = asyncio.run(runtime.run_voice_turn("hotkey", chime=False))
    assert second.get("busy") is not True, "the lock was never released"


def test_every_trigger_runs_the_same_choreography(monkeypatch):
    """The hotkey must reuse the wake phrase's turn, not a copy of it."""
    runtime = _runtime()
    monkeypatch.setattr(runtime, "_take_microphone", lambda: True)

    async def turn(**_kwargs):
        return {"ok": True, "response": "olá", "transcript": "diz olá"}

    monkeypatch.setattr(runtime, "process_wake_word_turn", turn)
    monkeypatch.setattr(runtime, "speak_response", lambda *_a, **_k: _async_true())

    sequences: dict[str, list[str]] = {}
    for source in ("wake_phrase", "hotkey", "ui"):
        phases: list[str] = []
        runtime.set_observer(on_phase=lambda p, d="", acc=phases: acc.append(p))
        asyncio.run(runtime.run_voice_turn(source, chime=False))
        sequences[source] = phases

    assert sequences["hotkey"] == sequences["ui"], sequences
    # The wake-phrase turn is identical except that it does not re-take a
    # microphone it is already holding.
    assert sequences["wake_phrase"] == sequences["hotkey"], sequences
    for source, phases in sequences.items():
        assert "COMMAND_LISTENING" in phases, (source, phases)
        assert "SPEAKING" in phases, (source, phases)


def test_an_unknown_trigger_falls_back_to_ui_rather_than_running_unlabelled(monkeypatch):
    runtime = _runtime()
    monkeypatch.setattr(runtime, "_take_microphone", lambda: True)

    async def turn(**_kwargs):
        return {"ok": True, "response": "", "transcript": ""}

    monkeypatch.setattr(runtime, "process_wake_word_turn", turn)
    result = asyncio.run(runtime.run_voice_turn("nonsense", chime=False))
    assert result["source"] == "ui"


def test_a_turn_that_cannot_take_the_microphone_gives_up_cleanly(monkeypatch):
    """Better to refuse than to become a second reader on one PortAudio stream."""
    runtime = _runtime()
    monkeypatch.setattr(runtime, "_take_microphone", lambda: False)

    captured: list[str] = []

    async def turn(**_kwargs):
        captured.append("captured")
        return {"ok": True}

    monkeypatch.setattr(runtime, "process_wake_word_turn", turn)
    result = asyncio.run(runtime.run_voice_turn("hotkey", chime=False))

    assert result["ok"] is False
    assert result["error"] == "microphone_busy"
    assert captured == [], "the turn captured audio it had not been granted"
    assert runtime.turn_status()["active"] is False


# ===========================================================================
#  Stop semantics
# ===========================================================================

def test_stopping_playback_leaves_the_wake_detector_alive():
    """Pressing Stop must not permanently disable voice for the session."""
    from core.voice import VoiceEngine

    engine = VoiceEngine({"enabled": True})
    stopped: list[str] = []
    engine.wake_phrase_provider.stop = lambda: stopped.append("wake_phrase")
    engine.wake_word_provider.stop = lambda: stopped.append("wake_word")
    engine.tts_provider.stop = lambda: stopped.append("tts")

    engine.stop_playback()
    assert stopped == ["tts"], f"Stop tore down more than playback: {stopped}"

    engine.shutdown()
    assert "wake_phrase" in stopped, "shutdown must still take the subsystem down"


# ===========================================================================
#  Data migration -- conservative by construction
# ===========================================================================

def _seed(directory, **files):
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (directory / name).write_bytes(content if isinstance(content, bytes)
                                       else content.encode("utf-8"))
    return directory


def test_data_is_rescued_when_the_canonical_directory_is_empty(tmp_path, monkeypatch):
    """The case that matters: a new interpreter sees an empty data directory.

    Under Store Python, writes to %LOCALAPPDATA% are redirected into a package
    cache. Run the same Nano under a normal Python and the canonical directory
    is empty, so Nano looks freshly installed and the stored key is "gone".
    """
    from core import data_migration as dm

    old = _seed(tmp_path / "legacy",
                **{"secrets.dat": b"\x01\x02encrypted",
                   "user_settings.json": '{"provider_mode": "CLOUD"}',
                   "helios.db": b"sqlite-ish"})
    new = tmp_path / "canonical"
    monkeypatch.setattr(dm, "legacy_candidates", lambda _destination: [old])

    summary = dm.migrate_user_data(new)

    assert summary["status"] == "migrated"
    assert sorted(item["file"] for item in summary["copied"]) == [
        "helios.db", "secrets.dat", "user_settings.json"]
    assert (new / "secrets.dat").read_bytes() == b"\x01\x02encrypted"
    # The source is never deleted: if anything here is wrong the data is still
    # in both places.
    assert (old / "secrets.dat").exists()


def test_migration_never_overwrites_the_destination(tmp_path, monkeypatch):
    """Never overwrite newer destination data blindly."""
    from core import data_migration as dm

    old = _seed(tmp_path / "legacy", **{"user_settings.json": '{"provider_mode": "LOCAL"}'})
    new = _seed(tmp_path / "canonical", **{"user_settings.json": '{"provider_mode": "CLOUD"}'})
    monkeypatch.setattr(dm, "legacy_candidates", lambda _destination: [old])

    summary = dm.migrate_user_data(new)

    assert summary["status"] == "destination_already_populated"
    assert summary["copied"] == []
    assert (new / "user_settings.json").read_text(encoding="utf-8") == '{"provider_mode": "CLOUD"}'


def test_migration_runs_once(tmp_path, monkeypatch):
    from core import data_migration as dm

    old = _seed(tmp_path / "legacy", **{"helios.db": b"data"})
    new = tmp_path / "canonical"
    monkeypatch.setattr(dm, "legacy_candidates", lambda _destination: [old])

    assert dm.migrate_user_data(new)["status"] == "migrated"
    assert dm.migrate_user_data(new)["status"] == "already_done"


def test_an_empty_receipt_does_not_suppress_the_migration(tmp_path, monkeypatch):
    """A zero-byte receipt must not read as "already done".

    Presence alone is too weak a signal -- anything that touches the path would
    otherwise disable the rescue permanently.
    """
    from core import data_migration as dm

    old = _seed(tmp_path / "legacy", **{"helios.db": b"data"})
    new = tmp_path / "canonical"
    new.mkdir(parents=True)
    (new / dm.RECEIPT_NAME).write_text("", encoding="utf-8")
    monkeypatch.setattr(dm, "legacy_candidates", lambda _destination: [old])

    assert dm.migrate_user_data(new)["status"] == "migrated"


def test_describe_does_not_disturb_the_receipt(tmp_path, monkeypatch):
    """The write-probe must not be the receipt file."""
    from core import data_migration as dm

    monkeypatch.setattr(dm, "legacy_candidates", lambda _destination: [])
    monkeypatch.setitem(sys.modules, "core.app_paths",
                        type(sys)("core.app_paths"))
    sys.modules["core.app_paths"].DATA_DIR = tmp_path / "canonical"

    dm.describe_data_location()
    assert not (tmp_path / "canonical" / dm.RECEIPT_NAME).exists(), (
        "describe_data_location created a receipt and disabled the migration"
    )
    assert not (tmp_path / "canonical" / dm._PROBE_NAME).exists(), "the probe was left behind"


def test_migration_reports_without_leaking_a_secret(tmp_path, monkeypatch):
    from core import data_migration as dm

    old = _seed(tmp_path / "legacy", **{"secrets.dat": b"gsk_super_secret_value"})
    new = tmp_path / "canonical"
    monkeypatch.setattr(dm, "legacy_candidates", lambda _destination: [old])

    summary = dm.migrate_user_data(new)
    blob = json.dumps(summary)
    assert "gsk_" not in blob, "the migration summary carried secret material"
    assert summary["copied"][0] == {"file": "secrets.dat", "bytes": 22}
