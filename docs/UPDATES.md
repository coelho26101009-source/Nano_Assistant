# Automatic updates

NANO updates itself on the PC (the installed Windows app) and on the phone (the
test APK from GitHub). Both read a small **signed update feed** from this
repository's GitHub Releases and never install anything the feed does not
describe byte for byte.

| | PC (installed app) | Phone (test APK) | Phone (Play Store) |
| --- | --- | --- | --- |
| Feed | `desktop-update` release → `nano-update.json` | `android-test` release → `nano-update.json` | none: Google Play updates it |
| Written by | `build-windows.yml`, publish job (manual release) | `android.yml`, publish job (every phone build on `main`) | — |
| Checks | 90 s after start, then every 6 h | when the app opens, at most every 12 h | — |
| Installs | when NANO closes, or now with **Restart and update** | after **Update** and Android's own confirmation | — |
| Switch | Settings → About → Updates | Settings → About | — |
| Code | `electron/lib/updater.js`, `electron/lib/update-feed.js` | `mobile/src/lib/updates.ts`, `NanoUpdaterPlugin.java` | — |

Running NANO from the project folder (`NANO_DESKTOP.bat`), the app only says a
new version exists: there is no installer there to replace.

## The feed

```json
{ "format": 1, "keyId": "d9c6b38f2b29f486",
  "payload": "<base64 of the JSON below>",
  "signature": "<base64 ECDSA P-256 / SHA-256 over the payload bytes, IEEE P1363>" }
```

```json
{ "product": "nano-desktop", "channel": "beta", "version": "0.4.0-beta.1",
  "url": "https://github.com/coelho26101009-source/Nano_Assistant/releases/download/v0.4.0-beta.1/Nano-Setup-0.4.0-beta.1-x64.exe",
  "sha256": "…", "size": 158000000, "published": "…", "notes": "…" }
```

The phone's payload has `"product": "nano-android"`, a `versionCode` (the
Android build number, which is what is compared) and the commit.

ECDSA P-256 because every verifier NANO has can check it with no new
dependency: Node's `crypto` on the PC and in CI, WebCrypto in the Android
WebView. The signature covers the payload **bytes**, so there is no JSON
canonicalisation question.

## What the apps check, in order

1. The signature, with a key listed in `electron/lib/update-keys.json` (the
   phone's copy is `mobile/src/lib/updateKeys.ts`; a test keeps them equal).
2. The product: a phone feed can never update the PC and the other way round.
3. The download URL: HTTPS, `github.com`, this repository's
   `/releases/download/` under its current name (`coelho97sc`) or its former
   one (`coelho26101009-source`, see RELEASING.md), no port, user, query or `..`. Where the download
   lands after GitHub's redirect must be GitHub's own asset hosts.
4. Newer than what is installed. An older signed feed is valid but ignored,
   which is what stops a replayed old feed from forcing a downgrade.
5. While downloading: never more bytes than `size`, and exactly `size` at the
   end. Then the SHA-256. A file that fails is deleted.
6. PC, just before running the installer: the SHA-256 again (the file sat on
   disk). Phone: the APK is this app, newer, and signed with the same
   certificate; Android then checks the certificate again itself.

## Failures

| What happens | PC | Phone |
| --- | --- | --- |
| No internet, GitHub down, no feed yet | Tries again at the next check. A missing feed reads as "up to date". | Tries again next time the app opens. |
| Bad signature, wrong host, wrong hash | Nothing is installed; the file is deleted; Settings says the update failed the security check. | Same, in a toast. |
| Not enough disk space | Stops before downloading and says so. | Android reports it. |
| Windows shutting down while NANO closes | The installer is **not** started (a half-written install is the one way an update could break NANO). It installs at the next normal exit. | — |
| The installer fails | Next start compares the running version with the one it tried. If it did not change: a notification and a message in Settings; the downloaded installer is kept and tried again at the next exit; after **2** failures NANO stops trying on its own and offers the releases page. Data is in `%LOCALAPPDATA%\NanoAssistant`, which the installer never touches. | Android's installer shows its own error; the old app stays. |
| Last resort | Run the installer kept in `%LOCALAPPDATA%\NanoAssistant\updates`, or download it from the Releases page. Settings, keys and conversations are kept. | Install the APK from the `android-test` release by hand. |

## The signing key

* **Public half**: in the repository (`update-keys.json`, `updateKeys.ts`).
* **Private half**: only in the GitHub Actions secret
  **`NANO_UPDATE_SIGNING_KEY`** (the PKCS#8 PEM, or that PEM in base64). It is
  read only by the two publish jobs, which run only on `main` (phone) or on a
  manual release dispatch (PC), never on a pull request.
* **Without the secret** the workflows still publish the APK and the release,
  just without a feed, and log a warning. Apps then find no update. Nothing
  fails.
* `scripts/update_feed.mjs sign` checks what it signed against the
  **repository's public keys** before writing, so a secret that does not match
  the shipped key fails the job instead of publishing a feed every app rejects.

### Setting it up (once)

1. GitHub → the repository → **Settings → Secrets and variables → Actions →
   New repository secret**.
2. Name: `NANO_UPDATE_SIGNING_KEY`. Value: the whole content of the key file
   you were given, from `-----BEGIN PRIVATE KEY-----` to
   `-----END PRIVATE KEY-----`. Save.
3. Delete the key file from wherever it was delivered. Keep one offline copy
   (a USB stick or a password manager) only if you want to be able to rotate
   without shipping a new key first.

### Rotating or a lost key

Make a new P-256 key, add its public half to both key lists, ship that in a
release and a phone build, then replace the secret. Apps that have the new list
accept the new key; until then they accept the old one. If the private key
leaks, remove its public half from both lists in the same release.

### What signing does and does not protect

It protects against a changed or replaced file on GitHub (for example a token
with release-write access, which every workflow that publishes holds, writing
a different installer), and against anything between GitHub and the PC. It
does not replace **Authenticode** signing of the installer (Windows'
"unknown publisher" warning; see [CODE_SIGNING.md](CODE_SIGNING.md)), and it
cannot help if the build itself were compromised, because CI signs what CI
built.

## Testing

* `cd electron && npm test` — `test/updater.test.js`: the feed (signature,
  tampering, wrong key, wrong product, URLs, versions) and the whole updater
  flow with a fake network and installer: download, wrong hash, oversize,
  short download, redirect off GitHub, no disk space, project-folder mode,
  install arguments, a file swapped before install, success and failure on the
  next start, retry limit.
* `cd mobile && npm test` — `test/updates.test.ts`: the phone's feed check,
  including a feed made by the PC's signing code.
* `node scripts/update_feed.mjs verify <feed> --file <installer>` checks a
  published feed by hand.
* Not tested in the cloud: a real NSIS install over a running NANO on Windows,
  and Android's installer on a phone (the Java side is compiled by CI only).
