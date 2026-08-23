# Nano — Speech Accuracy

Capture was solved first. Transcription was not, and this document is the
record of how it was fixed and what it cost.

Ctrl+Shift+Space records reliably and the adaptive gate no longer rejects a real
voice. What failed was what Whisper made of the audio:

| spoken | `tiny` heard |
|---|---|
| "Olá Nano, tudo bem?" | `Alana no tudo bem` |
| "Olá Nano, tudo bem?" | `tudo bem na no?` |

Those are decoding errors. No gate threshold, no capture window and no
microphone setting could fix them, and changing one to chase them would have
broken capture — which worked — for nothing. So the model and its decoding
configuration were chosen by measurement, on the user's own voice.

## The decision

Thirty recordings of the user reading a fixed Portuguese corpus, replayed
through every candidate over **byte-identical WAVs**:

| configuration | WER | CER | exact | critical entities |
|---|---|---|---|---|
| tiny / cpu / int8 *(was production)* | 67.3% | 33.0% | 13.3% | 4/14 (28.6%) |
| base / cpu / int8 | 61.0% | 28.4% | — | 4/14 (28.6%) |
| small / cpu / int8 | 32.7% | 12.6% | 26.7% | 6/14 (42.9%) |
| base / cpu / int8 + vocab | 57.2% | 27.8% | — | 9/14 (64.3%) |
| **small / cpu / int8 + vocab** *(production)* | **27.0%** | **10.6%** | **30.0%** | **13/14 (92.9%)** |

Auto-language detection was measured and was substantially worse throughout; it
must never be enabled.

`base` was not worth its cost — barely better than `tiny` at roughly double the
latency. `small` more than halved the word error rate, and the short vocabulary
hint then did what no model size could: it took Nano's own vocabulary from 6/14
to 13/14 entities, and improved WER as well, at no measurable latency cost.

The price is real and was accepted deliberately: warm median latency
**0.23 s → 1.25 s**, and resident memory **~290 MB → ~600 MB**.

## What is still NOT solved

PC-control phrasing remains unreliable — **51.7% WER** in that category even
with the winning configuration:

| spoken | heard |
|---|---|
| "Procura o ficheiro relatório" | `Procuro fechar o relatório` |
| "A diferença entre abrir e apagar…" | `A diferença entre ouvir e apagar…` |

This is exactly why no fuzzy correction layer exists and why PC control has not
been started. See section 4.

---

## 1. The current STT path, as it actually is

### Where the model is created

[`core/voice.py:266-279`](../../core/voice.py#L266-L279) —
`LocalSTTProvider.transcribe()` constructs the model **lazily, on the first
transcription**, and caches it on the instance:

```python
if self._model is None:
    self._model = WhisperModel(
        self.config.get("model", "tiny"),
        device=self.config.get("device", "cpu"),
        compute_type=self.config.get("compute_type", "int8"),
    )
```

### The live configuration

Both `config/settings.yaml` and `DEFAULT_CONFIG` now carry the same
`voice.stt` block, and a test asserts they agree key by key.

| Setting | Value | Source |
|---|---|---|
| model | `small` | `voice.stt.model` |
| device | `cpu` | `voice.stt.device` |
| compute_type | `int8` | `voice.stt.compute_type` |
| language | `pt`, forced | `voice.stt.language` (an older `pt-PT` still decodes as `pt`) |
| `initial_prompt` | the vocabulary hint, 104 chars | `voice.stt.vocabulary_hint` |
| `vad_filter` | `True` | hard-coded at the call site |
| beam size | faster-whisper default (5) | never passed |
| temperature / patience / etc. | library defaults | never passed |

Those are exactly the settings the benchmark measured. A test compares the
production hint against the benchmark's own constant, so the two cannot drift
apart and leave the measured numbers describing a configuration nobody runs.

**The dead-configuration trap that was fixed.** `config/settings.yaml` used to
contain `voice.stt_provider`, `voice.whisper_model`, `voice.whisper_device` and
`voice.whisper_compute_type`. **Nothing read them.** The provider reads
`voice.stt.*`, and the YAML had no `voice.stt` block at all, so the effective
values came entirely from `DEFAULT_CONFIG`. Editing the visible `whisper_model`
line changed nothing — and because both happened to say `tiny`, they agreed by
luck and the drift stayed invisible.

That stopped being harmless the moment production became `small`: an old config
carrying `whisper_model: tiny` would read like an explicit instruction while
doing nothing. The keys are gone from the shipped YAML, and
`_strip_legacy_whisper_keys` removes them from any older config **with a
warning naming `voice.stt`** — never silently, and never by honouring them,
which would restore the two-sources-of-truth problem.

Two keys were deliberately left in place: `voice.stop_on_silence` and
`voice.silence_threshold` are also unread, but they are silence-gate-adjacent
and the gate was out of scope for this pass. They now carry a comment saying
so.

`core/user_settings.py` cannot override any STT key either: `ALLOWED_KEYS` has
no STT entry, so nothing in the Settings UI can change the model.

### Audio format reaching the model

[`core/voice.py:420-660`](../../core/voice.py#L420-L660) —
`AudioInputProvider`:

- 16000 Hz, 1 channel, `paInt16` (2 bytes/sample), 1024-frame buffers
- device index from `voice.microphone.device_index` (currently `1`, set through
  the Settings UI and stored in `user_settings.json`)
- wrapped into a WAV container in memory by `_to_wav()`
- **no normalisation, no gain, no resampling, no pre-emphasis** — the bytes the
  model sees are the bytes PortAudio produced

`LocalSTTProvider.transcribe()` then writes those WAV bytes to a temporary file
and hands faster-whisper the *path*, deleting it afterwards.

### The path a voice turn actually takes

```
Ctrl+Shift+Space (Electron global shortcut)
  └─ VoiceRuntime.run_voice_turn(source="hotkey")      core/voice.py:1401
       ├─ audio_feedback.acknowledge_wake()            local chime
       ├─ _take_microphone()                           pauses the wake detector
       └─ process_wake_word_turn()                     core/voice.py:1208
            ├─ AudioInputProvider.capture(window)      7 s, one-shot
            ├─ AdaptiveGate.has_speech(audio)          energy gate  ← unchanged
            ├─ LocalSTTProvider.transcribe(audio)      ← THE PROBLEM IS HERE
            ├─ speech_filter.is_usable_command()       hallucination filter
            └─ process_request() → Brain → …
```

### Cold vs warm

- **Cold:** the first voice turn of a process pays the `WhisperModel(...)`
  construction — **~1.3 s** for `small` from a warm disk cache, and much longer
  on the very first run, when the weights are downloaded from Hugging Face.
- **Warm:** every later turn reuses `self._model`. Measured warm median on the
  user's recordings: **1.25 s**, p95 1.38 s.
- The model is **never** released. Once loaded it stays resident for the life of
  the process (~600 MB).

**This does not block the UI, and that was checked rather than assumed.** eel
serves its whole bridge from one cooperative gevent hub, so a slow exposed
function freezes everything. `start_voice_turn` does not run the turn: it
dispatches onto the dedicated `NanoAsyncLoop` thread and returns immediately,
reporting progress through the existing phase events. The extra second `small`
costs therefore lands on that loop, never on the bridge.

The one instance is shared with the wake-phrase engine, so there is never a
second copy of `small` in RAM. The wake phrase remains **off**; if it were ever
switched on it would now run `small` over every 2.5 s chunk continuously, which
is far heavier than it was with `tiny`.

### Microphone ownership

There is exactly one authority, `AudioInputProvider`, and one process-wide lock
(`_PORTAUDIO_LOCK`) that every PortAudio construction and teardown passes
through. The persistent capture stream is opened **only by the wake-phrase
engine**, and the wake phrase is **off** — in `config/settings.yaml`
(`wake_phrase_enabled: false`) and in the user's own overlay. So while Nano
idles, nothing holds the microphone; a hotkey turn opens it one-shot and closes
it again.

Consequence for the benchmark: a separate benchmark process does **not**
contend with Nano's lock, because the lock is per-process. It would contend
with Nano only if a voice turn ran at the same instant. The instruction to close
Nano Desktop first is therefore about the *user* (not pressing the hotkey
mid-recording), not about a crash risk.

---

## 2. The benchmark

- [`core/speech_benchmark.py`](../../core/speech_benchmark.py) — corpus,
  normalisation, WER/CER, entity scoring, report rendering. Pure: no
  microphone, no model, no network, no filesystem.
- [`scripts/speech_accuracy_benchmark.py`](../../scripts/speech_accuracy_benchmark.py)
  — recording, model execution, resource measurement, orchestration.
- [`scripts/BENCHMARK_VOZ.bat`](../../scripts/BENCHMARK_VOZ.bat) — double-clickable
  launcher. It stays in `scripts/` because `tests/test_launcher.py` enforces
  that the project root holds only the two public launchers.

Each phrase is recorded **once**, through Nano's own `AudioInputProvider` built
from the live merged config, and every configuration is then replayed over that
identical WAV. Each configuration runs in its own subprocess, which gives a
genuinely cold load to time, an uncontaminated RSS/VRAM reading, and crash
isolation.

Sessions are written to `runtime/speech_benchmark/<timestamp>/` — **git-ignored**,
because those files are recordings of a private human voice.

---

## 3. Model lifetime

**Nothing reloads per activation.** Measured cold-load costs on this machine
(warm disk cache, CPU/int8): `tiny` ≈ 0.75 s, `base` ≈ 0.83 s, `small` ≈ 1.34 s.
Paying that on every Ctrl+Shift+Space would add 1.3 s of dead air to a turn the
user experiences as instantaneous — a worse regression than the transcription
errors being fixed.

The lazy-once-then-cached lifetime in `LocalSTTProvider` was already the right
shape and needed **no change** for the model swap; a test now pins it. It has
one property worth keeping in mind rather than "fixing": the cost lands on the
*first* voice turn, not at startup.

Idle cost of keeping the model resident, measured with the benchmark's own
sampler:

| model | process RSS (CPU/int8) | VRAM delta at load (CUDA/float16) |
|---|---|---|
| tiny | ~180 MB | ~160 MB |
| base | ~230 MB | ~225 MB |
| small | ~720 MB | ~640 MB |

(The VRAM column is what the weights cost *at load*, which succeeds; inference
on CUDA does not work here — see below.)

On 16 GB of RAM, `small` on CPU costs about half a gigabyte of resident memory
for the whole session. That is affordable but not free, and it is a real input
to the decision, not a footnote.

**The GPU is NOT an option on this machine today — measured, not assumed.**
This is worth writing down carefully, because every cheap check says otherwise:

- `ctranslate2.get_cuda_device_count()` returns **1**.
- `WhisperModel("tiny", device="cuda", compute_type="float16")` **constructs
  successfully**, in about 0.3 s.
- The first real inference then fails:
  `RuntimeError: Library cublas64_12.dll is not found or cannot be loaded`.

The wheel ships `cudnn64_9.dll` but not cuBLAS, and cuBLAS is loaded lazily at
the first `encode()` — so nothing before that moment reveals the problem.

Worse, **it does not always fail cleanly**. In a single-threaded script it
raises. Inside the benchmark worker, where a resource sampler thread and tqdm's
monitor threads are alive, the identical call **hangs inside native code and
never returns**; three stuck Python processes had to be killed with `taskkill`
while this was being characterised. A benchmark that offered CUDA rows on a
device count would have stranded the user at a frozen console *after* they had
already read thirty phrases aloud.

Hence `gpu_is_usable()` runs a real transcription in a throwaway subprocess,
under a timeout, with `vad_filter=False` so the encoder is genuinely reached,
and GPU rows appear only if that produced output. Today it prints:

```
GPU: NAO — CUDA nao consegue transcrever: RuntimeError: Library
cublas64_12.dll is not found or cannot be loaded
```

If the missing library is ever installed (`pip install nvidia-cublas-cu12`, or a
CUDA 12 toolkit on `PATH`), the probe will pass on its own and the GPU rows
appear with no code change. Note then that STT on the GPU competes for the same
6 GB a local Ollama model would want, so it interacts with `provider_mode`
(currently `CLOUD`).

**Any eventual preload at startup must not repeat the `get_settings()` freeze.**
eel serves its whole bridge from one cooperative gevent hub; a first import of a
heavy native extension inside an exposed function froze the UI permanently once
already. If the model is ever loaded eagerly, it must be on a background thread
that the eel hub is not waiting on.

---

## 4. A future contextual-correction layer (design, NOT implemented)

The benchmark deliberately does **no** post-correction: the whole point is to
see what Whisper actually heard. Correction is a separate, later feature, and
its design is constrained by one rule:

> **An uncertain transcription must never be silently turned into an action.**

"apaga" and "abre" differ by one phoneme. A correction layer that is permissive
enough to rescue "Alana no tudo bem" → "Olá Nano, tudo bem?" is, by
construction, permissive enough to turn "abre os ficheiros" into "apaga os
ficheiros". Those two mistakes have wildly different costs, so they must not
share a policy. (Corpus phrase 030 makes the user say both words in one
sentence, so the benchmark measures exactly this confusion.)

Two lanes, chosen by what the transcript is *for*:

**Conversation — may be permissive.** Nothing irreversible happens, so a wrong
guess costs an odd reply. Correction here can be lexical and cheap: a small
Portuguese alias table for Nano's own vocabulary (the `Keyword` variants in the
corpus are the seed of it), plus wake-word repair. It should still be logged, so
"Nano answered something strange" stays traceable to the substitution.

**Actions — must be conservative.** The flow:

```
audio
  → STT (raw transcript + segment confidence + no-speech probability)
  → ambiguity analysis
       · low confidence on any span?
       · does a near-miss map to a DESTRUCTIVE verb?  (apagar/eliminar/formatar)
       · more than one plausible reading?
  → clarification turn if needed  ("Queres ABRIR ou APAGAR?")
  → Model
  → Policy
  → Permission
  → ToolExecutor
```

Non-negotiables for that layer when it is built:

1. **Never** rewrite a transcript into a destructive verb. Correction may only
   move *towards* asking, never towards acting.
2. Ambiguity resolves to a **question**, not to a best guess.
3. The raw transcript stays attached to the request all the way to the executor,
   so the permission prompt can show what was actually heard.
4. No fuzzy matching against the destructive verb list — the failure mode of
   fuzzy matching is precisely that it *finds* "apagar" in "abre a pasta".

faster-whisper already returns the signals this needs: `avg_logprob`,
`no_speech_prob` and `compression_ratio` per segment. None of them are read
today; capturing them is a prerequisite, not part of this phase.

---

## 5. What this phase deliberately did not touch

The adaptive gate and its thresholds, `no_speech` detection, microphone
ownership and capture format, the global hotkey, the overlay, Groq routing, TTS,
and the wake phrase (still off). Capture detection and transcription accuracy
are different problems and are kept that way — tests assert the gate constants
and the 16 kHz mono capture format are unchanged.

No fuzzy correction layer was built. PC-control phrasing is still 51.7% WER, and
a corrector permissive enough to rescue it would be permissive enough to turn
"abre" into "apaga". That is a separate design (section 4), not a patch.
