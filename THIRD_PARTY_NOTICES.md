# Third-Party Notices

**Status: draft, pending legal review.** Every licence below was read from the
metadata of the package actually installed in this project's development
environment, at the version shown. Nothing here was inferred from a package's
reputation or from memory. Items that could not be verified are marked
**REVIEW** rather than guessed.

This file is not a substitute for professional legal advice, and it is not yet
complete: transitive dependencies are not enumerated (see *Known gaps*).

Nano itself is licensed under **Apache License 2.0** — see [`LICENSE`](LICENSE).
Apache-2.0 is permissive and does not itself create the obligations below;
they come from the dependencies Nano uses, not from Nano's own licence.
Some of the obligations below only bite on *distribution*, which has not
happened yet. They will bite the moment an installer is published.

---

## ⚠️ Copyleft — decide before distributing

These two are the reason this file needs a lawyer's eye rather than a
maintainer's. Both are **weak copyleft** (LGPL), which generally permits use in
a differently-licensed application provided the LGPL component stays replaceable
and its own source and licence are made available. That general statement is not
legal advice, and how it applies depends on how Nano ends up packaged.

| Package | Version | Licence | Why it matters |
| --- | --- | --- | --- |
| `edge-tts` | 7.2.8 | **LGPL-3.0** | Nano's text-to-speech. LGPLv3 adds an anti-tivoisation clause and a relinking requirement. If it is frozen into a single-file binary by the future installer, the "user can replace this component" condition needs deliberate handling. |
| `pygame` | 2.6.1 | **LGPL-2.1** | Audio playback. Same shape of obligation, older and slightly laxer text. |

**Options if the copyleft obligation is unwanted:** keep both as ordinary
installed Python packages rather than frozen into a binary (which preserves
replaceability), or replace them — TTS could move to a permissively licensed
engine, and `pygame` is used only for audio playback.

**REVIEW:** confirm with counsel how LGPL relinking applies to a PyInstaller- or
similar-frozen Windows build before the first public installer.

---

## Python — runtime (`requirements.txt`)

| Package | Version | Licence |
| --- | --- | --- |
| `eel` | 0.18.2 | MIT |
| `groq` | 0.37.1 | Apache-2.0 |
| `httpx` | 0.28.1 | BSD-3-Clause |
| `python-dotenv` | 1.2.2 | BSD-3-Clause |
| `PyYAML` | 6.0.3 | MIT |
| `psutil` | 6.1.1 | BSD-3-Clause |
| `edge-tts` | 7.2.8 | **LGPL-3.0** — see above |
| `pygame` | 2.6.1 | **LGPL-2.1** — see above |
| `PyAudio` | 0.2.14 | MIT |

### Pulled in by `eel`

| Package | Version | Licence |
| --- | --- | --- |
| `bottle` | 0.13.4 | MIT |
| `gevent` | 26.7.0 | MIT |
| `pyparsing` | 3.3.2 | MIT (declared as `License-Expression`) |
| `gevent-websocket` | 0.10.1 | **REVIEW** |

**REVIEW — `gevent-websocket`.** Its `License` metadata field contains a
copyright line (`Copyright 2011-2017 Jeffrey Gelens <jeffrey@noppo.pro>`) rather
than a licence name, and it declares no licence classifier. The actual licence
must be confirmed from the project's own `LICENSE` file before distribution. It
is not optional: it is how eel serves its WebSocket, so it is on the critical
path of the local control plane.

## Python — optional (`requirements-optional.txt`)

Not installed by default. Licences shown for those present in this development
environment; the rest are marked accordingly and must be verified if they ever
become part of a shipped build.

| Package | Version | Licence |
| --- | --- | --- |
| `faster-whisper` | 1.2.1 | MIT |
| `ctranslate2` | 4.8.1 | MIT |
| `openwakeword` | 0.6.0 | Apache-2.0 |
| `onnxruntime` | 1.28.0 | MIT |
| `chromadb` | — | **NOT INSTALLED — verify before shipping** |
| `SpeechRecognition` | — | **NOT INSTALLED — verify before shipping** |
| `beautifulsoup4` | — | **NOT INSTALLED — verify before shipping** |
| `Pillow` | — | **NOT INSTALLED — verify before shipping** |
| `pypdf` | — | **NOT INSTALLED — verify before shipping** |
| `playwright` | — | **NOT INSTALLED — verify before shipping** |

**REVIEW — speech model weights.** `faster-whisper` the *library* is MIT, but
the Whisper **model weights** it downloads are a separate artifact under their
own terms, and `openwakeword` ships bundled models likewise. Library licence and
model licence are different things. If a build ever bundles weights rather than
fetching them, their terms must be reviewed and reproduced here.

**REVIEW — `onnxruntime`** ships prebuilt native binaries. Binary distributions
sometimes carry notices beyond the source licence; check its `ThirdPartyNotices`
before redistribution.

## Python — development only (`requirements-test.txt`)

Not distributed with Nano.

| Package | Version | Licence |
| --- | --- | --- |
| `pytest` | 9.1.1 | MIT (declared as `License-Expression`) |
| `pytest-asyncio` | 1.4.0 | Apache-2.0 (declared as `License-Expression`) |

## JavaScript — desktop shell (`electron/package.json`)

| Package | Version | Licence |
| --- | --- | --- |
| `electron` | 30.5.1 | MIT |
| `electron-builder` | 24.13.3 | MIT |

**REVIEW — Electron redistribution.** Electron itself is MIT, but a packaged
Electron application redistributes **Chromium** and **Node.js**, which carry
their own licences and a substantial third-party notices file (BSD-style terms
plus many bundled components). `electron-builder` can emit these. Any shipped
installer must include Chromium's and Node's notices; this file does not yet
reproduce them, because nothing is packaged yet.

## JavaScript — frontend (`frontend/package.json`)

| Package | Version | Licence |
| --- | --- | --- |
| `next` | 14.2.3 | MIT |
| `react` | 18.3.1 | MIT |
| `react-dom` | 18.3.1 | MIT |
| `typescript` | 5.9.3 | Apache-2.0 (dev only) |
| `@types/*` | various | MIT (DefinitelyTyped, dev only) |

Next.js output is statically exported and served locally; no Vercel service is
contacted at runtime.

## Assets

| Asset | Source | Status |
| --- | --- | --- |
| Nano flame mark and wordmark (`frontend/public/branding/*.png`) | Supplied by the project owner | Project-owned. **REVIEW:** confirm provenance and that no third-party stock or AI-generated asset with its own terms is embedded. |
| Application and tray icons (`electron/assets/*`) | Derived from the branding mark by `scripts/build_app_icon.ps1` | Derivative of the above; same status. |
| `docs/design/nano-ui-reference.png` | Internal design reference | Project-owned. |

**Fonts: none are bundled.** The interface asks for `Inter` and `JetBrains Mono`
and falls back to system faces (`Segoe UI`, `Consolas`, and the generic
families). No font file is redistributed, so no font licence applies. If a font
is ever bundled, its licence must be added here — both of those are SIL OFL,
which has its own requirements.

**Audio: none are bundled.** Notification sounds are generated at runtime; no
audio file ships with Nano.

## Known gaps

Stated plainly so the omissions are not mistaken for a clean bill of health.

1. **Transitive dependencies are not enumerated.** Only direct dependencies and
   eel's immediate chain were audited. A full tree (`pip-licenses`,
   `license-checker`) is needed before distribution.
2. **`gevent-websocket`'s licence is unconfirmed.**
3. **Chromium and Node notices are absent**, because nothing is packaged yet.
4. **Model weights are unaudited** — a separate question from library licences.
5. **Compatibility of Apache-2.0 with the LGPL dependencies above has not had a
   legal review** — Apache-2.0 is generally considered compatible with LGPL
   obligations at the application level, but that general statement is not a
   substitute for counsel reviewing the actual packaging shape.

## How this file was produced

Python licences were read with `importlib.metadata` from the installed
distributions, checking the `License` field, then `License-Expression`, then the
`License ::` trove classifiers — in that order, because modern packaging has
moved the answer between all three. JavaScript licences were read from each
package's own `package.json`. Nothing was filled in from memory; anything that
could not be read is marked **REVIEW**.

To regenerate the Python side:

```bash
python -c "import importlib.metadata as md; [print(d.metadata['Name'], d.version, d.metadata.get('License-Expression') or d.metadata.get('License')) for d in md.distributions()]"
```
