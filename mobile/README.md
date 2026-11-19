# NANO for Android

A remote for the NANO on your PC. The phone has no brain of its own: it sends
your message to the NANO running on your computer, which answers with the same
memory, conversations, agenda and tools, and streams the answer back.

| | |
|---|---|
| Package | `app.nano.assistant` |
| Stack | Capacitor 8 + React 18 + Vite (TypeScript) |
| Min Android | 7.0 (API 24), target API 36 |
| Languages | English (default), European Portuguese, following the phone |
| Talks to | `core/mobile_link.py` + `core/mobile_server.py` on the PC |

## How the link works

1. On the PC: Settings → General → Phone → turn on **Phone access** → **Connect a phone**.
   The PC listens on port 47821 on the local network and shows a one-time QR code
   (`nano://pair?...`: PC addresses, port, session id, 256-bit secret, 5 minutes).
2. The app scans it (Google code scanner: no camera permission), proves it has the
   secret and waits. The PC asks "<phone> wants to connect". Only after **Allow on
   the PC** does the phone get its own 256-bit key.
3. Every request and answer is AES-256-GCM sealed (`src/lib/crypto.ts`), bound to
   the device, the request id and the direction, with replay protection. HTTP goes
   through Android's native stack (CapacitorHttp).
4. Answers arrive through a long-polled event feed (`events.poll`), the same events
   the PC window receives (stream chunks, approval requests, reminders, notices).

Away from home: install Tailscale on the PC and the phone. The QR already lists the
PC's Tailscale address (100.x); nothing is exposed to the internet.

## Develop

```bash
cd mobile
npm ci            # Node 22+ (CI uses 24)
npm test          # crypto vector shared with tests/test_mobile_link.py, i18n coverage
npm run dev       # browser preview at http://127.0.0.1:5173 (paste the pairing code)
npm run build && npx cap sync android
cd android && ./gradlew assemblePreview   # needs Android SDK 36 + JDK 21
```

Strings: every user-facing string is the Portuguese original in `t()`, with English in
`src/lib/locales/en.ts` (same rule as the PC app). `npm test` fails on a missing entry.

Icons and store graphics: `python3 scripts/make_icons.py` (from the supplied artwork).

## Builds and signing

| Build | Command | Signed with | Use |
|---|---|---|---|
| `preview` APK | `./gradlew assemblePreview` | `android/app/nano-test.keystore` (committed, throwaway) | install by hand to test |
| `release` AAB | `./gradlew bundleRelease` | your private upload key | Google Play |

CI (`.github/workflows/android.yml`) builds both on every push to `mobile/`. On
`main` it publishes the test APK as the rolling **android-test** pre-release.
Official Betas attach the APK from the release commit to the version's own
release (for example `v0.3.0-beta.1`).

### Your Play Store upload key (once, on your own computer)

```bash
keytool -genkeypair -v -keystore nano-upload.jks -alias nano -keyalg RSA -keysize 4096 -validity 10000
```

Keep `nano-upload.jks` and its passwords somewhere safe and **never commit them**.
Then either create `android/app/keystore.properties` (git-ignored):

```
storeFile=/full/path/to/nano-upload.jks
storePassword=...
keyAlias=nano
keyPassword=...
```

or add four repository secrets so CI signs the bundle: `NANO_UPLOAD_KEYSTORE_B64`
(`base64 -w0 nano-upload.jks`), `NANO_UPLOAD_STORE_PASSWORD`, `NANO_UPLOAD_KEY_ALIAS`,
`NANO_UPLOAD_KEY_PASSWORD`. Use Play App Signing (Google keeps the app key; this is
only the upload key).

## Play Store checklist

- [x] Package name `app.nano.assistant`, app name NANO, adaptive icon
- [x] Store icon `store/icon-512.png`, feature graphic `store/feature-graphic.png` (1024×500)
- [x] Privacy policy: [`PRIVACY.md`](PRIVACY.md) (also inside the app, Settings → Privacy)
- [x] Release bundle (`.aab`), signable with your upload key
- [x] No ads, no analytics, no account; backup disabled
- [ ] Google Play developer account (one-time fee, owner's decision)
- [ ] Phone screenshots (at least 2) and the short/long description
- [ ] Data safety form: data is sent only to the user's own PC, encrypted in transit,
      nothing collected by the developer. Speech uses Android's recogniser.
- [ ] Exact alarms declaration: reminders app (SCHEDULE_EXACT_ALARM)
- [ ] Closed testing with testers before production (Play's rule for new personal accounts)
