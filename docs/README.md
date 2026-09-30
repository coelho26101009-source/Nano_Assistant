# Nano — Documentation

## Starting Nano

**`NANO_DESKTOP.bat`** is the primary way to start Nano: it validates the Python
runtime and dependencies, installs Electron on first run, rebuilds the frontend
only when needed, and hands control to the Electron shell. The window appears
only once the backend is ready.

**`NANO.bat`** starts the same backend in browser mode — useful for frontend
development, but without the tray, the global hotkey or the desktop voice
overlay.

If startup fails, the window stays open and prints the reason.

## Contents

| Document | What it covers |
|---|---|
| [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md) | System layers, the memory stack, and what is built versus declared |
| [architecture/MODEL_ROUTING.md](architecture/MODEL_ROUTING.md) | The routing authority, AUTO/CLOUD/LOCAL, provider order, model selection, the Execution Ledger |
| [architecture/DESKTOP.md](architecture/DESKTOP.md) | The Electron desktop shell: lifecycle, tray, global hotkey, voice overlay, and the trust model of the Electron to Python control channel |
| [architecture/PC_CONTROL.md](architecture/PC_CONTROL.md) | The narrow Windows tools, their capabilities and their approval gates |
| [architecture/SPEECH_ACCURACY.md](architecture/SPEECH_ACCURACY.md) | The speech-to-text benchmark and the decisions it drove |
| [SECURITY_POLICY.md](SECURITY_POLICY.md) | Capabilities, autonomy levels, approval gates |
| [VOICE.md](VOICE.md) | Voice runtime: local STT, Microsoft-backed TTS, wake detection |
| [design/README.md](design/README.md) | Visual identity and design decisions |
| [PUBLIC_RELEASE_CHECKLIST.md](PUBLIC_RELEASE_CHECKLIST.md) | What is done and what genuinely blocks a public beta |
| [RELEASING.md](RELEASING.md) | Versioning and the release process that does not exist yet |
| [DEPENDENCIES.md](DEPENDENCIES.md) | Python dependency manifests, the hashed locks generated from them, and how to change either |

The provider fallback order is backed by a committed measurement:
[`../benchmarks/provider_routing/README.md`](../benchmarks/provider_routing/README.md).

## Historical material

| Document | Status |
|---|---|
| [AJUDA.txt](AJUDA.txt) | **Historical.** The HELIOS V7 Portuguese help guide, kept as a record. It describes `start.bat`, a `HELIOS_V7` directory, a Google speech-to-text provider and configuration keys that no longer apply. Do not read it as current documentation. |
| `legacy/wakeword/` | **Historical.** Custom ONNX wake-word training material — see below. |

### Wake-word training material

`legacy/wakeword/` holds the custom ONNX wake-word training material (Colab
notebook instructions and the training guide). Nano no longer needs a trained
model: the shipped **"Hey Nano"** detector spots the phrase in local
speech-to-text transcripts and requires no training. The documents are kept
because the ONNX path still exists as an optional second wake engine, and the
material would be needed to revive it. Wake-phrase listening is experimental and
off by default.

## Diagnostics

```
python -m core.wake_phrase_debug     # microphone -> STT -> phrase match
```

Prints every transcript it hears and reports whether the microphone, the
speech-to-text, or the phrase matching is the thing failing.

## Legacy names still in the code

These are kept on purpose; they are not branding:

| Identifier | Why it stays |
|---|---|
| `HELIOS_MODE`, `HELIOS_APP_ROOT`, `HELIOS_DATA_DIR` | Read as a fallback after `NANO_*` so older installs and packaged builds keep working |
| `helios.*` logger namespaces | Still used by several modules; `setup_logger()` configures both so nothing loses its log output |
| `helios_docs` Chroma collection | Persisted data. Renaming it would orphan every already-indexed document |
| `helios.db` | Existing memory database filename |
