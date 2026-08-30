# Nano wake-word training plan

This file documents the controlled, reproducible workflow for creating a custom local wake-word model for the phrase:

```text
Nano
```

The goal is to create a valid openWakeWord model that can be exported to a final `.onnx` file and then loaded by the Nano runtime on Windows.

## Current status

- Runtime architecture is already prepared.
- openWakeWord runtime package is installed in this environment.
- The installed version is `openWakeWord 0.6.0`.
- The training stack is not installed in the main runtime, and this is intentional.
- This document is a plan only. No heavy training is started here.

## 1. Runtime vs training split

### Runtime environment

```text
Windows 11
Ryzen 7 5700X
GTX 1660 Ti
16 GB RAM
```

The runtime should stay simple and local-first. It only needs:

- microphone access
- PyAudio
- openWakeWord runtime
- the final `.onnx` model file
- the Nano voice runtime

### Recommended training environment

The custom training workflow is better suited to:

- Linux
- WSL2
- Colab
- another GPU-capable environment

This is because the training path relies on PyTorch and model-training tooling that is not part of the lean Windows runtime.

The recommended pattern is:

```text
TRAIN ELSEWHERE
  -> export `.onnx`
  -> copy model to Windows
  -> validate in Nano runtime
  -> deploy
```

## 2. Target phrase and initial scope

```yaml
model_name: "nano"
target_phrase:
  - "Nano"
```

This phase intentionally keeps a single target phrase. We do not train multiple phrases at once, and we do not add future variants until the first valid model is ready.

## 3. Training configuration

The baseline configuration lives at:

```text
tools/wakeword/training/config/nano.yaml
```

### openWakeWord 0.6.0 configuration

The real installed version of `openWakeWord` uses the following schema in `train.py`:

```yaml
model_name: "nano"
target_phrase:
  - "Nano"
custom_negative_phrases: []

n_samples: 20000
n_samples_val: 4000

tts_batch_size: 32
augmentation_batch_size: 64

piper_sample_generator_path: "REQUIRED BEFORE TRAINING"
output_dir: "models/wakeword/train-output"

rir_paths: []
background_paths: []
background_paths_duplication_rate: []
false_positive_validation_data_path: "REQUIRED BEFORE TRAINING"

augmentation_rounds: 2
feature_data_files: {}
batch_n_per_class: 32

model_type: "dnn"
layer_size: 32

steps: 20000
max_negative_weight: 1000
target_false_positives_per_hour: 0.5
```

Important corrections made versus the old YAML:

- `negative_phrases` was removed because it is not an official config key used by `openWakeWord 0.6.0`.
- `target_false_positive_rate` was removed because the real key is `target_false_positives_per_hour`.
- `output_path` was replaced by `output_dir`, which is the field the actual training script reads and creates.
- `layer_size` was reduced from `128` to `32` for the initial `nano-v1` baseline to keep the model compact and low latency.
- `steps` was set to `20000` as a practical first-pass value; it is a conservative baseline, not a maximal configuration, and it is still a valid starting point before pushing to the official `50000` reference used in the upstream examples.
- the placeholder resource paths are intentionally left as `REQUIRED BEFORE TRAINING` until the external data is actually prepared.

This config is aligned with the installed `openWakeWord 0.6.0` training script, but it remains intentionally non-executing until the external resources are prepared.

## 3.1 Bootstrap and resource validation

The project now includes a dedicated bootstrap script for the Colab training environment:

```bash
python tools/wakeword/training/bootstrap_colab.py --dry-run
```

This verifies:

- Python version
- GPU and CUDA availability
- openWakeWord installation
- PyTorch installation
- minimal training dependencies
- all required external resources
- which resources are ready and which are missing

It does not perform any download or training. The script supports explicit download planning only when the user requests it:

```bash
python tools/wakeword/training/bootstrap_colab.py --download-piper
python tools/wakeword/training/bootstrap_colab.py --download-rirs
python tools/wakeword/training/bootstrap_colab.py --download-background
python tools/wakeword/training/bootstrap_colab.py --download-validation
python tools/wakeword/training/bootstrap_colab.py --download-features
```

These flags only print the plan and the resource metadata; they do not execute a large automated download by default.

## 4. Positive data plan

Positive samples are recordings of the phrase:

```text
Nano
```

The initial data-generation plan should use diversified synthetic voice generation plus later real-world evaluation.

Prefer:

- diverse synthetic voices
- multiple pronunciations
- different speaking speeds
- different intonations
- varied recording distances
- multiple room conditions

Do not rely on a single voice or a single clip. The first generation should be broad but controlled, and the final tuning must happen against real room noise and live microphone conditions.

## 5. Negative data strategy

A custom wake-word model is only useful if it does not fire on ordinary speech.

Negative examples should include:

- normal conversation
- phrases similar to the target sound
- words that are acoustically close
- room noise
- keyboard typing
- fan noise
- game audio
- TV or video audio
- silence and low-energy backgrounds

The config relies on `custom_negative_phrases` as the supported mechanism for additional negative examples in the official openWakeWord 0.6.0 schema.

## 5.1 Required external resources before training

Before any actual model training, the following must be resolved and configured:

- `piper_sample_generator_path`
- `rir_paths`
- `background_paths`
- `false_positive_validation_data_path`
- `feature_data_files`

The project is intentionally keeping these as placeholders because the actual resources are not downloaded automatically. That keeps the workflow reproducible, explicit, and safe.

## 6. The first training pass

This document does not execute heavy training. The entry point is the openWakeWord training module that ships with the package, but the actual training dependencies are not installed in the main environment.

The intended workflow is:

```bash
# in a Linux / WSL2 / Colab environment
pip install torch torchaudio torchinfo torchmetrics speechbrain audiomentations torch-audiomentations mutagen acoustics pyyaml pronouncing datasets
python -m openwakeword.train --training_config tools/wakeword/training/config/nano.yaml --train_model
```

This is intentionally a planned command, not a command that is executed here.

## 7. Export and deployment

The expected output is versioned instead of overwritten automatically:

```text
models/wakeword/nano-v1.onnx
```

The export stage should explicitly convert the trained model to a final runtime format compatible with the installed openWakeWord runtime:

```text
training output
  -> export to ONNX
  -> copy to models/wakeword/nano-v1.onnx
  -> validate with openWakeWord.Model(...)
  -> deploy on Windows
```

The runtime configuration should then point to the exported file:

```yaml
voice:
  wake_word:
    enabled: true
    phrase: "Nano"
    provider: "openwakeword"
    model_path: "models/wakeword/nano.onnx"
    threshold: 0.7
```

## 8. Validation plan

After export, validate that the model is real and loadable:

- file exists
- extension is `.onnx` or `.tflite`
- the provider loads without error
- input format matches the expected sample rate
- inference returns scores

The repo already contains a validation concept in:

- [core/wake_word.py](../../../core/wake_word.py)
- [core/wake_word_test.py](../../../core/wake_word_test.py)
- [core/wake_word_test_file.py](../../../core/wake_word_test_file.py)

The file-based test path is especially useful before live microphone testing:

```bash
python -m core.wake_word_test_file --model models/wakeword/nano.onnx --audio test.wav --threshold 0.7
```

## 9. False positive and false negative evaluation

The first model should be judged using:

- true positives
- false positives
- false negatives
- true negatives

This is more realistic than accepting a single successful detection. The tuned threshold should be based on real room noise and microphone conditions.

The initial threshold is configured as a conservative default:

```yaml
validation:
  threshold: 0.7
```

This is only a starting value; the exact threshold must be measured and adjusted in the runtime environment.

## 10. Verifier model plan

A future verifier model can sit after the base wake-word model:

```text
Base wake-word model
  -> optional speaker verifier
  -> wake event
```

This is optional and not necessary for the first custom model.

## 11. Versioning and rollback

The model strategy should support versioned outputs:

```text
models/wakeword/nano-v1.onnx
models/wakeword/nano-v2.onnx
models/wakeword/nano-v3.onnx
```

This allows a controlled rollback path if a newer model worsens false positives or fails in real use.

## 12. Minimal deployment path

The simplest and most reliable path is:

1. prepare the training config
2. train in Linux/WSL2/Colab
3. export the final `.onnx`
4. place it in `models/wakeword/nano.onnx`
5. validate with `python -m core.wake_word_test`
6. run `python -m core.voice_diagnostics`
7. test the real live phrase: "Nano, que horas são?"

## 13. What stays out of scope for now

The current phase intentionally does not include:

- large dataset downloads
- long-running training jobs
- broad audio collection and model experimentation
- voice verification tuning without a valid base model
- deployment claims without real validation

## 14. Recommended next action

The next step is not heavy training. The next step is to choose the training environment, generate the initial positive/negative sample plan, and then run the first training job in a dedicated environment only after confirmation.

The simplest path is:

- use WSL2 or Colab for the first custom model
- export `.onnx`
- validate the model locally on the Windows PC
- tune threshold with real room-noise tests
- only then depend on the model for live voice activation
