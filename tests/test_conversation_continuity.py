"""THE THREAD THAT STARTS A TURN OWNS THE WHOLE TURN.

The defect these lock out was silent and destructive. Nano answers on an event
loop, but the conversation rail (`open_conversation`, `create_conversation`)
is served on eel's bridge thread and moves the active-thread pointer the moment
the user clicks. Persistence used to resolve "which thread does this belong to"
LAZILY -- once when the question was stored and again when the answer finished
-- so a user who sent a message and then opened another conversation had the
reply written into whichever thread happened to be active at the end.

Measured before the fix, through the real `send_message` path:

    THREAD A: []
    THREAD B: [('user', 'pergunta na conversa A'), ('assistant', 'resposta')]

The whole turn moved. Thread A kept nothing; thread B, which the user had
merely glanced at, gained a question it was never asked. Reopening A showed an
empty conversation, and B's history was a lie.

These are behavioural tests. Each drives the real `core.main` code path against
a real MemoryStack over a throwaway SQLite database and reads back what the
database actually contains. Nothing greps source: the bug was not visible in
the source of any single line, only in the interleaving.

UI VISIBILITY AND PERSISTENCE ARE SEPARATE CONCERNS, and these tests only
assert the second. What the user is looking at while the answer streams is the
renderer's business; where the answer is STORED is not negotiable.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

import core.memory as memory_module
from core.memory import MemoryEngine
from core.memory_stack import MemoryStack


class RecordingBridge:
    """Stands in for the eel callbacks. Records nothing anyone asserts on here."""

    def __getattr__(self, name: str):
        def _record(*_args):
            return lambda *_cb: None
        return _record


class GatedBrain:
    """Streams a first chunk, then holds the turn open until the test says go.

    This is what makes these tests deterministic rather than sleep-and-hope:
    the switch happens at a point where the turn is provably mid-flight, not at
    a point some timing guess hoped it would be.
    """

    def __init__(self, first: str = "primeira ", last: str = "parte final"):
        self._first, self._last = first, last
        self.started = threading.Event()
        self.gate = threading.Event()
        self.last_metadata = {"task": "SMALL_TALK", "tier": "FAST", "mode": "CLOUD",
                              "provider": "groq", "model": "stub-model"}

    @property
    def answer(self) -> str:
        return (self._first + self._last).strip()

    async def chat(self, message: str, stream: bool = True):
        yield self._first
        self.started.set()
        while not self.gate.is_set():
            await asyncio.sleep(0.005)
        yield self._last

    def switch_conversation(self, conversation_id):
        return 0


class ExplodingBrain(GatedBrain):
    async def chat(self, message: str, stream: bool = True):
        yield self._first
        self.started.set()
        while not self.gate.is_set():
            await asyncio.sleep(0.005)
        raise RuntimeError("provider exploded mid-answer")


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """core.main wired to a real memory stack over a throwaway database.

    EVERYTHING here goes through monkeypatch, including `brain` and
    `_get_or_create_loop`, which the tests below replace. `core.main` is a
    module-level singleton shared by the whole session: an attribute assigned
    directly would outlive this file and hand the next test a stopped event
    loop or a closed database. That is not hypothetical -- it is exactly the
    kind of cross-file pollution this suite already suffers from.
    """
    import core.main as main

    monkeypatch.setattr(memory_module, "DB_PATH", tmp_path / "nano.db")
    engine = MemoryEngine()
    stack = MemoryStack(engine, background=False)

    monkeypatch.setattr(main, "memory_stack", stack)
    monkeypatch.setattr(main, "eel", RecordingBridge())
    monkeypatch.setattr(main, "_should_speak", lambda source: False)
    try:
        yield main, stack, monkeypatch
    finally:
        stack.stop()
        engine.close()


def _roles(stack, conversation_id) -> list[tuple[str, str]]:
    return [(row["role"], row["content"])
            for row in stack.conversations.messages(conversation_id, limit=50)]


def _two_threads(stack):
    first = stack.new_conversation("Conversa A")
    second = stack.new_conversation("Conversa B")
    stack.open_conversation(first["id"])
    return first["id"], second["id"]


# ====================================================== switching mid-answer

def test_a_reply_stays_in_the_thread_that_asked_it_after_the_user_switches(wired):
    """Scenario B: send in A, open B while the answer is still streaming."""
    main, stack, monkeypatch = wired
    a, b = _two_threads(stack)
    brain = GatedBrain()
    monkeypatch.setattr(main, "brain", brain)

    async def scenario():
        turn = asyncio.create_task(main._process_message("pergunta em A", msg_id="t"))
        while not brain.started.is_set():
            await asyncio.sleep(0.005)
        stack.open_conversation(b)          # the user clicks conversation B
        brain.gate.set()
        return await turn

    result = asyncio.run(scenario())

    assert result["ok"] is True
    assert _roles(stack, a) == [("user", "pergunta em A"), ("assistant", brain.answer)]
    assert _roles(stack, b) == [], "the answer leaked into the thread the user switched to"


def test_a_conversation_created_mid_answer_does_not_capture_the_reply(wired):
    """Scenario C: 'Nova conversa' clicked while an answer is still streaming.

    Worse than the switch case in practice: the user is presented with a brand
    new, supposedly empty conversation that already contains an answer to a
    question it does not hold.
    """
    main, stack, monkeypatch = wired
    a, _b = _two_threads(stack)
    brain = GatedBrain()
    monkeypatch.setattr(main, "brain", brain)
    created: dict[str, str] = {}

    async def scenario():
        turn = asyncio.create_task(main._process_message("pergunta em A", msg_id="t"))
        while not brain.started.is_set():
            await asyncio.sleep(0.005)
        created["id"] = stack.new_conversation("Nova")["id"]
        brain.gate.set()
        return await turn

    asyncio.run(scenario())

    assert _roles(stack, a) == [("user", "pergunta em A"), ("assistant", brain.answer)]
    assert _roles(stack, created["id"]) == [], "a fresh conversation was born holding an answer"


def test_deleting_the_asking_thread_mid_answer_does_not_push_the_reply_elsewhere(wired):
    """Scenario E: the thread is deleted while its answer is still streaming.

    Deleting released the active pointer, `ensure_active()` then resolved to
    whatever thread was most recent, and the orphaned answer surfaced there.
    The correct outcome is that the row is DROPPED: the answer was already
    delivered on screen, and the conversation it belonged to is gone.
    """
    main, stack, monkeypatch = wired
    a, b = _two_threads(stack)
    brain = GatedBrain()
    monkeypatch.setattr(main, "brain", brain)

    async def scenario():
        turn = asyncio.create_task(main._process_message("pergunta em A", msg_id="t"))
        while not brain.started.is_set():
            await asyncio.sleep(0.005)
        stack.delete_conversation(a)
        brain.gate.set()
        return await turn

    result = asyncio.run(scenario())

    assert result["ok"] is True, "the user must still see the answer that was produced"
    assert result["text"] == brain.answer
    assert _roles(stack, b) == [], "an answer from a deleted thread reappeared in another"


def test_a_failed_turn_switched_away_from_writes_nothing_into_the_new_thread(wired):
    """Scenario D: the provider fails while the user is reading another thread."""
    main, stack, monkeypatch = wired
    a, b = _two_threads(stack)
    brain = ExplodingBrain()
    monkeypatch.setattr(main, "brain", brain)

    async def scenario():
        turn = asyncio.create_task(main._process_message("pergunta em A", msg_id="t"))
        while not brain.started.is_set():
            await asyncio.sleep(0.005)
        stack.open_conversation(b)
        brain.gate.set()
        return await turn

    result = asyncio.run(scenario())

    assert result["ok"] is False
    assert _roles(stack, a) == [("user", "pergunta em A")]
    assert _roles(stack, b) == []


# ================================================ the real send_message path

def test_the_turn_belongs_to_the_thread_that_was_open_when_enter_was_pressed(wired):
    """Scenario F, through the production path, and the most damaging case.

    `send_message` returns an ACK and the answer is produced on a loop running
    on ANOTHER thread. If the rail click lands before that coroutine gets its
    first slice, lazy resolution moved the QUESTION as well as the answer, and
    the asking thread was left completely empty.

    The loop here is a real loop on a real second thread, which is the only way
    to reproduce the interleaving `send_message` actually lives in.
    """
    main, stack, monkeypatch = wired
    a, b = _two_threads(stack)
    brain = GatedBrain()
    monkeypatch.setattr(main, "brain", brain)

    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _run():
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    ready.wait(timeout=5)
    monkey_loop = loop
    monkeypatch.setattr(main, "_get_or_create_loop", lambda: monkey_loop)

    try:
        ack = main.send_message("pergunta em A", "turn-f")
        assert ack["accepted"] is True

        # The rail click happens on the bridge thread, exactly as eel serves it,
        # and deliberately BEFORE the turn is known to have started.
        main.open_conversation(b)

        brain.gate.set()
        assert brain.started.wait(timeout=10), "the turn never started"
        deadline = threading.Event()
        for _ in range(400):                       # up to ~4s, no fixed sleep
            if len(_roles(stack, a)) == 2 or _roles(stack, b):
                break
            deadline.wait(0.01)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        worker.join(timeout=5)

    assert _roles(stack, a) == [("user", "pergunta em A"), ("assistant", brain.answer)]
    assert _roles(stack, b) == [], "the entire turn was filed into the thread the user opened"


# ============================================================== spoken turns

def test_a_spoken_turn_keeps_its_question_and_its_answer_in_one_thread(wired):
    """A voice turn is persisted in two calls. The rail can move between them.

    The window is narrow but real: `_emit_voice_exchange` writes the question
    and then the answer, and the bridge thread can serve `open_conversation`
    in between. Splitting a spoken turn across two conversations is the same
    defect wearing a different hat, so it is closed the same way -- one owner,
    resolved once. The switch is injected exactly in that window rather than
    raced for, so the test measures the guarantee instead of the timing.
    """
    main, stack, monkeypatch = wired
    a, b = _two_threads(stack)
    monkeypatch.setattr(main, "brain", GatedBrain())

    real_append = stack.conversations.append
    moved: list[str] = []

    def append_then_switch(conversation_id, role, content, **kwargs):
        stored = real_append(conversation_id, role, content, **kwargs)
        if role == "user" and not moved:
            moved.append("yes")
            stack.open_conversation(b)      # the user clicks away mid-turn
        return stored

    stack.conversations.append = append_then_switch
    try:
        main._emit_voice_exchange("voice-1", "pergunta falada", "resposta falada")
    finally:
        stack.conversations.append = real_append

    assert moved == ["yes"], "the switch was never injected; the test proves nothing"
    assert _roles(stack, a) == [("user", "pergunta falada"),
                                ("assistant", "resposta falada")]
    assert _roles(stack, b) == [], "a spoken turn was split across two conversations"


# ================================================================== baseline

def test_an_undisturbed_turn_is_still_stored_in_order_exactly_once(wired):
    """The fix must not change the ordinary case."""
    main, stack, monkeypatch = wired
    a, b = _two_threads(stack)
    brain = GatedBrain()
    brain.gate.set()
    monkeypatch.setattr(main, "brain", brain)

    asyncio.run(main._process_message("olá", msg_id="t"))

    assert _roles(stack, a) == [("user", "olá"), ("assistant", brain.answer)]
    assert _roles(stack, b) == []


# ============================================ resolving the owner is not free

def test_a_failing_memory_store_never_costs_the_user_their_message(wired):
    """Owner resolution runs on the bridge thread and touches SQLite.

    `send_message` is an eel endpoint: an exception raised here does not become
    an error result, it becomes NO ACK AT ALL, and the UI reports "Motor
    offline" while the model is perfectly healthy. A locked database must
    therefore degrade to "no explicit owner" -- the pre-existing behaviour --
    and never to a dropped turn.
    """
    main, _stack, monkeypatch = wired

    class Locked:
        ready = True

        def ensure_active(self):
            raise RuntimeError("database is locked")

    dispatched: list[str | None] = []

    def _capture(coro, _loop):
        dispatched.append(coro.cr_frame.f_locals.get("conversation_id"))
        coro.close()

    monkeypatch.setattr(main, "memory_stack", Locked())
    monkeypatch.setattr(main, "_get_or_create_loop", lambda: None)
    monkeypatch.setattr(main.asyncio, "run_coroutine_threadsafe", _capture)

    ack = main.send_message("olá", "turn-locked")

    assert ack["ok"] is True and ack["accepted"] is True, (
        "a memory failure was allowed to swallow the user's message")
    assert dispatched == [None], "the turn should dispatch with no explicit owner"


def test_a_healthy_store_passes_the_resolved_owner_to_the_turn(wired):
    """The other half: when it works, the id really does travel with the turn."""
    main, stack, monkeypatch = wired
    thread = stack.new_conversation("A")

    dispatched: list[str | None] = []

    def _capture(coro, _loop):
        dispatched.append(coro.cr_frame.f_locals.get("conversation_id"))
        coro.close()

    monkeypatch.setattr(main, "_get_or_create_loop", lambda: None)
    monkeypatch.setattr(main.asyncio, "run_coroutine_threadsafe", _capture)

    main.send_message("olá", "turn-ok")

    assert dispatched == [thread["id"]], (
        "send_message did not hand the turn the thread that was open")
