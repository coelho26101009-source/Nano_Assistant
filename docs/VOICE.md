# Nano Voice

## Architecture

The voice system is a first-class interface into the same Nano core, not a parallel brain. The flow is:

- Audio input
- Wake word detection
- STT transcription
- Nano request normalization
- Policy + permission evaluation
- Same task engine / orchestrator / tool execution
- Optional TTS response

This preserves the same memory, tools, policy engine, and model router used by text input.

## Providers

The voice layer is built around independent providers:

- WakeWordProvider: listens for activation phrases such as "Nano"
- SpeechToTextProvider: turns microphone audio into text
- TextToSpeechProvider: speaks responses back to the user
- AudioInputProvider: captures microphone audio
- AudioOutputProvider: plays generated audio
- VoiceSession: tracks lifecycle state and timeout rules

## Voice Runtime

The runtime is not a second brain. It connects the speech stack to the existing Nano Core:

- microphone capture
- wake-word or manual trigger
- STT transcription
- normalized Nano request
- Brain / Orchestrator processing
- policy and permission enforcement
- task creation for long work
- TTS response when available

Conceptually:

```text
VoiceRuntime
├── start()
├── stop()
├── listen()
├── process_audio()
├── process_request()
├── speak()
└── status()
```

Quick commands are processed via the Brain directly. Longer or riskier actions create real tasks through the Task Engine and existing permission flow.

## Local-first behavior

Default behavior is:

- local microphone capture first
- local STT when available
- local TTS when available
- cloud audio disabled by default
- cloud fallback only when explicitly configured

This keeps privacy and latency in line with the design of the rest of Nano.

## Wake word

The default activation phrase is "Nano".

The current local-first implementation prefers `openWakeWord` on Windows because it is fully local and offline, and it can run without cloud audio. The runtime checks for an actual live keyword model before claiming readiness. If the package is installed but the keyword model is not configured, the system reports:

### Installed version and training reality

The environment currently has `openWakeWord` installed and the runtime is compatible with the custom model API. We validated the actual package version in this environment:

```text
openWakeWord 0.6.0
```

This version includes the runtime model class (`Model`) and a training module (`train.py`). However, the training stack is not fully installed in the main Nano runtime environment because the PyTorch training dependencies (for example `torch`, `torchinfo`, `torchmetrics`) are not present. That means the correct split is:

- Runtime: Windows 11 / Ryzen 7 5700X / GTX 1660 Ti / 16 GB RAM
- Training: Linux / WSL2 / Colab or another GPU-capable environment
- Deployment: export the model, copy the final `.onnx` to the Windows machine, and run Nano locally

This split avoids heavy dependency installation in the normal runtime and keeps the deployment path simple and reproducible.

### Custom training preparation (not yet executed)

The repository now includes a baseline training configuration for the first custom model:

```yaml
model_name: "nano"
target_phrase:
  - "Nano"
provider: "openwakeword"
model_type: "dnn"
framework: "onnx"
steps: 5000
n_samples: 20000
n_samples_val: 4000
```

The current configuration is intentionally conservative and does not start heavy data generation or training. Its purpose is to define the exact first-pass workflow for:

1. positive sample generation
2. negative sample generation
3. training in a dedicated environment
4. export to `.onnx`
5. validation with `openWakeWord`
6. deployment to the Windows runtime

The repository structure for this stage is:

```text
tools/
  wakeword/
    config/
    training/
    output/
models/
  wakeword/
```

The expected final output is:

```text
models/wakeword/nano.onnx
```

The actual model should be validated before claiming wake-word readiness. Until then, the system must remain in a setup-required state.

```text
Wake word: ARCHITECTURE READY
LIVE PROVIDER NOT CONFIGURED
```

This is intentional and prevents false-positive readiness claims.

Important safeguards:

- configurable threshold
- cooldown between activations
- session timeout
- no permanent listening loop when not activated
- no cloud audio by default
- no custom keyword model download unless explicitly configured

## STT

A real STT provider is exposed through `LocalSTTProvider`.

It intentionally fails gracefully when the local runtime does not have a speech model installed. The rest of Nano continues to work in text mode.

## TTS

`LocalTTSProvider` uses the local runtime when the dependency is present. It is careful to fail closed and never block the rest of the system when speech is unavailable.

## Privacy

Voice has the same privacy posture as the rest of Nano:

- cloud audio disabled by default
- local-first route preferred
- permission checks still enforced for dangerous instructions
- audit metadata only; raw audio is not stored by default

## Hardware notes

This phase is meant to be safe for the target machine profile:

- Ryzen 7 5700X
- GTX 1660 Ti
- 16 GB RAM

The design avoids assuming high-end GPU or unlimited memory. It is conservative by default and relies on local-only behavior when available.

## Configuration

The central config holds the voice section, with values such as:

```yaml
voice:
  enabled: false
  local_first: true
  cloud_audio: false
  listen_seconds: 5
  wake_word:
    enabled: false
    phrase: "Nano"
  microphone:
    sample_rate: 16000
    channels: 1
```

## Troubleshooting

- Microphone not detected: check PyAudio / device permissions
- STT unavailable: install local speech models or leave the system in text-only mode
- TTS unavailable: speech output remains disabled without crashing the app
- Wake word unavailable: the system stays in text mode and continues to operate normally

## Manual testing

Use this flow when the local environment is configured:

1. Ensure PyAudio and a microphone are available.
2. Enable voice in the config.
3. Keep cloud audio disabled unless explicitly configured.
4. Start the app.
5. Trigger a short session manually through the runtime or wake-word flow.
6. Say a short command such as: "Nano, que horas são?"
7. Verify the request reaches the same Brain pipeline used by text input.
8. For long tasks, say something like: "Nano, analisa este projeto inteiro."

The runtime will route quick commands directly and longer work through the Task Engine.

## Optional dependencies

Required for a full local setup on a Windows/Linux PC:

- PyAudio
- pygame
- edge-tts (optional but recommended for local TTS)
- faster-whisper (optional but recommended for local STT)
- openwakeword or pvporcupine for wake-word detection (optional)

## Windows setup

Use this checklist before attempting live voice on Windows:

1. Install Python 3.11+ and verify the active interpreter.
2. Install PyAudio.
3. Ensure your microphone is enabled in Windows Sound settings.
4. Ensure the output device is available in the same panel.
5. Install a local STT provider such as faster-whisper if you want local recognition.
6. Install a local TTS provider such as edge-tts for speech output.
7. Optional: install wake-word support with openwakeword or pvporcupine.
8. Start Ollama and ensure the local endpoint is reachable.
9. Check that at least one compatible model is available through the Model Router.

## Python dependencies

Core runtime dependencies already included in the project include:

- Python
- PyYAML
- httpx
- psutil
- python-dotenv
- pygame
- PyAudio
- edge-tts

Optional voice setup dependencies:

- faster-whisper
- SpeechRecognition
- openwakeword
- pvporcupine

## PyAudio setup

Install with:

```bash
pip install PyAudio
```

If the package fails to build on Windows, check whether the Python version matches the available build dependencies and try a compatible Python interpreter.

## STT setup

For local recognition install:

```bash
pip install faster-whisper
```

If that dependency is not available, the system will report:

```
STT unavailable
Provider: faster-whisper
Reason: Required package is not installed.
```

## TTS setup

For local speech output install:

```bash
pip install edge-tts
```

If unavailable, TTS stays disabled and the rest of the Nano continues to work in text mode.

## Wake word setup

Wake word is optional and must be configured explicitly. The system reports it as:

```
ARCHITECTURE READY
LIVE PROVIDER NOT CONFIGURED
```

when the interface exists but the real runtime dependency is not installed or enabled.

## Ollama setup

Use a local Ollama service and ensure the endpoint is reachable:

```text
http://127.0.0.1:11434
```

If Ollama is offline, the diagnostics report clearly and the rest of Nano continues to function with the available local or cloud configuration.

## Device selection

The voice diagnostic tool lists connected microphones and speakers by index and name. If there is only one valid device, it can be suggested automatically. If there are several, the user must select explicitly.

## Diagnostics

The recommended first command is:

```bash
python -m core.voice_diagnostics
```

It checks:

- Python availability
- PyAudio installation
- microphone detection
- speaker detection
- STT readiness
- TTS readiness
- wake-word readiness
- Ollama availability
- model router compatibility

## Troubleshooting

Common issues:

- microphone not detected: check Windows Sound input settings
- PyAudio missing: install the package for the active Python interpreter
- STT missing: install faster-whisper or use text mode
- TTS missing: install edge-tts or leave speech output disabled
- wake word not configured: architecture exists, but no live provider is available
- Ollama offline: start the service and verify /api/tags responds

## Live test status

This environment is not guaranteed to contain a microphone, local STT runtime, or TTS runtime. The implementation is ready for those environments, but live hardware validation must be reported honestly.

Microphone live test: NOT AVAILABLE
STT live test: NOT AVAILABLE
TTS live test: NOT AVAILABLE
Hardware live validation: NOT AVAILABLE
