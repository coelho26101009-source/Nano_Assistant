# Changelog

All notable changes to Nano are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project intends to follow [Semantic Versioning](https://semver.org/)
from its first public release onward.

## About the entries below

`0.1.0-beta.1` is Nano's **first public release**. Everything under it is the
work that produced it, grouped by milestone rather than by version, because
those milestones predate the existence of any release to attach them to.

Dates are the real dates of the commits that delivered the work, taken from the
repository history. See [docs/RELEASING.md](docs/RELEASING.md) for the
versioning policy.

---

## [0.1.0-beta.1] - 2026-09-18

The first public Beta. Pre-release: not a stable version, not 1.0.

### Interface

- Rebuilt the interface around a warm cream, charcoal and terracotta design
  system. The previous system was near-black with the flame red of the mark
  spent on navigation, primary actions, focus and permission emphasis at once;
  that read as a gaming skin rather than as desktop software, and one red
  family carrying both the brand and "this is dangerous" left the two
  indistinguishable. The brand accent is now a terracotta and danger a cooler
  crimson, and they are never the same hue.
- Colour is a semantic role rather than a literal, so the dark theme and the
  charcoal conversation rail redefine tokens and every component follows.
- Four text roles replace two, fixing helper copy and metadata that rendered
  identically.
- Focus rings are warm rather than steel blue; every text role clears 4.5:1
  against every surface it can sit on, in both themes, measured off the painted
  page rather than derived from the stylesheet.
- A provider that is missing or unconfigured now shows a hollow amber ring
  instead of an indicator that read as healthy. Provider routing is unchanged.
- The first-run guide owns the whole stage instead of sharing it with the
  composer, which was the source of the cramped onboarding at 940x620.
- Assistant turns are unbubbled and flush with the reading column; user turns
  keep a bubble.
- Fixed a hand-drawn SVG "flame" on the About page whose inner path referenced
  a token that has never existed, painting a black blob inside the logo. It now
  uses the supplied mark.

### Release

- First tagged version, published as a GitHub pre-release with the NSIS
  installer and `SHA256SUMS.txt`.
- Windows binaries are **unsigned** in this Beta.
- There is no automatic updater; Beta updates are manual.
- The MSI is built and verified but not published in this release: a full
  managed-installer lifecycle has not been validated.

---

## [Unreleased]

### Documentation truth pass — 2026-09-09

- Audited every public document against the code and corrected what had gone
  stale. `PRIVACY.md`, `README.md` and `docs/architecture/MODEL_ROUTING.md` were
  rewritten; `SECURITY.md`, `docs/SECURITY_POLICY.md`,
  `docs/architecture/ARCHITECTURE.md`, `docs/VOICE.md`, `docs/README.md`,
  `docs/architecture/PC_CONTROL.md` and `docs/PUBLIC_RELEASE_CHECKLIST.md` were
  corrected in place.
- The material corrections: provider documentation described Groq as the only
  cloud provider, and Mistral and Gemini appeared in no user-facing document at
  all; AUTO and CLOUD mode semantics were wrong; conversations were described as
  read-only; Memory/RAG was listed as roadmap and the Second Brain was
  undocumented; `edge-tts` was described as a *local* TTS provider when it sends
  the spoken text to Microsoft; a "cloud audio fallback" was documented that has
  no implementation; the count of eel-exposed functions was stale; and
  `MODEL_ROUTING.md` described `core/model_router.py` as if it decided the
  provider, which it never has.
- `PRIVACY.md` now states two things it previously got wrong: that a cloud
  provider tried and failed during AUTO failover **has already received the
  request**, and that approved tool results — clipboard text, file contents,
  extracted web pages — are sent to the provider so the model can continue.
  Screenshots remain the genuine exception: the tool returns a path and a size,
  never pixels.
- Documentation only; no production, frontend or test source changed.

### Provider routing evidence and AUTO order — 2026-09-09

- Replaced the cloud fallback order with a **measured** one. It had been
  `(google, groq, mistral)`, which is alphabetical and was nobody's decision;
  it is now `groq → mistral → google`, then Ollama as the terminal local hop.
- Committed the benchmark that decided it to `benchmarks/provider_routing/` —
  a 53-case synthetic corpus, per-case verdicts and latencies, the scoring
  weights stated before the numbers were read, and the caveats: one account,
  one day, one run per case, and Google under-measured by rate limits. The
  artifact deliberately makes no claim that any vendor is objectively better.
  A test fails if the constant and the exported results stop agreeing.
- Clarified throughout that `preferredCloud` (a user setting, always the first
  hop) and `CLOUD_PROVIDER_IDS` (the system order for everyone after it) answer
  different questions. `DEFAULT_CLOUD_PROVIDER` stays `groq`; this run
  reaffirmed it rather than changing it.
- Model defaults may now be adopted from an account's own catalogue for Google
  and Mistral, reported through a `model_source` field, and never override a
  model the user chose.

### Windows close lifecycle — 2026-09-09

- `window.close` now verifies the outcome against the window's **identity** —
  handle plus owning process id — rather than the handle alone, because Windows
  recycles handles and a new window can land on the same integer.
- A poll where the owner PID cannot be read falls back to trusting `IsWindow`
  for that poll instead of comparing against a zero and reading as a mismatch,
  which had reported a window closed after one flaky read.

### Memory extraction V2 — 2026-09-08

- Inferred facts are now **scored** rather than all being parked as inert
  candidates: a high-confidence inference becomes an active memory, a weaker one
  stays a candidate, and a weaker one still is discarded. Extraction remains
  deterministic and local — never a model call, because a classifier deciding
  what to remember would be a second, unauditable authority over the store.
- Separated durable facts from passing reminders, so a reminder no longer
  becomes something Nano believes about you permanently.

### Execution Ledger hardening — 2026-09-08

- The per-turn duplicate-execution ledger is now keyed on the **effective** call
  — the arguments after the registered schema has been applied — and that same
  normalised object is what the executor runs. Previously the same integer
  serialised three ways was three ledger identities for one effect, so a
  mid-turn provider failover could move the volume twice.
- Paths are normalised for case and separator, and enum arguments case-folded,
  so two spellings of one file or one direction are one entry.
- Identical calls **in flight** are covered too: a call is recorded before it is
  awaited, so a model emitting the same tool call twice in one response no
  longer executes it twice. Only a success is recorded durably, so a refusal
  stays retryable within the turn.

### Test reliability and real Chromium CI — 2026-09-07

- The Chromium UI tests now **actually run in CI**. Every module looked for
  `electron.exe`, a filename that cannot exist on Linux, so on CI they skipped
  and the skip read as a pass. The `chromium-ui` job now loads the production
  bundle into Electron's own Chromium under `xvfb` and runs all 57 of them.
- Fixed two harness races around CSS transitions that made graph rendering
  assertions flaky.

### Conversation continuity — 2026-09-06

- **Each turn stays bound to the conversation it was written in.** A message now
  belongs to the thread that was active when it was sent, not to whichever
  thread happens to be open when the answer arrives.
- Added bulk conversation deletion as one narrow, confirmed operation taking a
  list of thread ids — not a generic "delete where" reachable from the renderer.
  The confirmation names the count and the message total.

### Multi-provider support — 2026-09-04

- Added **Google (Gemini)** and **Mistral** alongside Groq, each with its own
  adapter, its own credential slot and its own model discovery. One key can
  never satisfy or overwrite another provider's slot.
- Surfaced all of it in the interface: per-provider keys, per-provider model
  selection, and a preferred-cloud setting.
- Routing became genuinely multi-provider: AUTO now walks the preferred cloud,
  then the remaining ready clouds, then Ollama, and reports **every hop it
  made** with what happened to each — so "you chose Gemini, it rate-limited,
  Groq finished the turn" is expressible rather than being flattened into
  "groq (fallback)".
- Hardened security grading and permission grounding alongside it.

### Conversation threads, memory and the Second Brain — 2026-08-31 → 2026-09-05

- **Conversation threads.** Real threads in SQLite, with creation, switching,
  renaming, archiving and deletion. Opening an older conversation rebuilds the
  model's context from that thread's messages and summary, which is what makes
  an old conversation writable instead of a read-only transcript.
- **Long-term memory** across threads, with retrieval, and a single context
  composer that decides what the model is told about the past — recent turns,
  the thread summary, relevant older messages, relevant memories — under a
  per-section token budget with deduplication, so CLOUD, AUTO and LOCAL receive
  logically identical context.
- **Second Brain**: nodes for the entities memories are about, and edges between
  nodes that co-occur in a memory. Nodes come only from active memories or
  explicit user action, never from raw message text. Implemented and tested; in
  practice still sparse, which is what a graph looks like before prolonged real
  use.
- One database, migrated in place under `PRAGMA user_version` and never
  replaced; existing messages were back-filled into real threads rather than
  discarded.
- Replaced trust-boundary tests that asserted on source layout with tests that
  exercise the behaviour.

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
