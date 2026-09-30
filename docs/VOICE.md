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

The runtime holds no intelligence of its own. It connects the speech stack to
the existing Nano Core (and is unrelated to the Second Brain, which is the
knowledge graph in `core/knowledge_graph.py`):

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

## Where each half of voice actually runs

Voice is not uniformly local, and the two halves differ. Stating it plainly
matters more than the slogan:

| Stage | Where it runs |
|---|---|
| Microphone capture | Local |
| Wake-phrase detection | Local |
| **Speech-to-text** | **Local.** `faster-whisper`, on this machine. There is no cloud STT path in the code: the runtime constructs `LocalSTTProvider` unconditionally. |
| **Text-to-speech** | **Not local.** `edge-tts` sends the text to be spoken to **Microsoft's Edge voice service** and plays back the audio it returns. |

**This applies in every mode, including LOCAL.** "Local mode" refers to the
language model. If spoken replies are on, the sentence Nano is about to read is
sent to Microsoft even when the model itself never touches the network. Turn
spoken replies off in Definições → Voz if you need silence on the wire. See
[`../PRIVACY.md`](../PRIVACY.md).

Audio itself is never uploaded: a recording is written to a unique temporary
file and deleted immediately after transcription, and the synthesised audio file
is deleted after playback.

> **`cloud_audio` is a legacy configuration flag.** It defaults to `false`, is
> reported in the voice status payload, and gates nothing — there is no cloud
> audio implementation for it to enable. Earlier versions of this document
> described a "cloud fallback only when explicitly configured", which implied a
> capability that does not exist.

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
- no custom keyword model download unless explicitly configured
- audio is never uploaded; transcription is local

## STT

A real STT provider is exposed through `LocalSTTProvider`.

It intentionally fails gracefully when the local runtime does not have a speech model installed. The rest of Nano continues to work in text mode.

## TTS

`LocalTTSProvider` fails closed and never blocks the rest of the system when
speech is unavailable.

**The class name is misleading and is kept for compatibility.** It is "local" in
the sense that it runs in this process without a configured cloud account; the
synthesis itself is performed by `edge-tts` against Microsoft's Edge voice
service, so the text leaves the machine. This is the single most misunderstood
fact about Nano's privacy posture, which is why it is repeated here and in
[`../PRIVACY.md`](../PRIVACY.md).

## Privacy

- **Recordings never leave the machine.** Audio is written to a unique temporary
  file and deleted immediately after transcription.
- **Transcription is local.** `faster-whisper`, with no cloud STT path in the
  code.
- **Spoken replies do leave the machine**, to Microsoft, in every mode. This is
  the one exception and it is deliberate to state it loudly.
- Permission checks are still enforced for anything a spoken instruction asks
  for: a voice turn goes through the same Brain and the same
  schema -> policy -> permission -> executor pipeline as typed input.
- The audit trail holds metadata only; raw audio is never stored.

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
3. Decide whether spoken replies should be on — they send text to Microsoft.
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
- edge-tts (required for spoken replies; synthesis happens at Microsoft)
- faster-whisper (required for local speech-to-text)
- openwakeword or pvporcupine for wake-word detection (optional)

## Windows setup

Use this checklist before attempting live voice on Windows:

1. Install Python 3.11+ and verify the active interpreter.
2. Install PyAudio.
3. Ensure your microphone is enabled in Windows Sound settings.
4. Ensure the output device is available in the same panel.
5. Install a local STT provider such as faster-whisper if you want local recognition.
6. Install edge-tts if you want spoken replies (note: it synthesises at
   Microsoft, not on your machine).
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

PyAudio is part of the runtime lock, so the standard install already provides it
(after `requirements/build.lock`; see [DEPENDENCIES.md](DEPENDENCIES.md)):

```bash
python -m pip install --require-hashes --no-build-isolation -r requirements/runtime.lock
```

The lock pins a prebuilt wheel for CPython 3.12 on Windows. On any other interpreter or platform pip has to build PyAudio from source, which needs the PortAudio headers.

## STT setup

For local recognition install faster-whisper, pinned and hash-checked by the optional lock:

```bash
python -m pip install --require-hashes --no-build-isolation -c requirements/optional.lock faster-whisper
```

If that dependency is not available, the system will report:

```
STT unavailable
Provider: faster-whisper
Reason: Required package is not installed.
```

## TTS setup

edge-tts is part of the runtime lock too, installed by the same command as PyAudio above.

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
