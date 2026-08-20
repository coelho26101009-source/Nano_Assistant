# Nano wake-word Colab bootstrap

This is the exact preparation plan for the first custom model for the phrase `Nano` in the dedicated Colab training environment.

## 1. Open Colab

- open Google Colab
- create a new notebook
- select `Python 3.11`
- choose `GPU` runtime in the Colab toolbar

## 2. Verify the environment

Run:

```python
import platform, torch
print(platform.python_version())
print(torch.__version__)
print(torch.cuda.is_available())
print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA unavailable')
```

Expected for this project:

```text
Python: 3.11.16
GPU: NVIDIA Tesla T4
CUDA available: True
```

## 3. Install training dependencies

The minimal stack should be installed only after the environment is confirmed:

```bash
pip install torch torchaudio torchinfo torchmetrics speechbrain audiomentations torch-audiomentations mutagen acoustics pyyaml pronouncing datasets
```

Do not install a broad "full" stack unless it is explicitly needed.

## 4. Install openWakeWord

```bash
pip install openwakeword==0.6.0
```

## 5. Clone or sync the Nano repo

```bash
git clone <repo-url>
cd <repo-folder>
```

## 6. Validate config

```bash
python tools/wakeword/training/bootstrap_colab.py --dry-run
```

This must confirm:

- Python version is compatible
- GPU is visible to PyTorch
- openWakeWord is installed
- training dependencies are either ready or still missing
- the external resources are either prepared or still placeholder values

## 7. Validate the training configuration

```bash
python -m core.wake_word_training --config tools/wakeword/training/config/nano.yaml --dry-run
```

Expected output:

```text
Configuration:
VALID

Training:
NOT STARTED
```

## 8. Resolve the external resources

The current `nano.yaml` requires the following to be prepared before actual training:

- `piper_sample_generator_path`
- `rir_paths`
- `background_paths`
- `false_positive_validation_data_path`
- `feature_data_files`

The bootstrap script can tell you which are still missing. It does not download anything in dry-run mode.

## 9. Explicit download phase

Only once you are ready to fetch a resource:

```bash
python tools/wakeword/training/bootstrap_colab.py --download-piper
python tools/wakeword/training/bootstrap_colab.py --download-rirs
python tools/wakeword/training/bootstrap_colab.py --download-background
python tools/wakeword/training/bootstrap_colab.py --download-validation
python tools/wakeword/training/bootstrap_colab.py --download-features
```

The script only prints the plan and does not automatically start a large download from this repository.

## 10. Dry-run before training

Before touching the training pipeline, run:

```bash
python tools/wakeword/training/bootstrap_colab.py --dry-run
python -m core.wake_word_training --config tools/wakeword/training/config/nano.yaml --dry-run
```

If everything is ready, proceed to the training command.

## 11. Actual training command

When approved by the user:

```bash
python -m openwakeword.train --training_config tools/wakeword/training/config/nano.yaml --train_model
```

This is intentionally kept as the final step only.

## 12. Expected output

The output should be a versioned model such as:

```text
models/wakeword/train-output/nano/nano.onnx
```

Then the exported model can be copied and renamed for runtime deployment, for example:

```text
models/wakeword/nano-v1.onnx
```

## 13. Download trained model to Windows

After training completes in Colab:

1. download the trained `.onnx` model
2. copy it to the Nano Windows runtime folder
3. validate with `python -m core.wake_word_test`
4. run `python -m core.voice_diagnostics`
5. test the first live phrase: "Nano, que horas são?"

This is the exact sequence to keep the workflow predictable and safe.
