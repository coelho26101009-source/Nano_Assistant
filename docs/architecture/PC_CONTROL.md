# Nano — PC Control V2

Nano can act on Windows. It does so through **56 narrow, typed tools**, and
through nothing else.

The premise is unchanged from V1 and is what the whole design is for:

> **Broad capability coverage through many narrow tools — never one generic
> executor.**

A single `computer_action({type, args})` would be far shorter to write and
impossible to reason about, because every refusal beside it could be spelled
out inside it. So each capability has its own schema, its own risk, its own
confirmation rule and its own target, and a thing Nano cannot do has nowhere to
be expressed.

## The chain, in full

```
MODEL
  └─ picks a tool name + typed arguments   (it can see the tools; that is not authorization)
REQUEST
  └─ ToolExecutor._authorize
       ├─ emergency stop
       ├─ argument validation      paths resolved + scope-classified centrally
       ├─ capability resolution    pc_window_close  ->  pc.window.close
       │                           pc_input_press_key(delete) -> pc.input.key_destructive
       ├─ target binding           _pc_control_target -> `_pc_target`
       ├─ PolicyEngine.evaluate    AUTONOMOUS | APPROVAL_REQUIRED | BLOCKED
       └─ PermissionManager        stored policy, task grants, ALLOW_ONCE
CONFIRMATION                       only if required, bound to THIS target,
                                   showing ACTION / TARGET / SCOPE / preview
ToolExecutor._run_handler_async    on the bounded tool thread pool
PC TOOL                            plugins/pc_control.py
WINDOWS                            core/pc_control/*  ->  ctypes -> Win32
REAL RESULT                        observed, then verified
```

The model never reaches Windows. `plugin_loader.execute_tool` raises
`UnauthorizedExecution` unless the caller presents the bound `ToolExecutor` as
its execution authority, so a bypass fails closed rather than silently working.

## Capability matrix

Risk and confirmation come from `PolicyEngine._register_default_rules`; the
dotted capability is the policy identity, and the underscore tool name is what
Groq sees (function names cannot contain dots). The mapping lives in
`PolicyEngine._aliases` and is the only place the two vocabularies meet.

### Applications

| Tool | Capability | Risk | Confirm | Target | Implementation |
|---|---|---|---|---|---|
| `pc_app_search` | `pc.app.search` | low | no | `query:…` | catalogue lookup |
| `pc_app_launch` | `pc.app.launch` | medium | no | `app:…` | `ShellExecuteExW` |
| `pc_app_switch` | `pc.app.switch` | medium | no | `app:…` | `SetForegroundWindow` |
| `pc_app_list_running` | `pc.app.read` | low | no | `apps:running` | window enumeration, grouped |

### Windows

| Tool | Capability | Risk | Confirm | Target | Implementation |
|---|---|---|---|---|---|
| `pc_window_list` | `pc.window.read` | low | no | `windows:all` | `EnumWindows` |
| `pc_window_focus` | `pc.window.control` | medium | no | `window:…` | `SetForegroundWindow` |
| `pc_window_minimize` | `pc.window.control` | medium | no | `window:…` | `ShowWindow` |
| `pc_window_maximize` | `pc.window.control` | medium | no | `window:…` | `ShowWindow` |
| `pc_window_restore` | `pc.window.control` | medium | no | `window:…` | `ShowWindow` |
| **`pc_window_close`** | `pc.window.close` | **high** | **yes** | `window:…` | `PostMessage(WM_CLOSE)` |
| `pc_window_move` | `pc.window.geometry` | medium | no | `window:…` | `SetWindowPos` |
| `pc_window_resize` | `pc.window.geometry` | medium | no | `window:…` | `SetWindowPos` |
| `pc_window_center` | `pc.window.geometry` | medium | no | `window:…` | work area + `SetWindowPos` |
| `pc_window_snap` | `pc.window.geometry` | medium | no | `window:…` | work area + `SetWindowPos` |
| `pc_window_move_monitor` | `pc.window.geometry` | medium | no | `window:…` | `EnumDisplayMonitors` |
| `pc_window_set_topmost` | `pc.window.geometry` | medium | no | `window:…` | `SetWindowPos(HWND_TOPMOST)` |
| `pc_window_batch_state` | `pc.window.batch` | medium | no | `windows:<app>:<state>` | `ShowWindow` per window |
| **`pc_window_batch_close`** | `pc.window.batch_close` | **high** | **yes** | `windows:<app>` | `WM_CLOSE` per window |

### Audio and media

| Tool | Capability | Risk | Confirm | Target | Implementation |
|---|---|---|---|---|---|
| `pc_volume_get` | `pc.volume.read` | low | no | `volume:get` | `IAudioEndpointVolume` |
| `pc_volume_set` | `pc.volume.control` | medium | no | `volume:set` | `IAudioEndpointVolume` |
| `pc_volume_change` | `pc.volume.control` | medium | no | `volume:change` | `IAudioEndpointVolume` |
| `pc_volume_mute` | `pc.volume.control` | medium | no | `volume:mute` | `IAudioEndpointVolume` |
| `pc_volume_unmute` | `pc.volume.control` | medium | no | `volume:unmute` | `IAudioEndpointVolume` |
| `pc_media_control` | `pc.media.control` | medium | no | `media:<action>` | `SendInput` (4 fixed VKs) |

### Display

| Tool | Capability | Risk | Confirm | Target | Implementation |
|---|---|---|---|---|---|
| `pc_display_info` | `pc.display.read` | low | no | `display:all` | `EnumDisplayMonitors` + DDC/CI probe |
| `pc_display_set_brightness` | `pc.display.control` | medium | no | `display:<n>` | `SetMonitorBrightness` |
| `pc_display_change_brightness` | `pc.display.control` | medium | no | `display:<n>` | `SetMonitorBrightness` |

### Clipboard

| Tool | Capability | Risk | Confirm | Target | Implementation |
|---|---|---|---|---|---|
| **`pc_clipboard_read`** | `pc.clipboard.read` | **high** | **yes** | `clipboard:read` | `GetClipboardData(CF_UNICODETEXT)` |
| **`pc_clipboard_write`** | `pc.clipboard.write` | **high** | **yes** | `clipboard:write:#<digest>` | `SetClipboardData` |
| **`pc_clipboard_clear`** | `pc.clipboard.clear` | **high** | **yes** | `clipboard:clear` | `EmptyClipboard` |

### Keyboard and pointer

| Tool | Capability | Risk | Confirm | Target | Implementation |
|---|---|---|---|---|---|
| **`pc_input_type_text`** | `pc.input.type` | **high** | **yes** | `input:type:window:…:#<digest>` | `SendInput` (KEYEVENTF_UNICODE) |
| `pc_input_press_key` | `pc.input.key` | medium | no | `input:key:<key>:window:…` | `SendInput` |
| **`pc_input_press_key`** (delete/backspace) | `pc.input.key_destructive` | **high** | **yes** | as above | `SendInput` |
| `pc_input_hotkey` | `pc.input.hotkey` | medium | no | `input:hotkey:<name>:…` | `SendInput` |
| `pc_pointer_scroll` | `pc.pointer.scroll` | low | no | `pointer:scroll:window:…` | `SendInput` (wheel) |

### Files and folders

| Tool | Capability | Risk | Confirm | Target | Implementation |
|---|---|---|---|---|---|
| `pc_folder_open` | `pc.folder.open` | medium | no | resolved path / `folder:…` | `ShellExecuteExW` |
| `pc_file_search` | `pc.file.search` | low | no | `query:…` | `os.walk`, bounded |
| `pc_file_open` | `pc.file.open` | medium | no | resolved path | `ShellExecuteExW`, documents only |
| **`pc_folder_create`** | `pc.folder.create` | **high** | **yes** | `create:<parent>/<name>` | `Path.mkdir` |
| **`pc_file_create_text`** | `pc.file.create` | **high** | **yes** | `create:<parent>/<name>` | `Path.write_text`, inert extensions |
| **`pc_file_copy`** | `pc.file.copy` | **high** | **yes** | `file:<src> -> <dst>` | `shutil.copy2` |
| **`pc_file_move`** | `pc.file.move` | **high** | **yes** | `file:<src> -> <dst>` | `shutil.move` |
| **`pc_file_rename`** | `pc.file.rename` | **high** | **yes** | `file:<src> -> <name>` | `Path.rename` |
| **`pc_file_recycle`** | `pc.file.recycle` | **critical** | **yes** | `recycle:<path>` | `SHFileOperationW` + `FOF_ALLOWUNDO` |
| **`pc_folder_recycle`** | `pc.folder.recycle` | **critical** | **yes** | `recycle:<path>` | `SHFileOperationW` + `FOF_ALLOWUNDO` |

### Web, Settings, system

| Tool | Capability | Risk | Confirm | Target | Implementation |
|---|---|---|---|---|---|
| `pc_web_open_url` | `pc.web.open` | medium | no | the validated URL | `ShellExecuteExW` |
| `pc_web_search` | `pc.web.search` | medium | no | `search:<engine>:<query>` | `ShellExecuteExW` |
| `pc_settings_open` | `pc.settings.open` | medium | no | `settings:<section>` | `ShellExecuteExW(ms-settings:…)` |
| `pc_system_info` | `pc.system.read` | low | no | `system:info` | `psutil` + `platform` |
| `pc_network_status` | `pc.network.read` | low | no | `system:network` | `InternetGetConnectedState` |
| `pc_storage_info` | `pc.storage.read` | low | no | `system:storage` | `psutil.disk_usage` |

### Power, session, screen

| Tool | Capability | Risk | Confirm | Target | Implementation |
|---|---|---|---|---|---|
| **`pc_session_lock`** | `pc.session.lock` | **high** | **yes** | `session:lock` | `LockWorkStation` |
| **`pc_power_sleep`** | `pc.power.sleep` | **high** | **yes** | `power:sleep` | `SetSuspendState` |
| **`pc_power_restart`** | `pc.power.restart` | **critical** | **yes** | `power:restart` | `ExitWindowsEx(EWX_REBOOT)` |
| **`pc_power_shutdown`** | `pc.power.shutdown` | **critical** | **yes** | `power:shutdown` | `ExitWindowsEx(EWX_SHUTDOWN)` |
| **`pc_session_logoff`** | `pc.session.logoff` | **critical** | **yes** | `session:logoff` | `ExitWindowsEx(EWX_LOGOFF)` |
| **`pc_screenshot_capture`** | `pc.screen.capture` | **high** | **yes** | `screen:<mode>` | `BitBlt` / `PrintWindow` + stdlib PNG |

**Critical** capabilities additionally cannot be covered by a task-wide grant:
`PermissionManager.resolve_permission` refuses `ALLOW_FOR_TASK` for
`pc.file.recycle`, `pc.folder.recycle`, `pc.power.restart`,
`pc.power.shutdown` and `pc.session.logoff`. Approving one restart cannot
become approving restarts for the rest of the task.

## Why there is no shell

There is no `subprocess` import anywhere in `core/pc_control/` except one
fixed-argv `nvidia-smi` call in `system.py` for the GPU name. Nothing composes
a command line, so no argument can be re-parsed as syntax. Tests assert this
from the AST rather than by grepping, so a comment mentioning `shell=True`
cannot satisfy them.

`ShellExecuteExW` takes the file and its parameters as separate typed fields,
which is what makes `.lnk` shortcuts and `ms-settings:` URIs work without a
shell. `IAudioEndpointVolume` is called directly through its COM vtable.

### The composition that would have been a shell

V2 can launch a terminal — it is an application the user installed — and V2 can
send keystrokes. Individually reasonable; together, arbitrary command execution
with nothing in between but the user reading a dialog carefully.

So the composition is broken **structurally**: `windows.resolve_input_target`
refuses a target whose process or window class is a console
(`cmd.exe`, `powershell.exe`, `pwsh.exe`, `WindowsTerminal.exe`, `conhost.exe`,
`bash.exe`, `python.exe`, `ConsoleWindowClass`, `CASCADIA_HOSTING_WINDOW_CLASS`,
…). Every input tool goes through it, and the refusal is `blocked`, not a
prompt. Nano's own windows are refused the same way.

The application catalogue also filters the interpreters back out of discovery,
so `pc_app_launch` does not offer `powershell` or `wt` as applications even
though the registry and the Store aliases list them.

### Three arbitrary-execution tools were withdrawn in the V2 audit

`plugins/god_mode.py` is now a tombstone: no tools, no PowerShell, and a
docstring recording what was there. On top of V1's withdrawals
(`system_run_powershell`, `system_wifi`, `system_volume`), the V2 audit removed:

* **`system_bluetooth`** — a live command-injection sink. Its argument was
  declared as a boolean but nothing enforced that, and the value was rendered
  into a PowerShell block with `str(enable).lower()`. Replaced by
  `pc_settings_open(section="bluetooth")`.
* **`system_brightness`** — built a WMI call as a PowerShell string. Replaced by
  `pc_display_set_brightness` / `pc_display_change_brightness`.
* **`system_files`** — the widest hole left. It could create a file with **any**
  extension anywhere it could write (`.bat`, `.ps1`, `.vbs` included), which
  made "write a file" a way to author an executable and reduced the careful
  refusal in `pc_file_open` to a formality. Its `move` was a bare
  `Path.rename` with no protected-path policy and no undo. Replaced by the
  `pc_file_*` / `pc_folder_*` family.

## Result contract

Every tool returns the same shape, and it always describes something observed:

```json
{ "ok": true,  "status": "recycled", "message": "…", "item": {...} }
{ "ok": false, "status": "protected_path", "error": "protected_path", "message": "…" }
```

`fail()` always sets `error`, and `ToolExecutor._verify_execution` treats
`ok: false` as a failed execution. That is what stops "não encontrei o Spotify"
from reaching the model wrapped as `success: true`.

**Statuses.** V1's vocabulary is canonical and V2 uses it rather than
introducing synonyms — the V2 brief's `invalid_argument` is this project's
`invalid_input`:

`launched` · `already_running` · `found` · `listed` · `read` · `set` ·
`changed` · `moved` · `resized` · `centered` · `snapped` · `topmost_set` ·
`batch_applied` · `batch_closed` · `typed` · `pressed` · `scrolled` ·
`written` · `cleared` · `created` · `copied` · `renamed` · `recycled` ·
`removed_not_recycled` · `opened` · `captured` · `sent` · `requested` ·
`not_found` · `ambiguous` · `conflict` · `blocked` · `protected_path` ·
`executable_refused` · `permission_denied` · `invalid_input` · `unsupported` ·
`unsupported_platform` · `refused` · `focus_refused` · `state_unchanged` ·
`failed` · `launch_failed` · `open_failed` · `close_failed` ·
`audio_unavailable` · `audio_failed` · `internal_error`

`unsupported` and `unsupported_platform` are different facts: the first means
"the platform is right and this hardware cannot do it" (a monitor with no
DDC/CI, a clipboard holding an image), the second means "this is not Windows".

## Bounds

Windows ≤ 60 · batch ≤ 20 windows · app candidates ≤ 12 · file results ≤ 100 ·
running apps ≤ 30 · strings ≤ 512 chars · nesting ≤ 8 · whole result ≤ 128 KB
(matching `task_engine.MAX_RESULT_BYTES`) · permission target ≤ 300 chars ·
typed text ≤ 2000 chars · clipboard ≤ 4000 chars each way · created text file
≤ 32 KB · copied file ≤ 256 MB · scroll ≤ 20 clicks · screenshot long edge
≤ 1920 px. File search additionally stops at 8 s, depth 6, and 40 000 entries.
Every trim sets `truncated: true`.

## Applications

`app.launch` takes a NAME, never a path. V2 widened **discovery** without
widening what may be launched — every launch target still comes from the
machine:

| Source | What it contributes | Launch target |
|---|---|---|
| Start Menu (user + system) | the display names people actually say | the `.lnk` path found on disk |
| App Paths registry | ordinary desktop software (`chrome`, `code`) | the registry KEY NAME, which `ShellExecuteExW` resolves itself |
| `%LOCALAPPDATA%\Microsoft\WindowsApps` | packaged/Store apps — **this is how Spotify became findable** | the alias file found on disk |
| Fixed built-in table | Calculator, Notepad, Explorer, Paint, Settings | a constant in source |

115 entries on this machine, cached for five minutes, no disk crawl. Discovery
filters out plumbing (installers, helpers, `*Server`, `*_cli`, version-suffixed
aliases) and command interpreters.

Scoring is exactness only: exact name 1.0, declared alias 1.0, leading whole
words 0.9, whole-word containment 0.6. **There is no substring rule and no edit
distance**, so "apaga" cannot reach "Paint". Two candidates tied at the top are
`ambiguous` and neither runs.

`app.switch` never falls back to launching: "muda para o Discord" when Discord
is closed is a question, not a licence to start a program nobody asked for.

## Windows and geometry

`window.list` filters out invisible, cloaked, owned, tool and shell-class
windows — 148 raw handles become 3–6 real applications.

**Coordinates are requests, not addresses.** Every number is finite-checked
(NaN and infinity are *rejected*, never clamped — clamping NaN would silently
mean "top-left corner"), bounded to ±32 000, then clamped against the real
**work area** of a real monitor from `EnumDisplayMonitors`. A window is never
left entirely off-screen, because a window with no visible titlebar cannot be
dragged back. It is also never shrunk below 200×120.

Snapping and centring take no coordinates at all — an enum plus a monitor, and
the rectangle is computed from the work area, which is why a snapped window
does not sit under the taskbar. Monitors are numbered left-to-right,
top-to-bottom rather than in OS enumeration order, so "monitor 2" means the
same screen every day.

Every placement **re-reads** the window rect afterwards. A fixed-size or
self-positioning application that ignores the move is reported as
`state_unchanged`, not narrated as success.

`window.close` posts `WM_CLOSE`, the same message the X button sends, and
refuses a loose title match (`allow_partial=False`). **There is no process
termination anywhere in PC Control**; a test walks the AST of every module to
prove no `terminate`/`kill`/`unlink`/`rmtree` call exists, with exactly one
audited exception (`screen.cleanup`, which deletes Nano's own expired captures
and is proved to enumerate nothing else).

### Batch actions

`pc_window_batch_state` (minimise/restore, moderate) and
`pc_window_batch_close` (**high, always confirmed**) match by **process name or
exact title only** — never a loose substring, because a browser tab or a chat
message can put the word "discord" in any caption. Batches are capped at 20
windows, and the confirmation card names the count and the titles: "fecha tudo
do Discord" is a different decision at one window than at nine.

## Keyboard and input

Three rules, and they are the whole design.

1. **There is no key-sequence argument.** Text is sent as Unicode CHARACTERS
   (`KEYEVENTF_UNICODE`) — no scan-code table, and accented Portuguese arrives
   correctly whatever the layout is. A chord is chosen by NAME from
   `HOTKEY_ALLOWLIST`. `press_chord` is the only function taking a raw code and
   the tool layer never calls it directly.
2. **Typing is always aimed.** Every input action names its window, that window
   is resolved **strictly** (`allow_partial=False`), focused, and the OS is
   asked whether the focus change actually happened. If Windows refused, nothing
   is typed. There is deliberately no implicit "foreground window" target:
   Nano's own approval dialog holds the foreground while the user reads it, so
   an implicit target would resolve to Nano.
3. **Nano never types a secret it looked up**, because it cannot look one up:
   no module in the package imports the secret store, and no tool returns a
   stored credential. What can be typed is text the model composed or the user
   dictated — and the confirmation card shows it in full first.

Allowed keys: `enter escape tab backspace delete space up down left right home
end page_up page_down`. `delete` and `backspace` resolve to a **different,
confirmed capability** (`pc.input.key_destructive`) through
`PermissionManager.resolve_tool_capability` — pressing Right-Arrow and pressing
Delete are the same tool and are not the same action.

Allowed hotkeys: `copy paste cut select_all undo redo save find address_bar`
(each needing a named window) plus `switch_window` (Alt+Tab) and
`show_desktop` (Win+D), which act on the desktop and therefore refuse a window
argument.

### What a keystroke result actually claims

Injected events carry the hardware **scan code** (`MapVirtualKeyW`) and the
**extended-key flag** for the arrows, Home/End, Page Up/Down and Delete. Both
were missing at first, and the failure was invisible: `SendInput` reports
success as soon as an event is injected, so a key with `wScan = 0` is delivered,
accepted, and then ignored by any application that matches accelerators on the
scan code. Without the extended flag the arrows arrive as their numpad twins.

Even with both correct, **sending a key is not the application acting on it**,
and Nano cannot tell the difference from outside. Measured on this machine:

| action | result |
|---|---|
| `type_text` into Windows 11 Notepad | lands — the title gains its unsaved-changes `*` |
| `Win+D` | lands — every window minimises, and again restores them |
| `Ctrl+A` / `Ctrl+C` at that same Notepad window | **does not take effect** |

Notepad on Windows 11 is a packaged XAML application whose text surface lives in
a content island, and it does not act on those injected accelerators. So
`pc_input_press_key`, `pc_input_hotkey` and `pc_media_control` all return status
`sent` with `confirmed: false` and say plainly that Nano cannot confirm the
application reacted. `pc_input_type_text` keeps a stronger claim, because it has
a real signal: the number of events Windows accepted, checked against the number
required.

Chords are therefore reliable for **desktop-level** gestures and for classic
Win32 applications, and unreliable for packaged XAML apps. Typing works
everywhere tested.

## Pointer — deliberately deferred

**Implemented:** `pc_pointer_scroll`, which takes a direction enum and a
bounded click count, focuses a named window first, and has no coordinate
argument anywhere.

**Deliberately not implemented:** click-at-a-pixel, pointer movement, drag.
A coordinate click is only meaningful if you know what is *at* that coordinate,
and Nano does not — it has no vision. Shipping one would mean the model
guessing pixel positions from a screenshot it cannot see, which is
click-and-pray automation with a permission prompt in front of it. That belongs
to a Computer Use / Vision phase with its own consent model, not to V2.

## Files and folders

`folder.open` accepts a known name via `folder` (Downloads, Documentos,
Ambiente de Trabalho, Imagens, Música, Vídeos) or a full path via `path`. The
split is load-bearing: `ToolExecutor` centrally resolves every argument named
`path` against the workspace root, which turned the word "Downloads" into
`<repo>/Downloads`. The same split applies to `pc_folder_create` and
`pc_file_create_text`.

`file.search` defaults to Desktop, Documents and Downloads — never `C:\`. It
returns metadata, **never contents**, so it cannot become a read primitive.

`file.open` **refuses** `.exe .com .bat .cmd .ps1 .vbs .js .msi .scr .reg .lnk`
and friends outright — not gated, refused.

### Creating, copying, moving

* **Never a shell bypass.** A text file may only use an extension from
  `TEXT_EXTENSIONS` (`.txt .md .csv .tsv .json .log .yaml .yml .ini .cfg .conf
  .rst .tex .srt`). `.html` and `.svg` are refused too: both can carry script
  that runs when the file is opened. Copy, move and rename refuse an executable
  destination extension for the same reason.
* **Never a surprise overwrite.** An existing destination is a `conflict`,
  refused. There is no overwrite flag in V2.
* **Names are not paths.** `safe_name` refuses separators, `..`, drive letters,
  Windows device names, control characters, and a trailing dot — Windows erases
  a trailing dot, so `report.txt.` *is* `report.txt`, and accepting the first
  spelling would let a "new" file land on an existing one past the conflict
  check.
* **Never unverified.** Every operation re-reads the filesystem afterwards.

### Deleting means the Recycle Bin

There is no `unlink`, `rmdir` or `rmtree` in the file layer and no fallback that
reaches for one. `pc_file_recycle` / `pc_folder_recycle` call
`SHFileOperationW` with `FOF_ALLOWUNDO` — the shell's own "send to Recycle
Bin" — plus `FOF_WANTNUKEWARNING`, so if an item *cannot* be recycled Windows
asks the user rather than silently destroying it.

The result is then **verified against the bin's own item count**
(`SHQueryRecycleBinW`). If the source is gone but the count did not rise, the
result is `removed_not_recycled` with a message saying the undo the user
expects is not there. Two different true statements; Nano reports the one that
is actually true.

### Protected locations

`core.execution_scope.is_protected_location` is the single source of truth, and
`core.pc_control.files.is_protected` delegates to it — until V2 only the PC
layer knew about Windows and Program Files, so the central path authority every
`path` argument passes through did not.

Protected: `%SystemRoot%`, Program Files (both), ProgramData, Nano's own
application root and data directory, any bare drive root, `.ssh` `.aws`
`.gnupg` `.kube` `.azure` `.docker` `$Recycle.Bin` `System Volume Information`
wherever they appear, and these profile sub-trees — DPAPI master keys
(`AppData/Roaming/Microsoft/Crypto`, `.../Protect`), the **Startup** folder,
and the Chrome, Edge, Brave, Firefox and Opera profile directories.

A protected location is refused by `PolicyEngine` **before the user is asked**
(`PC_FILE_MUTATION_CAPABILITIES`), because asking somebody to authorise
something that was always going to be refused teaches them their approval does
not mean anything.

## Web and Windows Settings

`pc_web_open_url` hands an address to the user's own browser and stops. It does
not read the page, click anything, or know what happened next — that is Browser
Agent, and keeping them apart is what stops "abre o YouTube" from growing into
"act as me on the web".

The **scheme is the security boundary**: `http` and `https` only. `file:`,
`javascript:`, `data:`, `vbscript:`, `shell:`, `ms-settings:`, `search-ms:`,
`view-source:`, `ftp:`, `smb:` and anything else are refused, as are URLs
carrying credentials. A bare `github.com` is upgraded to **https**, never http.
`pc_web_search` builds the URL from an engine enum and a percent-encoded query,
then re-validates it.

Two consequences worth knowing: the executor's central URL validation also
applies (`_URL_ARGUMENT_KEYS`), so a private or unresolvable host is refused —
`http://localhost:3000` will not open. And `pc.web.open` / `pc.web.search` are
the only PC capabilities in `SCOPE_ESCALATION_EXEMPT`, because otherwise every
"abre o YouTube" would raise an approval dialog purely because the target
string contains a URL.

`pc_settings_open` takes a **section name from an enum**; the `ms-settings:`
URI is a constant in `core/pc_control/settings.py` that the name maps to. The
model never supplies a URI — that protocol reaches account recovery, sign-in
options and device enrolment, and accepting a string would make "open the sound
settings" and "open the page that removes a device" the same tool.

## System information

Deliberately absent everywhere: serial numbers, product keys, MAC and IP
addresses, gateways, DNS servers, Wi-Fi network names, saved profiles, volume
serials, environment variables, the account name.

`pc_network_status` uses `InternetGetConnectedState`, a **local** query that
sends no traffic to anybody — so it answers "is there a connection", not "does
the internet work", and says so rather than overclaiming.

## Power and session

Nothing here is **forced**: `ExitWindowsEx` is called without `EWX_FORCE`, so an
application with unsaved work can veto and show its own dialog. Nothing here is
**scheduled**: no countdown, no delay. Nothing here closes applications first.
Restart, shutdown and sign-out are CRITICAL, which means only `ALLOW_ONCE` can
authorise them.

## Screenshots and OCR

Permission-gated, always confirmed. Three modes: `desktop`, `active_window`,
`window` (target-bound to a window id). The image **never enters the model's
context** — the tool returns a path, dimensions and a byte count, no base64 —
and nothing uploads it: no `httpx`, `requests`, `socket` or any other network
import exists anywhere in the package, which a test asserts. Files land in
`<DATA_DIR>/screenshots/` under a unique name, scaled so the long edge is
≤ 1920, and every capture first deletes anything older than an hour or beyond
the most recent ten.

Window capture uses `PrintWindow(PW_RENDERFULLCONTENT)` so a partly covered
window still captures, and falls back to copying the window's screen rectangle
— **saying which one it used**, because a fallback capture of a covered window
contains whatever was on top of it.

**OCR is deferred, and nothing pretends otherwise.** Windows' own OCR
(`Windows.Media.Ocr`) needs a WinRT binding this project does not have, and
adding one to read text off a screenshot is a large new dependency for a
capability nothing currently asks for. Until it exists, no tool is named or
described as if it read the screen — a test asserts that too. "I can see what
is on your screen" is a claim a screenshot path does not support.

## Target binding

Permissions bind to the thing actually affected. `_pc_control_target` produces
the string and `_validate_arguments` writes it to **`_pc_target`**, which
`PermissionManager._resolve_target` reads **first**.

Two rules the table follows:

* **A call with more than one target names all of them.** `pc_file_move` binds
  to source *and* destination — binding to the source alone would let one
  approval reach a different destination. This is why `_pc_target` out-ranks
  the single `path` key.
* **Content is never a target.** Typed text and clipboard writes bind to an
  8-character SHA-256 prefix, which distinguishes two calls and carries nothing
  that should not be in a log.

Any `_pc_target` the *model* supplies is discarded before anything reads it, so
the authoritative target cannot be forged from tool arguments.

## Duplicate protection across provider failover

The per-turn ledger in `Brain._run_tool` is unchanged in principle: the first
execution of a call is remembered, and an identical repeat returns that result
instead of touching Windows again.

V2 changed what "identical" has to mean. `(tool_name, arguments)` was enough for
V1's window ids; a path can be written several ways for the same file, and two
spellings would be two ledger keys — exactly the hole the ledger exists to
close. `Brain._canonical_arguments` therefore normalises before hashing: strip
strings, case-fold the enum arguments, `normcase`/`normpath` the path
arguments, drop explicit nulls. Deliberately shallow: it must never merge two
calls that would do different things.

Tested across a simulated Groq 429 → Ollama failover for window snap, window
move, typing, file move, file recycle, clipboard write, screenshot, window close
and shutdown.

### The handoff itself was broken, and the fake was hiding it

Groq speaks the OpenAI wire format, where a tool call's `function.arguments` is
a JSON **string**. Ollama decodes that field into a Go map and answers

```
400 {"error":"Value looks like object, but can't find closing '}' symbol"}
```

Nano stored the Groq shape — correctly, since that is what goes back to Groq —
and replayed it verbatim to Ollama. The consequence was not a visible error:
the request failed, the caller fell into the "stream without tools" branch, and
**once Groq had called any tool, the local model lost its tools for the rest of
the conversation.** PC Control silently stopped working on the local provider,
and the model filled the gap with plausible advice — in one observed run, a
fabricated `Get-Process` table for a request that has no shell to run.

`Brain._ollama_tool_calls` now converts the arguments on the way out and leaves
the stored history alone. A call whose arguments will not parse is **dropped**
rather than sent as `{}`, because an empty argument map invites the local model
to invent arguments — which would be a second, different call that the per-turn
ledger has no reason to recognise as a repeat.

The tests were green throughout, because `FakeOllamaClient` accepted any
payload. **A fake more permissive than the server it stands in for cannot fail
where the server does**, so it now validates requests on exactly this rule.

## Ambiguity and voice

STT is not rewritten here, and no fake confidence number is invented — Whisper
does not provide a meaningful one. The signals used genuinely exist:

* **multiple candidates** — `app.launch`, `app.switch` and `window.*` return
  `ambiguous` with the list instead of picking.
* **unknown target** — `not_found`, never a guess.
* **loose match + consequential verb** — `window.close` and every input tool set
  `allow_partial=False`, so they need a `window_id`, an exact title, or a
  process name. A substring match raises `ambiguous` and asks.
* **batch by process, not caption** — a loose title cannot select a batch.
* **consequential capability** — twenty capabilities always require
  confirmation, bound to the specific target.

The benchmark produced "Procuro fechar o relatório" for "Procura o ficheiro
relatório". A loose match plus a consequential verb is exactly that failure, and
it stops and asks.

## The approval card

A confirmation shows three things a person can judge, plus a preview where the
size of the decision is not visible from the target:

```
ACTION   MOVER PARA A RECICLAGEM
TARGET   C:\Users\…\Documents\relatorio.pdf
SCOPE    Vai para a Reciclagem; podes recuperar
```

It used to read "O Nano pretende executar 'pc.window.close' sobre
'window:786686'. Confirmas?" — a capability the person has never heard of and a
handle that means nothing to them, and the only thing anybody can do with that
is press Yes. `core/confirmation.py` builds the card; it is read-only and
best-effort, because failing to *describe* an action must never become failing
to *ask* about it.

## Tool scoping

56 PC tools is roughly 4500 prompt tokens, against a Groq tier that allows 8000
per minute. `core/model_selection.py` therefore sends the smallest plausible
subset: `PC`, `PC_APPS`, `PC_WINDOWS`, `PC_AUDIO`, `PC_DISPLAY`, `PC_INPUT`,
`PC_POWER`, `PC_SCREEN`, `FILES`, `BROWSER`. A message matching several
categories gets the union, which is how "abre a calculadora e mete-a à direita"
reaches both `PC_APPS` and `PC_WINDOWS` in one turn.

Tool filtering is **not** permission. Narrowing what the model can request only
reduces noise and cost; everything it does request still travels the full
pipeline.

## Explicitly unsupported in V2

Arbitrary command/shell/PowerShell execution · process kill · permanent file
deletion · registry edits · Windows services · software install/uninstall ·
click-at-a-pixel and pointer movement · keyboard macros and raw scan codes ·
OCR and screen understanding · browser automation and DOM interaction ·
clipboard history or background monitoring · credential management ·
autonomous background computer control.

**Unsupported is a declaration, not just a list here.** This section is prose,
and prose is unreadable by the model — which is how Nano came to answer
"Executa PowerShell e corre Get-Process" with "Precisamos de confirmar…
Pretende prosseguir?". Confirmation is how Nano asks for permission to do
something it *can* do; offering it for an absent capability tells the person
their Yes is the missing ingredient.

`core/capabilities.py` is now the machine-readable half. Shell execution is
declared there, and three layers read the same entry:

* `Brain._build_system_prompt` injects a grounding block **only** on turns
  whose request matches, so an ordinary "abre o Spotify" pays no tokens for
  it;
* `Brain._run_tool` and `ToolExecutor._authorize` refuse a matching tool name
  with status `unsupported_capability`, **before** any confirmation path;
* `PolicyEngine` blocks the capability outright, and `remove_rule` refuses to
  unblock it, so it cannot be approved, granted or allow-listed either.

Adding an entry is a security decision: it asserts that no handler implements
the capability, and `tests/test_capability_awareness.py` checks that claim
against the live registry.

### The shell that was still there

Until the V2 checkpoint audit, `ToolExecutor` registered a real `shell.execute`
tool running `subprocess.run(["cmd", "/c", <model string>])`, gated by nothing
but an approval dialog. It was never *advertised* to the model — but
`Brain._run_tool` dispatches whatever name the model emits, so invisibility was
not de-authorisation, and one confirmed call would have run arbitrary
PowerShell. `plugins/god_mode.py` claimed no PowerShell call site remained
anywhere in the repository; that claim was false while this registration stood.
The tool, its handler and its risk classifier are gone. `_run_project_tests` is
the only remaining subprocess call in the executor, and it picks its argv from
a closed allow-list with `shell=False`.

## Future expansion points

* **Media session state.** `pc_media_control` sends the transport key and says
  honestly that it cannot confirm an application acted on it. Reading real
  playback state needs the Windows media-session API (GSMTC), which needs a
  WinRT binding.
* **Local OCR**, with a consent model of its own.
* **Browser Agent**, with per-site consent and no credential access.
* **Region screenshots** and per-monitor capture.
* A clarification round-trip surfaced in the voice overlay, using the
  `ambiguous` candidate lists these tools already return.
