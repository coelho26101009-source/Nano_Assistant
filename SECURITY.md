# Security Policy

Nano runs on a person's own computer, holds an API key, can open applications,
move windows, read system state and capture the screen. A security bug here is
not abstract. Reports are taken seriously and are welcome.

## Project status

Nano is in **active development and has not had a public release**. There is no
released version, no installer and no published build. Everything below
describes the `main` branch.

| Version | Supported |
| --- | --- |
| `main` (development) | Yes — fixes land here |
| Any tagged release | None exist yet |

Because there is no release channel yet, there is also no backporting policy.
When the first public beta ships, this table will list the versions that
actually receive fixes.

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

**The execution pipeline.** Nano's central invariant is:

```
MODEL → REQUEST → POLICY → PERMISSION → TOOL EXECUTOR → NARROW TOOL → REAL RESULT
```

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
* **`core/local_control_plane.py` and the eel bridge** — roughly seventy
  functions are reachable over a local WebSocket, including the approval
  controls. Origin enforcement lives here.
* **`electron/main.js` and `electron/preload.js`** — process isolation, the
  preload surface, navigation restrictions, and the Content Security Policy.
* **`core/secret_store.py`** — the API key. It is stored OS-encrypted and must
  never reach the renderer, a log, the clipboard, or a tool result.
* **Prompt injection** — external content (web pages, file contents, tool
  output) is data, never instructions. It may not grant a permission.

## Known limitations

Stated plainly rather than left for someone to discover:

* **A native process running as the same user is not defended against.** The
  Origin check on the local control plane stops a malicious *web page*; it
  cannot stop a local program that simply sends a different header. A process at
  that privilege level can already read the credential store and modify Nano's
  own files, so this is an accepted limitation, not an oversight.
* **In CLOUD and AUTO modes, message text is sent to Groq.** See `PRIVACY.md`.
* **No code signing yet.** Nothing is packaged or signed, so there is no
  supply-chain guarantee on a built artifact — because there is no built
  artifact.
