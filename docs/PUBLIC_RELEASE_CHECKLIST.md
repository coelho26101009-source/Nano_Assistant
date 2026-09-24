# Public Beta release checklist

Reviewed **2026-09-18**, against the local `0.1.0-beta.1` candidate. This is a
local engineering result, not a clean-machine result. `0.1.0-beta.1` was then
published the same day as a GitHub pre-release (NSIS only); the unchecked items
below were still open at publication and carry forward to the next release.
See [BETA_READINESS_REPORT.md](BETA_READINESS_REPORT.md) for evidence and limits,
[BETA_GUIDE.md](BETA_GUIDE.md) for user instructions, and
[RELEASING.md](RELEASING.md) for the build/publish procedure.

## Candidate implemented and checked

- [x] Canonical Beta identity in Python, frontend, Electron and both lockfiles.
- [x] Real Windows x64 NSIS and MSI builds with embedded Python 3.12.10.
- [x] App.asar and explicit resource allowlist; package input and output checks.
- [x] No developer profiles, .env, databases, logs, tests or scratch directories
      found in the inspected package; dependency CA certificates remain.
- [x] Electron 44.2.0 and electron-builder 26.16.1; local Electron npm audit zero.
- [x] Minimal optional first-run guide using the existing visual design.
- [x] Provider setup, optional voice, permissions and privacy explained.
- [x] First-run acknowledgement persists across processes and backend ports.
- [x] Local allowlisted diagnostics; no automatic upload.
- [x] Bounded startup/render retries and actionable failure messages.
- [x] Logs in the durable data folder, with credential redaction and rotation.
- [x] Validated settings; atomic settings, policies and credential writes.
- [x] Windows DPAPI failure cannot silently save plaintext credentials.
- [x] Explicit test profiles do not import the developer's legacy profile.
- [x] Real installed NSIS launch, settings, conversation initialization and restart.
- [x] Actual NSIS uninstall removes binaries and preserves synthetic profile bytes.
- [x] Shutdown checked for owned process survivors; shared Ollama is not owned.
- [x] Python, frontend, Electron, real Chromium and local static security gates.
- [x] Sandbox, context isolation, deny-by-default permissions and narrow IPC.
- [x] MODEL → REQUEST → POLICY → PERMISSION → ToolExecutor → NARROW TOOL preserved.
- [x] Six protected check names unchanged; remote ruleset requires all six.
- [x] Build workflow manual-only; publishing separately opted in and SHA pinned.
- [x] Apache-2.0 metadata and license/third-party notices included in resources.
- [x] Beta/unsigned/manual-update/data-preservation limitations documented.

## Known technical debt and evidence boundaries

- [ ] Clean Windows x64 account without development tools: install, launch, chat,
      restart, uninstall and reinstall using the final installer and checksum.
- [ ] Real no-Ollama/no-key/offline scenarios on that clean machine. Existing
      error branches have automated coverage; this host already has Ollama.
- [ ] Live cloud credentials and each provider's current account/model access.
- [ ] Human microphone, wake phrase, transcription and spoken-reply validation.
      Base installer does not bundle optional faster-whisper or model weights.
- [ ] MSI managed deployment, upgrade and cross-target NSIS/MSI migration matrix.
      Numeric prerelease metadata is not a validated upgrade policy.
- [ ] Signing certificate, publisher verification and actual SmartScreen reputation.
      Local artifacts are unsigned; no certificate or signing claim is fabricated.
- [ ] Current patch CI/CodeQL and dependency rescan after an authorized push.
      Remote CodeQL was zero; remote Dependabot was 35, not zero.
- [ ] Next.js major migration: 23 advisories in the local audit are triaged against
      static export. Reassess before adding a Next server or affected client API.
- [ ] Independent security/privacy review and third-party license distribution review.
      Included notices are not a claim of legal clearance for every optional model.

Execution ledger and permission audit history are in-memory. Durable policies,
conversations, Memory, tasks and settings persist; interrupted actions are not
replayed on restart. There is no automatic updater, telemetry/crash-upload service,
or one-click cross-store data wipe. These are not silently promised by this Beta.

## Publication remains a separate owner action

- [ ] Accept clean-machine results and the documented Beta limitations.
- [ ] Decide whether the public Beta distribution policy permits unsigned builds.
- [ ] Review and authorize the changes, then run remote protected CI and CodeQL.
- [ ] Prepare release notes, known issues, checksums and a staffed support channel.
- [ ] Authorize a tag and explicit manual publication. (`v0.1.0-beta.1` was
      tagged and published on 2026-09-18.)

A normal push to main must not publish. This engineering pass stages, commits,
pushes, tags and publishes nothing. Code signing, hardware validation, review and
publication are not made complete by a green local test suite.
