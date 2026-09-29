# Nano Architecture

How the system is put together as it stands on `main`. Where a layer is
aspirational rather than built, it says so.

## Purpose

Nano is a desktop personal assistant for Windows. It combines several model
providers, persistent conversations, long-term memory, task orchestration and
narrow tool use behind one rule: **the model may ask; the system decides and
executes.**

## The shape of it

```
┌──────────── Electron shell (electron/) ─────────────────────────┐
│  window · tray · global hotkey · voice overlay · lifecycle      │
└──────────────────────────┬──────────────────────────────────────┘
                           │ stdio control channel (no socket, no port)
                           ▼
┌──────────── Python backend (core/) ─────────────────────────────┐
│  eel bridge  ← the renderer's only way in, Origin-enforced      │
│      │                                                          │
│  Brain ── providers.resolve_route ─┬─ groq                      │
│      │                             ├─ mistral                   │
│      │                             ├─ google                    │
│      │                             └─ ollama (local, terminal)  │
│      │                                                          │
│      ├─ MemoryStack   threads · long-term memory · Second Brain │
│      ├─ TaskEngine    persistent queue                          │
│      ├─ VoiceRuntime  STT (local) · TTS (Edge) · wake phrase    │
│      │                                                          │
│      └─ schema → policy → permission → ToolExecutor → narrow tool│
│                                                                  │
│  SQLite: one database, versioned migrations, never replaced      │
└──────────────────────────────────────────────────────────────────┘
```

The Electron shell owns the application lifecycle and spawns the Python backend
as a child process. Tool execution stays in the backend; the renderer never gets
a generic bridge for running anything on the system. See
[DESKTOP.md](DESKTOP.md).

## Layers

### 1. Interaction

The NANO interface (Next.js + React), served locally and reached over eel; the
voice overlay as a separate Electron window; the global hotkey; event streaming
to the UI. Every voice activation path — hotkey, microphone button, wake phrase
— calls the same `VoiceRuntime.run_voice_turn(source)`. There is no second voice
pipeline.

### 2. Conversation and the ownership invariant

Conversations are real threads, stored in SQLite. Creating, opening, renaming,
archiving, deleting one and deleting several are all distinct, narrow
operations exposed to the renderer — never a generic "delete where".

**The ownership invariant:** a message belongs to the thread that was active
when it was written, not to whichever thread happens to be open when the answer
arrives. Opening an older thread rebuilds the Brain's context window from that
thread's messages and summary, which is what makes an old conversation writable
rather than a read-only transcript.

### 3. Model layer

`core.providers.resolve_route` is the single routing authority for a chat turn.
Modes are AUTO (preferred cloud → remaining clouds → Ollama), CLOUD (preferred
cloud only) and LOCAL (Ollama only, no cloud contact at all, not even a status
probe).

`core/model_router.py` is a **separate** scoring abstraction. Its only live role
is choosing which local Ollama model to use when the local model is configured
as `auto`. It does not choose between providers.

Provider status is one shared, TTL'd snapshot (`core/provider_status.py`) rather
than a probe per caller. Rate limits are handled by reacting to a real 429 and
its `Retry-After`, with a per-provider cooldown. See
[MODEL_ROUTING.md](MODEL_ROUTING.md).

### 4. Memory layer

`MemoryStack` is the single facade over six pieces: the versioned schema, the
retrieval index, the conversation store, long-term memory, the knowledge graph
and the context composer. Nothing outside it opens a connection or writes SQL.

* **Conversation store** — threads, messages, progressive summaries, per-thread
  facts.
* **Long-term memory** — cross-thread facts. Extraction (`memory_extraction.py`)
  is **deterministic and local**, not a model call: an explicit "remember this"
  is stored active; an inference is scored and becomes an active memory, an
  inert candidate, or nothing. Preferring to miss an ambiguous fact over storing
  a wrong one is the design.
* **Second Brain** (`knowledge_graph.py`) — nodes for the entities memories are
  about, and edges between nodes that appeared in the same memory. Nodes come
  only from active memories or explicit user action, never from raw message
  text. Implemented and tested; in practice still sparse, because a useful graph
  comes from prolonged real use.
* **Retrieval** — an FTS index over user-provenance material only.
* **Context composer** — the one place that decides what the model is told about
  the past, with a per-section token budget and fingerprint deduplication, so
  CLOUD, AUTO and LOCAL are given logically identical context.

**The latency rule:** everything between Enter and the first token is
synchronous and cheap — one INSERT, one FTS write, a few bounded SELECTs.
Summarisation, memory extraction and graph promotion run on a background worker,
because none of them change the answer to the message that triggered them.

### 5. Tooling layer

Plugins register tool schemas; the registry currently holds **84 entries**, of
which **56** are the `pc_*` PC Control tools. Handlers are never invoked
directly: `plugin_loader` refuses to run one unless the caller presents the
ToolExecutor as its execution authority, so a bypass fails closed.

Browser automation exists (`plugins/web_vision.py`, Playwright) but depends on
an optional dependency and is restricted to public HTTP(S) destinations.

**There is no generic shell, PowerShell, CMD or script-execution tool anywhere
reachable by the model.** The plugins that once offered one were withdrawn and
their files kept as the record of why — see `plugins/god_mode.py` and
`plugins/context_switcher.py`.

### 6. Security layer

The execution path, as implemented in `core/tool_execution.py`:

```
capability resolution
  → argument validation against the registered schema
  → scope classification / target resolution
  → PolicyEngine
  → PermissionManager
  → execution (narrow tool)
  → verification
  → audit
```

Note that **schema validation happens before the policy decides**. The shorter
`MODEL → REQUEST → POLICY → PERMISSION → EXECUTOR` shorthand used elsewhere is a
summary of this, not a different order.

Alongside it: a trust boundary that treats model output and external content as
data rather than instruction, grants bound to capability + target + scope,
protected paths, and a per-turn Execution Ledger that stops a provider failover
from repeating an effect on the machine. See
[../SECURITY_POLICY.md](../SECURITY_POLICY.md).

## Data and migrations

One SQLite database in the data directory, migrated in place and never
replaced. `core/memory_schema.py` is at `SCHEMA_VERSION = 2`; `apply()` is
guarded by `PRAGMA user_version`, runs each version inside one transaction, and
is written to be a no-op if re-run, so it is safe on every start and safe twice
in a row. Nothing is dropped destructively: the two legacy tables keep their
shape and their rows, and pre-existing messages were back-filled into real
threads rather than discarded.

The file is named `helios.db` for compatibility with existing installs. See
[`../README.md`](../README.md) for the other legacy identifiers kept on purpose.

## Task queue

The TaskEngine persists tasks in SQLite. Statuses in use:

`QUEUED` · `PLANNING` · `RUNNING` · `WAITING` · `WAITING_FOR_PERMISSION` ·
`WAITING_PERMISSION` · `RETRYING` · `RECOVERABLE` · `NEEDS_ATTENTION` ·
`COMPLETED` · `FAILED` · `CANCELLED`

A task interrupted while awaiting approval does not auto-execute the action on
recovery; it transitions to `NEEDS_ATTENTION`.

## What is built, and what is not

**Built and on the live path:** the Electron shell and its control channel, the
eel bridge with Origin enforcement, the provider layer and routing authority,
conversation threads, long-term memory with retrieval, the Second Brain, the
TaskEngine, the EventBus, PermissionManager, ContextEngine, PC Control, the
Execution Ledger, and the voice runtime.

**Not built:** packaging and an installer, code signing, an update mechanism,
first-run onboarding, vision/OCR, and the external integrations (GitHub, mail,
calendar beyond the local plugin) that `SECURITY_POLICY.md` declares
capabilities for. Those declarations are architectural placeholders, not
shipped features.
