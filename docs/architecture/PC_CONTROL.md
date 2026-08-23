# Nano — PC Control V1

Nano can act on Windows. It does so through eighteen narrow, typed tools, and
through nothing else.

## The chain, in full

```
MODEL
  └─ picks a tool name + typed arguments   (it can see the tools; that is not authorization)
REQUEST
  └─ ToolExecutor._authorize
       ├─ emergency stop
       ├─ argument validation      paths resolved + scope-classified centrally
       ├─ capability resolution    pc_window_close  ->  pc.window.close
       ├─ target binding           window_id 786686 ->  target "window:786686"
       ├─ PolicyEngine.evaluate    AUTONOMOUS | APPROVAL_REQUIRED | BLOCKED
       └─ PermissionManager        stored policy, task grants, ALLOW_ONCE
CONFIRMATION                       only if required, bound to THIS target
ToolExecutor._run_handler_async    on the bounded tool thread pool
PC TOOL                            plugins/pc_control.py
WINDOWS                            core/pc_control/*  ->  ctypes -> Win32
REAL RESULT                        observed, then verified
```

The model never reaches Windows. `plugin_loader.execute_tool` raises
`UnauthorizedExecution` unless the caller presents the bound `ToolExecutor` as
its execution authority, so a bypass fails closed rather than silently working.

## Tools

| Tool | Capability | Risk | Confirmation | Implementation |
|---|---|---|---|---|
| `pc_app_search` | `pc.app.search` | low | no | Start Menu catalogue |
| `pc_app_launch` | `pc.app.launch` | medium | no | `ShellExecuteExW` |
| `pc_window_list` | `pc.window.read` | low | no | `EnumWindows` |
| `pc_window_focus` | `pc.window.control` | medium | no | `SetForegroundWindow` |
| `pc_window_minimize` | `pc.window.control` | medium | no | `ShowWindow` |
| `pc_window_maximize` | `pc.window.control` | medium | no | `ShowWindow` |
| `pc_window_restore` | `pc.window.control` | medium | no | `ShowWindow` |
| **`pc_window_close`** | `pc.window.close` | **high** | **yes** | `PostMessage(WM_CLOSE)` |
| `pc_volume_get` | `pc.volume.read` | low | no | `IAudioEndpointVolume` |
| `pc_volume_set` | `pc.volume.control` | medium | no | `IAudioEndpointVolume` |
| `pc_volume_change` | `pc.volume.control` | medium | no | `IAudioEndpointVolume` |
| `pc_volume_mute` | `pc.volume.control` | medium | no | `IAudioEndpointVolume` |
| `pc_volume_unmute` | `pc.volume.control` | medium | no | `IAudioEndpointVolume` |
| `pc_folder_open` | `pc.folder.open` | medium | no | `ShellExecuteExW` |
| `pc_file_search` | `pc.file.search` | low | no | `os.walk`, bounded |
| `pc_file_open` | `pc.file.open` | medium | no | `ShellExecuteExW`, documents only |
| `pc_system_info` | `pc.system.read` | low | no | `psutil` + `platform` |
| **`pc_screenshot_capture`** | `pc.screen.capture` | **high** | **yes** | `BitBlt` + stdlib PNG |

Tool names use underscores because Groq function names cannot contain dots.
The dotted **capability** is the policy identity; the mapping lives in
`PolicyEngine._aliases`.

## Why there is no shell

There is no `subprocess` import anywhere in `core/pc_control/` except one
fixed-argv `nvidia-smi` call in `system.py` for the GPU name. Nothing composes
a command line, so no argument can be re-parsed as syntax. A test asserts this
from the AST rather than by grepping, so a comment mentioning `shell=True`
cannot satisfy it.

`ShellExecuteExW` takes the file and its parameters as separate typed fields,
which is what makes `.lnk` shortcuts work without a shell.

`IAudioEndpointVolume` is called directly through its COM vtable
(`core/pc_control/winapi.py`). pycaw and comtypes are not installed, and the
usual alternative — generating a PowerShell script — is exactly what this
design refuses.

### Two arbitrary-execution tools were withdrawn

`plugins/god_mode.py` previously exposed `system_run_powershell` (a full
command line for the model) and `system_wifi` (which interpolated a
model-supplied network name into a `netsh` string — command injection). Both
are no longer declared to the model. `system_volume` was withdrawn too: it is
superseded by `pc_volume_*`, and its fallback path allocated a buffer, changed
nothing, and reported success. The functions remain in the file; restoring any
of them requires re-adding both the declaration and the handler entry.

## Result contract

Every tool returns the same shape, and it always describes something observed:

```json
{ "ok": true,  "status": "launched", "message": "Calculadora foi aberto.", "app": {...} }
{ "ok": false, "status": "not_found", "error": "not_found", "message": "..." }
```

`fail()` always sets `error`, and `ToolExecutor._verify_execution` treats
`ok: false` as a failed execution. That is what stops "não encontrei o Spotify"
from reaching the model wrapped as `success: true`. Nano may only say "Spotify
aberto" after a launch that returned a real PID.

Statuses: `launched`, `already_running`, `not_found`, `ambiguous`,
`permission_denied`, `launch_failed`, `refused`, `focus_refused`,
`state_unchanged`, `invalid_input`, `protected_path`, `executable_refused`,
`unsupported_platform`, `internal_error`.

## Bounds

Windows ≤ 60 · app candidates ≤ 12 · file results ≤ 100 · strings ≤ 512 chars ·
nesting ≤ 8 · whole result ≤ 128 KB (matching `task_engine.MAX_RESULT_BYTES`).
File search additionally stops at 8 s, depth 6, and 40 000 entries. Every
trim sets `truncated: true` — a silently shortened list would be read as a
complete answer.

## Application control

`app.launch` takes a NAME, never a path. Names resolve against a catalogue
built from the two Start Menu trees plus a fixed table of Windows built-ins
(Calculator, Notepad, Explorer, Paint, Settings). 101 entries on this machine,
cached for five minutes, no disk scan.

Scoring is exactness only: exact name 1.0, declared alias 1.0, leading whole
words 0.9, whole-word containment 0.6. **There is no substring rule and no edit
distance**, so "apaga" cannot reach "Paint". Two candidates tied at the top are
`ambiguous` and neither runs — Nano asks.

## Window control

`window.list` filters out invisible, cloaked, owned, tool and shell-class
windows — 148 raw handles become 3–6 real applications.

`window.close` posts `WM_CLOSE`, the same message the X button sends. The
application may prompt to save; if the window is still there after 1.5 s the
result is `refused`, with the likely reason. **There is no process termination
anywhere in PC Control**, and a test walks the AST of every module to prove no
`terminate`/`kill` call exists.

`focus` re-reads `GetForegroundWindow` afterwards, because Windows is allowed
to refuse a foreground change; a refusal is reported, not narrated as success.

## Volume

Levels are integers 0–100. NaN, infinity and non-numeric input are **rejected**
(`invalid_input`), never coerced — silently turning NaN into 0 would mute the
machine on a malformed argument. Deltas are rejected outside ±100 and the
*result* is clamped to 0–100, so "+100" at 40 lands on 100 and is not an error.
With no number given, the step is **10 points**. Every operation re-reads the
device afterwards.

## Files and folders

`folder.open` accepts a known name via `folder` (Downloads, Documentos,
Ambiente de Trabalho, Imagens, Música, Vídeos) or a full path via `path`. The
split is load-bearing: `ToolExecutor` centrally resolves every argument named
`path` against the workspace root, which turned the word "Downloads" into
`<repo>/Downloads`. Known names therefore travel as `folder`; real paths keep
the central validation.

`file.search` defaults to Desktop, Documents and Downloads — never `C:\`. It
returns path, filename, extension, size and mtime; **never contents**, so it
cannot become a read primitive for files the model may not read. Symlinks are
not followed (`followlinks=False`).

`file.open` **refuses** `.exe .com .bat .cmd .ps1 .vbs .js .msi .scr .reg .lnk`
and friends outright — not gated, refused. Windows would *run* those on an
"open" verb, and `app.launch` already covers launching real software safely.

Protected everywhere: `%SystemRoot%`, Program Files, ProgramData, Nano's own
data directory, `.ssh`, `.aws`, `.gnupg`.

## System info

OS, hostname, CPU model and cores, CPU %, RAM, disk, GPU, battery, uptime.
Deliberately absent: serial numbers, product keys, MAC and IP addresses,
environment variables, the account name.

## Screenshots

Permission-gated (`pc.screen.capture`, high risk, always confirmed). The image
**never enters the model's context** — the tool returns a path, dimensions and
a byte count, no base64. Files land in `<DATA_DIR>/screenshots/` under a unique
name, scaled so the long edge is ≤ 1920, and every capture first deletes
anything older than an hour or beyond the most recent ten. Nothing uploads.

## Ambiguity and voice

STT is not rewritten here, and no fake confidence number is invented — Whisper
does not provide a meaningful one. The signals used are ones that genuinely
exist:

* **multiple candidates** — `app.launch` and `window.*` return `ambiguous` with
  the list instead of picking.
* **unknown target** — `not_found`, never a guess.
* **loose match + destructive verb** — `window.close` sets
  `allow_partial=False`, so it needs a `window_id` from `window.list`, an exact
  title, or a process name. A substring match raises `ambiguous` and asks.
* **consequential capability** — `pc.window.close` and `pc.screen.capture`
  always require confirmation, bound to the specific target.

The benchmark produced "Procuro fechar o relatório" for "Procura o ficheiro
relatório". A loose match plus a consequential verb is exactly that failure,
and it stops and asks.

## Target binding

Permissions bind to the actual target. `ToolExecutor._pc_control_target`
normalises a PC call into `target` (`app:Spotify`, `window:786686`,
`folder:Downloads`, `query:...`) before authorization, because
`PermissionManager._resolve_target` only inspects
`(path, target, url, command, cwd)` — without it every PC grant would key on
`*`, and one ALLOW_ONCE would authorise every later call of that capability.
Non-PC tools are untouched.

## Explicitly unsupported in V1

Arbitrary command/shell/PowerShell execution · process kill · file delete,
move, rename or write · registry edits · Windows services · shutdown or reboot
· software install/uninstall · keyboard and mouse automation · screen-coordinate
clicking · OCR · browser automation · clipboard history · credential management.

## Future expansion points

* A `pc.window.close` task grant so a multi-window cleanup confirms once.
* Per-monitor screenshots and region capture.
* An app catalogue fed by installed-package metadata as well as the Start Menu.
* A clarification round-trip surfaced in the voice overlay, using the
  `ambiguous` candidate lists these tools already return.
