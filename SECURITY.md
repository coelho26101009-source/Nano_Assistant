# Security Policy

Nano runs on a person's own computer, holds an API key, can open applications,
move windows, read system state and capture the screen. A security bug here is
not abstract. Reports are taken seriously and are welcome.

## Project status

Nano is in **active development**. The current public release is a Beta;
security fixes land on `main` and can be included in later Beta releases.

| Version | Supported |
| --- | --- |
| `main` (development) | Yes — fixes land here |
| `0.2.0-beta.1` | Current public Beta; update manually when a fix is released |
| `0.1.0-beta.1` | Previous Beta; update to the current Beta |

There is no automatic updater or promised backport schedule.

## Reporting a vulnerability

**Please do not open a public issue for anything exploitable.** A public issue
is visible to everyone the moment it is filed, including before a fix exists.

Use **GitHub's private vulnerability reporting** instead:

1. Go to the repository's **Security** tab.
2. Choose **Report a vulnerability**.
3. Describe the issue, the impact, and how to reproduce it.

This creates a private advisory visible only to the maintainers. It is GitHub's
built-in channel and needs no email address, which is deliberate: this project
does not publish a personal contact address, and a security process should not
depend on one being monitored.

If private reporting is not enabled or not available to you, open a public issue
containing **only** the sentence "I would like to report a security issue
privately" and no details. A maintainer will open a private channel.

### What helps

* what you did, and what happened
* why it is a security problem rather than a bug
* the smallest reproduction you have
* the commit you tested

### What to leave out

Do not include your API keys, your `.env`, unredacted logs, screenshots of your
own desktop, or personal files. A description of the flaw is worth more than a
capture of your machine, and a report should never be the reason your
credentials leak.

### What to expect

This is a small project with no paid staff and no on-call rotation, so no
response-time guarantee is offered — promising one that cannot be kept would be
worse than promising nothing. Reports are read, and credible ones are acted on.
Please allow a reasonable period for a fix before disclosing publicly.

## Security-sensitive areas

If you are looking for somewhere to start, these are the parts of Nano where a
mistake matters most.

**The execution pipeline.** Nano's central invariant, as implemented in
`core/tool_execution.py`:

```
MODEL
  → capability resolution
  → argument validation (registered schema)
  → scope classification / target resolution
  → POLICY
  → PERMISSION
  → TOOL EXECUTOR
  → NARROW TOOL → OS
  → VERIFIED RESULT → AUDIT
```

Schema validation happens **before** the policy decides, so an argument the
policy reasons about is an argument the handler will actually receive. The
shorter `MODEL → REQUEST → POLICY → PERMISSION → EXECUTION` shorthand used in
other documents is a summary of this chain, not a different one.

No model output may reach the operating system except through a narrow tool with
typed arguments. Anything that shortens that chain is a vulnerability, even if
it never executes in practice. See `docs/SECURITY_POLICY.md`.

* **`core/policy_engine.py`, `core/permission_manager.py`, `core/tool_execution.py`** —
  the authority chain. Bypasses, capability confusion, grants that are broader
  than the approval that created them.
* **`core/capabilities.py`** — the declaration of what Nano deliberately cannot
  do. Anything that makes a declared-absent capability reachable.
* **`plugins/`** — plugin tools reach the model. A plugin that spawns a process,
  builds a command line, or writes outside its scope is the exact class of bug
  this project has already had three times.
* **`core/pc_control/`** — every Windows effect. Path containment, target
  binding, protected locations, confirmation on destructive actions.
* **`core/local_control_plane.py` and the eel bridge** — every backend function
  the renderer can call is reachable over a local WebSocket, including the
  approval controls (`confirm_action`, `resolve_permission`,
  `set_emergency_stop`). The surface has grown with the product and is now well
  over a hundred functions, which is exactly why Origin enforcement lives here
  rather than being assumed.
* **`electron/main.js` and `electron/preload.js`** — process isolation, the
  preload surface, navigation restrictions, and the Content Security Policy.
* **`core/secret_store.py`** — the API key. It is stored OS-encrypted and must
  never reach the renderer, a log, the clipboard, or a tool result.
* **`core/schema_validation.py`** — the central argument validator. Every tool
  call is normalised here before the policy sees it, so a divergence between
  what the policy judged and what the handler runs is a vulnerability.
* **The Execution Ledger in `core/brain.py`** — per-turn deduplication of tool
  calls across a provider failover, including calls still in flight. A hole here
  means a real effect on the machine happening twice.
* **`core/mobile_link.py` and `core/mobile_server.py`** — NANO on the phone.
  Off by default. When on, a small HTTP server listens on the local network.
  Pairing needs the one-time QR secret (256-bit, 5 minutes) AND an approval on
  the PC; each phone then has its own 256-bit key, and every request and answer
  is AES-256-GCM sealed and bound to the device, the request id and the
  direction, with a ±5-minute window and replay rejection. A phone can call only
  the methods registered in `core/main.py` (chat, conversations, agenda, memory,
  approvals, status): never settings, keys, permissions or the emergency stop.
  Approvals it answers are the same approvals the PC shows. Removing a phone on
  the PC deletes its key. Anything that widens that method list, or lets a
  phone pair without the PC's yes, is a vulnerability.
* **Prompt injection** — external content (web pages, file contents, tool
  output) is data, never instructions. It may not grant a permission.

## Known limitations

Stated plainly rather than left for someone to discover:

* **A paired phone is as trusted as the PC window.** It can read your
  conversations, agenda and memory and answer approval requests. Losing an
  unlocked phone means removing it in Settings → General → Phone. The phone's
  key lives in the Android Keystore and is excluded from backups.

* **A native process running as the same user is not defended against.** The
  Origin check on the local control plane stops a malicious *web page*; it
  cannot stop a local program that simply sends a different header. A process at
  that privilege level can already read the credential store and modify Nano's
  own files, so this is an accepted limitation, not an oversight.
* **In CLOUD and AUTO modes, message text and its assembled context are sent
  to a cloud provider** — Groq, Mistral or Google (Gemini). In AUTO a provider
  that was tried and failed has already received the request before the next one
  answers, so the provider named in the interface is the one that *answered*,
  not the only one that saw the message. See `PRIVACY.md`.
* **Tool results are sent to the provider.** A tool you approved — reading the
  clipboard, reading a file, extracting a web page — returns content that
  becomes part of the request so the model can continue. Approval and
  non-transmission are different things. Screenshots are the exception: the tool
  returns a path and a size, never pixels.
* **The Execution Ledger deduplicates identical calls within one turn, and that
  is all it claims.** It stops a second provider from replaying a call it can
  see in the history, and stops two identical calls in one response from both
  executing. It is not a transactional guarantee across turns, and it does not
  make a non-idempotent tool idempotent in general.
* **No code signing yet.** Nothing is packaged or signed, so there is no
  supply-chain guarantee on a built artifact — because there is no built
  artifact.
