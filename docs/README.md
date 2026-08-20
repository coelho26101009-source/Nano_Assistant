# Nano — Documentation

## Starting Nano

Double-click **`NANO.bat`** in the project root. That is the only supported way
to start Nano. It validates the Python runtime, builds the frontend on first
run, starts the Ollama server if it is not already running, starts the backend
and the wake listener, and opens the UI exactly once.

If startup fails, the window stays open and prints the reason.

## Contents

| Document | What it covers |
|---|---|
| [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md) | System layers and the intended agent flow |
| [architecture/MODEL_ROUTING.md](architecture/MODEL_ROUTING.md) | Model router, providers, local-first policy |
| [SECURITY_POLICY.md](SECURITY_POLICY.md) | Capabilities, autonomy levels, approval gates |
| [VOICE.md](VOICE.md) | Voice runtime: STT, TTS, wake detection |
| [AJUDA.txt](AJUDA.txt) | Original Portuguese help notes (historical) |

## Legacy

`legacy/wakeword/` holds the custom ONNX wake-word training material (Colab
notebook instructions and the training guide). Nano no longer needs a trained
model: the shipped **"Hey Nano"** detector spots the phrase in local
speech-to-text transcripts and requires no training. The documents are kept
because the ONNX path still exists in `core/wake_word.py` as an optional second
wake engine, and the material would be needed to revive it.

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
