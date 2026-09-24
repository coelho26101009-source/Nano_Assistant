# Releasing Nano

**Current public release: [`0.1.0-beta.1`](https://github.com/coelho26101009-source/Nano_Assistant/releases/tag/v0.1.0-beta.1)**,
published on 2026-09-18 as a GitHub **pre-release**. Only the unsigned NSIS x64
installer and `SHA256SUMS.txt` are attached; the MSI is built by the workflow but
not published. There is no automatic updater: Beta updates are manual.

The Beta shipped with the open items in
[`PUBLIC_RELEASE_CHECKLIST.md`](PUBLIC_RELEASE_CHECKLIST.md) still unchecked,
including clean Windows validation. They carry forward to the next release.

## Versioning

Nano follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

Before `1.0.0`, the promise is weaker on purpose — the interface, the capability
set and the settings schema are all still moving.

| Stage | Example | Means |
| --- | --- | --- |
| Alpha | `0.1.0-alpha.1` | Internal. Expect breakage; no upgrade path. |
| Beta | `0.1.0-beta.1` | Public beta. Usable, still changing; data may need migration. |
| Release candidate | `1.0.0-rc.1` | Feature-frozen; only fixes. |
| Stable | `1.0.0` | Supported. Breaking changes wait for `2.0.0`. |

Pre-release tags sort correctly and mark a GitHub release as a pre-release
automatically, which keeps a beta from looking like a finished product.

### The one place a version lives

`version.json` at the repository root is the single source of truth:

```json
{ "product": "0.1.0-beta.1", "display": "v0.1.0-beta.1", "name": "Nano Assistant", "channel": "beta" }
```

`core/version.py` and `frontend/lib/version.ts` read it. Before this existed
there were four version strings in three languages and they disagreed — the UI
said `v1.0` while the backend reported `8.1.0`.

**Reconciled in the packaging pass.** `electron/package.json` and
`frontend/package.json` used to carry a legacy `8.1.0`, and `electron-builder`
stamps the installer from the Electron one — so an installer built before that
pass would have called itself version 8.1.0 of a product whose interface said
`v1.0`. All three now hold the same string, as do both `package-lock.json`
files, and `electron/test/packaging.test.js` fails if they drift.

`channel` was `stable`, which the Settings panel rendered to the user as
“Canal: stable” for a product that has never been released. It is now `beta`.

### Bumping the version

Six version records, one number, and a rebuild:

1. `version.json` — `product`, `display` and, if the stage changed, `channel`.
2. `electron/package.json` and `electron/package-lock.json` (root `version` and
   `packages[""].version` — leave any dependency that happens to share the old
   number alone).
3. `frontend/package.json` and `frontend/package-lock.json`, likewise.
4. **Rebuild the frontend.** `frontend/lib/version.ts` imports `version.json` at
   *build* time, so `frontend/out` freezes whatever the version was when it was
   exported. Skipping this produced an installer whose title bar said `v1.0`
   while the shell and backend both reported `0.1.0-beta.1`.
   `electron/scripts/verify-package-inputs.js` now refuses to package a stale
   export, but the rebuild is still the thing to do.

Then `cd electron && npm test` — the packaging suite checks all six files agree.

### One limitation worth knowing

Windows Installer's `ProductVersion` field accepts only numeric
`major.minor.build`, so the MSI records the numeric version and drops `-beta.1`.
The executable's Windows `ProductVersion` metadata is also numeric `0.1.0.0`.
Its readable `FileVersion`, the version displayed throughout Nano, package
manifests and installer filenames retain the product identity `0.1.0-beta.1`.
Do not interpret numeric Windows metadata as a stable-release claim. The
practical consequence: **Windows would not consider `0.1.0-beta.2` an upgrade
over `0.1.0-beta.1` via MSI**, because both are `0.1.0.0`. Bump the numeric part
for any MSI a user is expected to upgrade over.

## Release flow

```
  update version.json + package.json versions
        │
        ├─ update CHANGELOG.md (move [Unreleased] into the new version)
        │
        ▼
  full test gate, green            ← required; see below
        │
        ▼
  git tag -a v0.1.0-beta.1 -m "…"  ← annotated, signed if a key exists
  git push origin v0.1.0-beta.1
        │
        ▼
  CI (manual dispatch, publish_release: true)
        │
        ├─ tests
        ├─ frontend build
        ├─ embedded Python runtime
        ├─ electron-builder → Nano-Setup-0.1.0-beta.1-x64.exe, .msi
        ├─ SHA256SUMS.txt
        │
        ▼
  GitHub prerelease (only with explicit publish input and matching version tag)
```

### The test gate

A release requires everything green, on a Windows machine, from a clean
checkout. CI now runs the real-Chromium harnesses too, in the `chromium-ui`
job, on Linux under xvfb -- so what is left uncovered is narrower than it was:
the Windows rendering path, and the real-application checks, which need a
human.

```bash
python -m pytest -q                     # backend
cd electron && npm test                 # desktop shell
cd frontend && npx tsc --noEmit         # types
cd frontend && npm run build            # production bundle

cd electron
npx electron test/render-check.js       # layout, 1920 → 940×620
npx electron test/settings-drive.js     # behaviour in real Chromium
npx electron test/csp-check.js          # Content Security Policy
```

Plus, by hand, because a green suite has never been sufficient in this project:

* launch the real desktop app and use it
* one voice turn end to end
* one PC Control action that asks for confirmation, and one that does not
* switch AUTO → CLOUD → LOCAL and confirm the provider actually changed

The `nano-test-gate` project skill is the canonical checklist.

### Artifacts and checksums

| Artifact | Purpose |
| --- | --- |
| `Nano-Setup-0.1.0-beta.1-x64.exe` | NSIS installer (primary, per user) |
| `Nano-Setup-0.1.0-beta.1-x64.msi` | MSI, for managed environments |
| `SHA256SUMS.txt` | Checksums for both |

Checksums are generated in CI and attached to the release, so a download can be
verified independently of the transport:

```powershell
Get-FileHash Nano-Setup-0.1.0-beta.1-x64.exe -Algorithm SHA256
```

### Release notes

Written for a user, not a changelog reader. Lead with what changed for them;
put the internals below the fold. Every release must state:

* what is new
* what broke, and what to do about it
* **what data leaves the machine**, if that changed — see [PRIVACY.md](../PRIVACY.md)
* any new capability that can act on the computer
* known limitations, honestly

A beta release must say it is a beta in the first sentence.

## Code signing

**Not implemented.** The installer is unsigned. Windows may show an unknown
publisher or SmartScreen warning. Communicate that before an unsigned Beta is
downloaded; do not ask users to disable Windows security.

When it is set up:

* Choose a supported signing service or certificate and validate the result.
* The certificate lives in GitHub Actions secrets, **never** in the repository.
* Only the manual release workflow may access it — never a workflow triggered by
  a pull request, because a fork's PR must never be able to reach a signing key.
* Sign both installers and verify with `signtool verify /pa` before publishing.

## Rollback

Once people have downloaded a build, it cannot be recalled — so the plan is
containment, not undo.

1. **Mark the release as a pre-release** and edit its notes to describe the
   problem at the top. Do not delete it: deleting breaks anyone trying to
   diagnose what they already installed.
2. **Publish the previous version** as the recommended download.
3. **Fix forward.** Ship a patch release rather than re-cutting the same version
   — a version that means two different builds is worse than the bug.
4. **If it is a security issue**, publish a GitHub Security Advisory and follow
   [SECURITY.md](../SECURITY.md).

Never move or re-point an existing tag.

### Data migration

Nano stores conversation history, settings and memory in
`%LOCALAPPDATA%\NanoAssistant`. A release that changes those formats must
migrate forward, and must not assume a user can roll back — an older build
reading a newer database is the failure mode that loses somebody's data.

## What does not exist yet

Stated plainly so nothing here reads as more finished than it is:

* No code signing.
* No update mechanism — a user with `0.1.0` will not learn that `0.2.0` exists.
* A developer-host smoke does not prove installation on a clean Windows machine.
* MSI prerelease-only upgrades need a numeric-version policy before Beta 2.

## Building and validating locally

Use Node 22.12+ and Python 3.12 on Windows x64. From the repository root:

```powershell
Push-Location frontend
npm ci
npx tsc --noEmit
npm run build
Pop-Location
./scripts/prepare_windows_runtime.ps1
Push-Location electron
npm ci
npm run fetch-electron
npm test
npm run build:all
node scripts/verify-built-package.js
node test/packaged-smoke.js 'dist-electron/win-unpacked/Nano Assistant.exe'
Pop-Location
```

The runtime preparer stages a fresh embedded interpreter, verifies pinned
download hashes and dependency imports, then replaces only `runtime/python`.
The package gate rejects missing resources, mismatched frontend version and
private files. It never copies the broader `runtime/` developer-data tree.

The smoke creates its own temporary profile, strips inherited credentials,
checks the guide/settings/SQLite/restart and requests a normal quit. Its
loopback debugging port is a test-launch flag only. It retains synthetic
evidence in the printed temporary folder. Ollama is a shared detached service
and deliberately survives Nano shutdown; Nano's Python and Electron processes
must terminate. The same command accepts the installed executable's path.

Before distribution, inspect `app.asar`, resources, executable product/file
versions and MSI metadata, then run the checklist on clean Windows. NSIS
uninstall preserves `%LOCALAPPDATA%\NanoAssistant` and Electron shell data;
no wipe option is supplied. See [BETA_GUIDE.md](BETA_GUIDE.md).
