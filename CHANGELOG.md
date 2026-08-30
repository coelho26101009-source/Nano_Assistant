# Changelog

All notable changes to Nano are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project intends to follow [Semantic Versioning](https://semver.org/)
from its first public release onward.

## About the entries below

**Nano has never had a public release.** There are no version tags in this
repository, so nothing below carries a version number — inventing one would
imply a release that never happened and that nobody can download.

Dates are the real dates of the commits that delivered the work, taken from the
repository history. Entries are grouped by the milestone they belong to rather
than by release, because a release is not what produced them.

The first tagged version will become `## [0.1.0-beta] - YYYY-MM-DD` at the top
of this file, with everything below it folded into it as the initial release.
See [docs/RELEASING.md](docs/RELEASING.md).

---

## [Unreleased]

### Public release foundation — 2026-08-30

**Security**

- Withdrew the `context_switcher` plugin. It exposed `context_activate_mode` to
  the model and its handler ran PowerShell twice (volume and a Focus Assist
  registry write), force-killed processes with `taskkill /F`, launched
  applications through `subprocess.Popen(..., shell=True)` — the shipped
  `hacker` mode opened Windows Terminal, an interpreter `pc_app_launch`
  explicitly refuses — and resolved its mode file from a model-supplied name
  with no path containment. It presented as a low-risk "activate work mode"
  confirmation.
- Removed `launch_process()` and `kill_process()` from `core/desktop_agent.py`.
  Both used `shell=True`; neither was referenced anywhere, which is precisely
  why they were worth deleting rather than leaving.
- Added Origin enforcement to the local control plane
  (`core/local_control_plane.py`). eel performs no Origin validation at all, so
  any web page could have opened Nano's WebSocket and called roughly seventy
  exposed functions — including `confirm_action`, `resolve_permission` and
  `set_emergency_stop`, the entire approval surface.
- Added a Content Security Policy to the Electron main window, verified against
  the real production bundle with a negative control proving it enforces.
- Removed `core/project_agent.py` after a repository-wide reference audit found
  it genuinely unreferenced. It ran `git` and `pytest` in a caller-supplied
  directory with no path containment; running pytest in an attacker-chosen
  directory would have executed that directory's `conftest.py`.

**Licensing**

- Nano is now licensed under **Apache License 2.0** (`LICENSE`), a decision
  made by the project owner. `THIRD_PARTY_NOTICES.md` and
  `docs/PUBLIC_RELEASE_CHECKLIST.md` updated accordingly; third-party
  obligations (notably the LGPL dependencies `edge-tts` and `pygame`) still
  apply independently of Nano's own licence.

**Documentation**

- Added `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SUPPORT.md`,
  this changelog, issue and pull-request templates.
- Added `PRIVACY.md` documenting the real data flows per provider mode, and
  `THIRD_PARTY_NOTICES.md` drafted from installed dependency metadata.
- Added `docs/RELEASING.md` and `docs/PUBLIC_RELEASE_CHECKLIST.md`.

**Continuous integration**

- Reworked CI into fast cross-platform checks plus a Windows job, with least
  privilege, pinned action majors, dependency caching, and no secrets.

### Settings V2 and information architecture — 2026-08-29

- Reorganised navigation so each destination answers one question: Chat,
  Ferramentas (a capability catalogue read from the live tool registry), PC
  (this computer's state, permissions and activity), Memória, Definições.
- Rebuilt Settings into seven categories behind a rail: Geral, IA, Voz, PC
  Control, Memória, Privacidade, Sobre.
- Made the top-right AI pill a working mode selector for AUTO / CLOUD / LOCAL,
  sharing one source of truth with Settings → IA and persisting the choice.
- Established a single canonical product version in `version.json`, read by the
  frontend, the Electron shell and the Python backend, replacing four
  independent version strings that disagreed.
- Fixed the fallback report naming the provider and model that had *failed*
  rather than the one that actually answered.

### PC Control V2 Ultra — 2026-08-29

- Expanded Windows control across applications, windows, audio and media,
  files and folders, web, display, input and clipboard, system, and power and
  session — every capability a narrow tool with typed arguments.
- Declared what Nano deliberately cannot do in `core/capabilities.py`, so the
  model is grounded to say so plainly instead of offering a confirmation for a
  capability that does not exist.
- Removed a live `shell.execute` tool from the executor. It ran
  `subprocess.run(["cmd", "/c", <model string>])` behind an approval dialog and
  was reachable by name even though it was never advertised.
- Rebuilt approval cards around what a person can judge — action, target, scope
  — with a preview where the size of the decision is not visible from the target.

### Ember interface and secure desktop architecture — 2026-08-23 → 2026-08-26

- Redesigned the desktop interface around the Ember visual identity.
- Shipped the Electron desktop shell with `contextIsolation`, `sandbox`,
  `nodeIntegration: false`, a narrow preload, restricted navigation and denied
  permission requests.
- Added the global voice hotkey and the voice overlay window.
- Added secure PC control with a robust local fallback.

### Voice and speech accuracy — 2026-08-22 → 2026-08-23

- Unified the voice runtime and the wake-turn handling.
- Moved Portuguese speech recognition to Whisper `small`, measurably more
  accurate than `tiny` at a higher CPU cost, and left wake-phrase listening off
  by default as a result.

### Provider routing and fallback hardening — 2026-08-19 → 2026-08-21

- Added AUTO / CLOUD / LOCAL provider modes with a single routing authority:
  CLOUD never silently downgrades, LOCAL never contacts the cloud (not even for
  a status probe), and an AUTO fallback is always visible rather than silent.
- Added provider and secret management with OS-encrypted key storage, and
  overhauled the settings interface.
- Hardened the core execution path and redesigned the command centre.

### Foundations — 2026-08-16 → 2026-08-20

- Initial project structure, the permission and policy architecture, the
  launcher, and the retirement of the legacy HELIOS branding.
