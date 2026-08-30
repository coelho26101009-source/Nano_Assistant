# Public Release Checklist

What is done, what is not, and what genuinely blocks a public beta.

Marks are used strictly: `[x]` only when the thing is actually finished and
verified, `[ ]` when it is not, `[~]` when it is partly there with the remainder
named. An item nobody has tested says so.

Last reviewed: **2026-08-30**, during the public-release foundation audit and
its final cleanup (licence decision, `project_agent.py` removal).

---

## Repository

- [x] Clear README describing what Nano is
- [x] `.gitignore` covers secrets, logs, runtime data, voice recordings, build output
- [x] No secrets, keys or personal data committed (audited; `.env` is ignored)
- [x] Single canonical product version (`version.json`)
- [x] Architecture documentation (`docs/architecture/`)
- [x] Security policy documentation (`docs/SECURITY_POLICY.md`)
- [~] Version alignment — `version.json` is canonical, but `electron/package.json`
      and `frontend/package.json` still read `8.1.0`. `electron-builder` stamps
      the installer from the Electron one, so this is a packaging-pass item.
- [ ] Repository description and topics set on GitHub (values recommended in the
      audit report; must be set by a human in repository settings)

## Community standards

- [x] `README.md`
- [x] `CONTRIBUTING.md`
- [x] `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1)
- [x] `SECURITY.md`
- [x] `SUPPORT.md`
- [x] `CHANGELOG.md`
- [x] Issue templates (bug, feature) with a security escape hatch
- [x] `.github/ISSUE_TEMPLATE/config.yml` — blank issues disabled so a
      vulnerability cannot arrive as an untemplated public issue
- [x] Pull request template with security and capability impact sections
- [x] **`LICENSE`** — Apache License 2.0, canonical text, chosen by the project owner
- [x] GitHub private vulnerability reporting **enabled** in repository settings
      (`SECURITY.md` instructs people to use it, so it must actually be on)
- [x] Discussions enabled, or `SUPPORT.md` updated to stop linking to it

## License

- [x] **Choose a licence.** Apache License 2.0.
- [x] Add `LICENSE` at the repository root
- [ ] Add the licence to the About panel (README.md links it; the in-app About
      panel does not yet show it)
- [~] Confirm compatibility with the LGPL dependencies below — Apache-2.0 is
      generally understood to be compatible at the application level; a formal
      legal review has not been done. See
      [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

Nano is now licensed under **Apache License 2.0**: permissive, permits
commercial reuse, modification and redistribution, and does not generally
require downstream modified applications to stay open-source. This does not
remove the obligations from third-party dependencies — see
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

## Privacy

- [x] `PRIVACY.md` documenting real data flows per provider mode
- [x] Data flows traced in code rather than assumed
- [x] Storage locations, retention and deletion documented
- [x] **Corrected a false claim**: the UI said "no modo Local, nada sai do
      computador" while `edge-tts` sends spoken text to Microsoft in every mode
- [x] Spoken-reply disclosure surfaced in Definições → Privacidade
- [x] Screenshots auto-expire (1 hour / 10 most recent)
- [x] Voice recordings deleted immediately after transcription
- [x] API key stored OS-encrypted (DPAPI), never reaches the renderer
- [ ] One-click "delete all my data" (individual controls exist; a single wipe
      does not)
- [ ] Privacy review by someone qualified, before any commercial deployment

## Security

- [x] Central authority chain: MODEL → REQUEST → POLICY → PERMISSION → EXECUTOR → NARROW TOOL
- [x] No arbitrary shell, PowerShell, CMD or script execution anywhere reachable
- [x] Unsupported capabilities declared machine-readably (`core/capabilities.py`)
- [x] Protected paths; deletion means the Recycle Bin
- [x] Grants bound to capability + target + scope; no permanent allow
- [x] Secrets never in logs, tool results, clipboard or audit entries
- [x] Prompt-injection trust boundary (external content is data, never instruction)
- [x] **Audit fix**: withdrew `context_switcher` (PowerShell, `shell=True`,
      `taskkill /F`, registry write, path traversal on a model-supplied name)
- [x] **Audit fix**: removed `launch_process`/`kill_process` (`shell=True`, dead code)
- [x] **Audit fix**: Origin enforcement on the local control plane
- [x] Regression tests for all three, proven non-vacuous
- [x] CI gate rejecting `shell=True`, `os.system`, `os.popen`, `eval`, `exec`
- [x] **Audit fix**: removed `core/project_agent.py` — unreferenced, unreachable
      helpers that ran `git`/`pytest` in a caller-supplied directory. Confirmed
      dead via a repository-wide reference audit before deletion.
- [ ] Independent security review before public beta
- [ ] Dependency vulnerability scanning (Dependabot or equivalent) enabled

### Electron

- [x] `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`
- [x] `webviewTag: false`, `webSecurity: true`
- [x] Narrow preload; no generic invoke channel
- [x] Navigation restricted to Nano's own origin
- [x] External links validated and handed to the OS browser
- [x] All device permission requests denied
- [x] Single-instance lock
- [x] **Audit fix**: Content Security Policy on the main window, verified
      against the real bundle with a negative control proving enforcement

### Local control plane

- [x] Binds to loopback only; never `0.0.0.0`
- [x] Ephemeral port
- [x] **Audit fix**: WebSocket upgrades rejected unless the Origin is Nano's own
      page — closes cross-site WebSocket hijacking of ~70 exposed functions
      including the entire approval surface
- [~] **A native local process is still not authenticated.** It can send any
      Origin. Accepted for now: a process at that privilege already owns the
      user session. A per-session token would close it if the threat model
      changes.

## Continuous integration

- [x] CI on push and pull request
- [x] Python tests on Ubuntu **and** Windows
- [x] Frontend typecheck and production build
- [x] Electron shell tests
- [x] Static security gate
- [x] No secrets required; a fork's PR runs the full suite
- [x] Least privilege (`contents: read`)
- [x] Pinned action majors, official actions only
- [x] Dependency caching
- [x] Packaging workflow made manual-only with an explicit publish opt-in
- [ ] Render/behaviour harnesses in CI (need real Chromium and a display;
      currently local-only and required before a release)
- [ ] Branch protection requiring CI to pass before merge

## Packaging

- [ ] **Professional Windows installer** — the largest remaining piece of work
- [ ] Packaged Python runtime that does not depend on a system Python
- [ ] **Zero terminal windows** — no console flashes on launch
- [ ] Installed-build validation (everything so far was validated from a checkout)
- [ ] Uninstaller that removes the application and offers to remove user data
- [ ] Install size and startup time measured and acceptable
- [~] `build-windows.yml` exists and is a reasonable starting point, but has
      never produced a validated installer

## Code signing

- [ ] Code-signing certificate obtained (OV or EV)
- [ ] Certificate stored in Actions secrets, never in the repository
- [ ] Signing wired into the release workflow only — never a PR-triggered one
- [ ] Signature verified before publishing
- [ ] SmartScreen reputation understood and communicated to early users

## Updates

- [ ] Update mechanism (none exists; a user cannot learn a new version shipped)
- [ ] Update check is opt-in or clearly disclosed — it is a network call
- [ ] Data migration strategy across versions
- [ ] Rollback guidance for users

## Onboarding

- [ ] First-run experience (there is none — Nano starts in its full interface)
- [ ] Provider setup UX: explain AUTO/CLOUD/LOCAL, and that CLOUD needs a key
- [ ] Explain what Nano can do to the computer, and that it asks first
- [ ] Surface the privacy summary during setup, not buried in Settings
- [ ] Microphone and voice setup, including that wake-phrase is off by default
- [ ] Graceful path when neither Groq nor Ollama is available

## Website

- [ ] Landing page
- [ ] Download with checksums
- [ ] Screenshots and a demo
- [ ] Public privacy and security pages
- [ ] Documentation hosting

*(Cookie/analytics policy deliberately out of scope until a website exists.)*

## Public beta

- [ ] All security items above resolved
- [x] Licence chosen and applied
- [ ] Installer built, signed and validated on a clean Windows machine
- [ ] Tested on a machine that is not the developer's
- [ ] Tested without Ollama installed
- [ ] Tested without a Groq key
- [ ] Known-issues list published
- [ ] Feedback channel staffed
- [ ] Beta expectations set explicitly in the release notes

---

## The honest summary

**Ready:** the security architecture, the permission model, the interface, the
test coverage, the community documentation, and CI.

**Not ready, and genuinely blocking:**

1. **No installer.** There is nothing for a beta tester to download.
2. **No code signing**, so the first thing a user would see is a SmartScreen
   warning on software that asks to control their computer.
3. **No onboarding.** Nano opens into its full interface and assumes the user
   knows what AUTO, CLOUD and LOCAL mean.
4. **Never validated as an installed application** — only from a development
   checkout.

The security and privacy work is in good shape. The distribution work has not
started.
