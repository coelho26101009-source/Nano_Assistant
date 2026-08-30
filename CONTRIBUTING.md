# Contributing to Nano

Nano is an AI desktop assistant that can act on a real computer. That shapes
everything below: the bar for a change is not only "does it work" but "can it
still not do the things it says it cannot do".

Please read [`SECURITY.md`](SECURITY.md) before your first pull request, and
never open a public issue for a vulnerability.

## Development setup

Nano needs **Windows** for the PC Control features, **Python 3.12** and
**Node 20**. The Python test suite runs on any platform; the Windows-specific
tests skip elsewhere.

```bash
# Backend
python -m pip install -r requirements.txt
python -m pip install -r requirements-test.txt   # test-only, no audio/GUI wheels

# Frontend
cd frontend && npm ci && npm run build

# Desktop shell
cd electron && npm ci
```

Run it:

```
NANO_DESKTOP.bat     # the real desktop app (Electron window)
NANO.bat             # the same UI in a browser, for development
```

An API key is not required to develop: Nano runs in LOCAL mode against Ollama,
and the whole test suite runs without any credential.

## Testing

**Every change needs the suites its area touches. A green suite is necessary,
not sufficient — run the real thing too.**

```bash
python -m pytest -q                 # backend
cd electron && npm test             # desktop shell
cd frontend && npx tsc --noEmit     # types
cd frontend && npm run build        # production bundle
```

For UI work there are two harnesses that render the **real production bundle**
in Electron's own Chromium rather than reasoning about CSS:

```bash
cd electron
npx electron test/render-check.js     # layout at 1920 → 940×620
npx electron test/settings-drive.js   # behaviour: navigation, popovers, focus
npx electron test/csp-check.js        # the Content Security Policy
```

House rules that came from real incidents:

* **Never weaken a test to make the build green.** Fix the code, or correct an
  assertion that was measuring the wrong thing — and then it must guard the
  original contract more precisely, not less.
* **Prefer behavioural tests to source-string assertions.** A test that greps
  for `nodeIntegration: false` passes the day the line moves into a comment.
* **Strip comments *and* docstrings before scanning source.** A tombstone
  explaining a removed hazard quotes the hazard; a naive scan matches the
  explanation. See `executable_source()` in
  `tests/test_public_release_security.py`.
* **Prove a new guard is not vacuous.** Reintroduce the defect, watch the guard
  fail, then restore the fix.

## Security expectations for contributors

These are not style preferences. A pull request that breaks one of them will not
be merged regardless of how useful the feature is.

### There is no arbitrary shell, and there never will be

Nano has **no** PowerShell, CMD, terminal, bash, script execution or generic
command executor, and this is a deliberate architectural decision rather than a
missing feature. `core/capabilities.py` declares it absent, `PolicyEngine`
blocks the capability outright, and the model is grounded to say so plainly
instead of offering a confirmation for something that cannot happen.

Do not add:

* `subprocess` with `shell=True`
* `os.system`, `os.popen`, `eval`, `exec` on model-influenced input
* a tool that takes a command string
* a "run this for me" escape hatch of any shape

This has been violated three times in this project's history — by
`plugins/god_mode.py`, by a live `shell.execute` tool in the executor, and by
`plugins/context_switcher.py`. Each looked harmless. Each is now a tombstone
file that explains exactly what went wrong; they are worth reading.

If you genuinely need the OS to do something, add a **narrow tool**.

### How to propose a new PC Control capability

1. **Start with the human sentence.** "Close the window I'm looking at" is a
   capability. "Run a command" is not.
2. **Typed arguments only.** The model chooses a tool and values; it never
   composes a command line. Values reach a Win32 call as parameters.
3. **Declare it properly** — a capability id in `core/policy_engine.py`, a risk
   level, a scope, and a permission target that binds the grant to *this* target
   (approving "close Calculator" must not authorise closing Discord).
4. **Decide the confirmation honestly.** Reversible and read-only can run
   directly. Anything that closes, writes, deletes, captures the screen or
   touches the session asks first, showing the action, the target and the scope.
5. **Never force.** "Delete" means the Recycle Bin. "Close" means asking the
   application, not `taskkill /F`.
6. **Refuse ambiguity.** An unresolvable or ambiguous target fails closed.
7. **Verify the real result.** Report what actually happened, never what was
   attempted. Do not claim success from a call that returned.
8. **Add tests**, including the refusal paths.

The `nano-pc-control` and `nano-security` project skills in `.claude/skills/`
carry the full checklist.

### Secrets

* API keys live in `core/secret_store.py`, OS-encrypted (DPAPI on Windows).
* A secret must never reach the renderer, a log line, a tool result, the
  clipboard, an audit entry, or a permission payload. The backend sends a
  *masked* description, never the value.
* Never commit `.env`. It is gitignored; keep it that way.
* Never paste a real key into an issue, a pull request, a test fixture or a
  screenshot.

## Branches and commits

Work on a branch off `main` and open a pull request; `main` is not committed to
directly.

Commit messages in this repository are short, imperative and describe the
outcome — `Harden core execution path and redesign the command center UI`,
`Add secure PC control and robust local fallback`. Match that. No strict
convention is enforced, but a message that says what changed and why beats a
prefix taxonomy.

## Code style

Follow what the file you are editing already does.

**Python** — 4 spaces, type hints on new public functions, `from __future__
import annotations` at the top of modules that use them. Modules and non-obvious
functions carry a docstring that explains *why*, not just *what*; several
explain a specific bug they exist to prevent, and that is the house style.
Comments are in English; user-facing strings are in **Portuguese (Portugal)**.

**TypeScript/React** — functional components, explicit prop types, no `any`
where a real type is available. Styling goes in `frontend/styles/globals.css`
using the existing Ember design tokens; do not introduce new colours. `npx tsc
--noEmit` must be clean.

**Honesty in the interface.** Nano must never display a state it has not
measured. If a value is unknown, the UI says unknown — it does not default to a
reassuring one, and it does not show a control that cannot do anything. Hide an
unavailable option rather than rendering a dead toggle.
