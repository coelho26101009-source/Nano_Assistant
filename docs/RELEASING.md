# Releasing Nano

**Nano has never been released.** There is no tag in this repository, no
installer, and nothing to download. This document describes the process that
*will* be used, so that the first release is deliberate rather than improvised.

Nothing here should be executed until the packaging pass is complete and
[`PUBLIC_RELEASE_CHECKLIST.md`](PUBLIC_RELEASE_CHECKLIST.md) is satisfied.

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
{ "product": "1.0.0", "display": "v1.0", "name": "Nano Assistant", "channel": "stable" }
```

`core/version.py`, `frontend/lib/version.ts` and the Electron shell all read it.
Before this existed there were four version strings in three languages and they
disagreed — the UI said `v1.0` while the backend reported `8.1.0`.

**Still to reconcile:** `electron/package.json` and `frontend/package.json` both
carry a legacy `8.1.0`. `electron-builder` reads the Electron one to stamp the
installer, so it must be aligned as part of the packaging pass. Leaving it out
of scope until then was deliberate — changing it changes the installer's
identity, which is a packaging decision.

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
        ├─ electron-builder → Nano-Setup-x64.exe, .msi
        ├─ SHA256SUMS.txt
        │
        ▼
  GitHub Release (draft) → review → publish
```

### The test gate

A release requires everything green, on a Windows machine, from a clean
checkout. CI does not cover all of it: the render and behaviour harnesses need
real Chromium and a display, and the real-application checks need a human.

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
| `Nano-Setup-x64.exe` | NSIS installer (primary) |
| `Nano-Setup-x64.msi` | MSI, for managed environments |
| `SHA256SUMS.txt` | Checksums for both |

Checksums are generated in CI and attached to the release, so a download can be
verified independently of the transport:

```powershell
Get-FileHash Nano-Setup-x64.exe -Algorithm SHA256
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

**Not implemented.** Until it is, Windows SmartScreen will warn on every
download, and every user is right to be suspicious of an unsigned executable
that asks to control their computer.

When it is set up:

* Use an OV or EV certificate; EV avoids the SmartScreen reputation delay.
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

* No packaging pass has been completed; `build-windows.yml` is a starting point,
  not a working pipeline.
* No code signing.
* No update mechanism — a user with `0.1.0` will not learn that `0.2.0` exists.
* No installed-application testing: everything so far has been validated from a
  development checkout, not from an installed build.
