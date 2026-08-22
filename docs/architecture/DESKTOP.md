# Nano Desktop

How the desktop application is put together, and — more importantly — what each
part is *not* allowed to do.

## The shape of it

```
                      ┌──────────────────────────────────────────┐
   Ctrl+Shift+Space ─▶│  ELECTRON MAIN  (electron/main.js)       │
   (global, works     │  owns: window · tray · shortcut ·        │
    with no window)   │        overlay · the Python child        │
                      └───┬──────────────┬───────────────────┬───┘
                          │              │                   │
             stdio pipe   │              │ IPC (9 named      │ IPC (1 channel,
        (control channel) │              │  channels)        │  inbound only)
                          ▼              ▼                   ▼
        ┌─────────────────────────┐  ┌──────────────┐  ┌──────────────────┐
        │  PYTHON BACKEND         │  │ MAIN WINDOW  │  │  VOICE OVERLAY   │
        │  core/main.py           │  │ React, from  │  │ frameless, click │
        │                         │◀─┤ 127.0.0.1    │  │ -through, always │
        │  VoiceRuntime           │  │ over eel     │  │ on top           │
        │  Brain · Policy         │  └──────────────┘  └──────────────────┘
        │  PermissionManager      │         ▲                   ▲
        │  ToolExecutor           │         │ eel websocket     │ voice_phase
        └─────────────────────────┘─────────┘                   │ events
                          │                                     │
                          └─────────────────────────────────────┘
```

Every activation path — the global hotkey, the microphone button in the UI, and
the experimental wake phrase — calls the same coroutine:

```python
VoiceRuntime.run_voice_turn(source)      # source ∈ {"hotkey", "ui", "wake_phrase"}
```

There is no second voice pipeline. The three triggers differ only in `source`.

## Startup order

Deliberate, and not to be raced:

1. `app.requestSingleInstanceLock()` — a second launch quits and the first one
   shows itself.
2. Probe TCP 47615. If something already holds Python's own instance mutex, a
   backend is running outside this shell (typically `NANO.bat`); say so and stop
   rather than create a second microphone owner.
3. Pick a free port.
4. Spawn `core/main.py --mode electron --port N --desktop-control`.
5. Wait for a `ping` answer on the control channel.
6. Wait for a real HTTP 200 on `/index.html` — a TCP connect is not readiness.
7. **Then** create the window, the tray, the overlay, and register the shortcut.

The window can therefore never open blank, and a keypress can never arrive at a
backend that is not listening yet.

## The control channel, and why it is not a localhost port

The hotkey lives in Electron; the voice turn lives in Python. Something has to
carry "the user pressed the shortcut" across that boundary, and it has to keep
working when the main window is hidden — which rules out routing through the
renderer, because there may be no renderer.

The obvious answer is an authenticated loopback endpoint. Nano deliberately does
**not** do that. Electron already spawns the backend, so a private pipe between
parent and child exists before any request is made:

| authenticated loopback port          | stdin/stdout pipe                     |
|--------------------------------------|---------------------------------------|
| a real listening socket              | no socket, no port, nothing to scan   |
| reachable by every local process     | reachable only by the parent process  |
| needs a shared secret                | the OS handle *is* the authorisation  |
| the secret must be generated, passed, stored, compared, and never logged | there is nothing to leak |
| can outlive Nano as a stale listener | dies exactly when the process does    |

**The trust model in one sentence: the operating system decides who may talk to
this channel, and the answer is "the process that spawned Nano".** There is no
token because a token would add nothing — an attacker who can write to another
process's stdin has already won by an easier route.

Wire format — one tagged JSON object per line, so it cannot be confused with log
output sharing the same stream (`core/desktop_bridge.py`, `electron/lib/ipc-protocol.js`):

```
parent → Nano   @@NANO_IPC@@{"id":"7","op":"start_voice_turn","args":{"source":"hotkey"}}
Nano  → parent  @@NANO_IPC@@{"id":"7","ok":true,"result":{"accepted":true}}
Nano  → parent  @@NANO_IPC@@{"event":"voice_phase","payload":{"phase":"SPEAKING"}}
```

### The complete operation vocabulary

`core/main.py::DESKTOP_OPERATIONS`, and nothing else. An unknown name is refused:

| operation | what it does |
|---|---|
| `ping` | liveness, for the readiness gate |
| `voice_status` | is a turn running, and is voice ready |
| `start_voice_turn` | begin one turn; `source` is the only argument |
| `cancel_voice_turn` | stop current playback; voice stays available |
| `data_location` | the effective data directory, for diagnostics |
| `report_shortcut` | the shell tells the backend what really happened |
| `shutdown` | graceful exit, so PortAudio and SQLite close properly |

Nothing here can name a command, a path or a piece of code; resolve a
capability; bypass the policy engine; or read a secret. `start_voice_turn` only
*starts a turn* — whatever the user then says is understood by the Brain and
travels the unchanged pipeline:

```
MODEL → REQUEST → POLICY → PERMISSION → EXECUTION
```

## What the page is given

`electron/preload.js` exposes one frozen object, `window.nanoApp`, with eleven
named operations: `isDesktop`, `minimize`, `toggleMaximize`, `hide`, `quit`,
`getWindowState`, `onWindowState`, `getDesktopStatus`, `retryShortcut`,
`setOverlayEnabled`, `setAutoLaunch`.

There is deliberately no `invoke(channel, …)`, no `send(channel, …)`, and no
`on(channel, …)`. A generic channel looks like one small convenience and hands
the page every IPC handler the main process will ever register, including ones
added years later. `electron/test/security.test.js` loads the real preload and
the real main process under a stub and asserts the exposed keys and the
registered channels against explicit allow-lists, so widening either is a
decision someone has to make on purpose.

Renderer settings: `contextIsolation: true`, `nodeIntegration: false`,
`sandbox: true`, `webviewTag: false`, all device permission requests denied,
navigation confined to the backend's own origin, popups denied and external
`http(s)` links handed to the OS browser after validation.

The overlay's preload has exactly one entry, `onState`, and no way to send
anything back: it is a status light, not a control.

## Where the data lives

One directory, and Python owns the definition:

```
%LOCALAPPDATA%\NanoAssistant
```

`core/app_paths.data_root()` computes it; `electron/lib/paths.js`
`canonicalDataDir()` mirrors it exactly and passes it back down as
`NANO_DATA_DIR`. This used to be two locations — Electron pointed at its own
`app.getPath('userData')` under `%APPDATA%` — so launching the desktop app after
using `NANO.bat` presented an empty profile and looked like a fresh install.
`tests/test_desktop_shell.py` executes both implementations and compares the
strings rather than trusting this paragraph.

Recovery of data left in older locations is `core/data_migration.py`: it copies
in only when the destination is empty, never overwrites, and never deletes the
source. The desktop shell's own preferences (window bounds, overlay on/off) live
separately in `desktop-state.json` under Electron's `userData` — they are shell
state, not user data.

## Window behaviour

Closing the window **hides it to the tray**. That is the point of the tray: the
global shortcut cannot work if the app has quit. The close button's tooltip says
so, and quitting is a separate, explicit action — tray → **Sair do Nano**.

Quit releases the accelerator, hides and destroys the overlay, asks the backend
to shut down gracefully, kills the process tree if it will not, destroys the
tray, and leaves **Ollama running** — Nano did not start it exclusively and only
tears down what it owns.

Remembered geometry is validated before use (`electron/lib/window-state.js`): a
position that no longer lands on an existing display is dropped and the window
is centred, so unplugging a second monitor cannot hide Nano offscreen.

## The overlay

A second `BrowserWindow`: frameless, transparent, always-on-top (above
full-screen apps), `skipTaskbar`, `focusable: false`, click-through, hidden by
default, positioned bottom-centre of the **work area** of the display under the
cursor.

Every state it shows comes from a real backend event — `voice_phase` while the
turn progresses, `voice_turn_ended` when it finishes, and the backend's own busy
answer when a second activation is refused. `electron/lib/overlay-state.js` is
the whole mapping and is pure, so it is unit-tested without a window. It never
advances a state on its own: if the backend goes quiet, the overlay keeps
showing the last thing that actually happened.

User-visible error text is short and human. Internal codes, provider messages
and tracebacks stay in `logs/nano.log`; a test asserts no label looks like an
identifier.

## Activation

`CommandOrControl+Shift+Space` → **Ctrl+Shift+Space** on Windows.

Registration is verified with `globalShortcut.isRegistered()` rather than
assumed from the return value, and the real outcome is reported to the backend
so Settings can show "Registado" or "Em conflito" honestly, with a retry button.
A conflict disables only the shortcut; the rest of Nano, including the
microphone button, keeps working.

A second press during a turn is refused by the non-blocking lock inside
`run_voice_turn`, so no second microphone reader can be opened. The overlay says
"O Nano já está ocupado".

## Wake phrase

The STT wake phrase ("Ei Nano") is **experimental and off by default** since this
migration. It keeps a capture stream open and runs faster-whisper-tiny over
every chunk forever, it owns the microphone architecture for the whole session,
and tiny is not reliable enough at spotting the phrase to be the activation a
user depends on. The implementation is intact and Settings can turn it back on
under a clear *Experimental* badge. Bare "Nano" and the legacy ONNX wake word
remain disabled.
