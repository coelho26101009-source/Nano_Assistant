from __future__ import annotations

import argparse
import math
import wave
from pathlib import Path

import numpy as np


try:
    from openwakeword.model import Model
except Exception as exc:  # pragma: no cover - environment specific
    Model = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())
    samples = np.frombuffer(frames, dtype=f"<i{sample_width}")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples.astype(np.float32), sample_rate


def _score_audio(model, audio: np.ndarray, sample_rate: int, threshold: float) -> tuple[bool, float, dict]:
    if sample_rate != 16000:
        return False, 0.0, {"reason": f"Unsupported sample rate: {sample_rate}. Expected 16000 Hz."}
    scores = model.predict(audio)
    if not scores:
        return False, 0.0, {"reason": "Model returned no score."}
    label, value = max(scores.items(), key=lambda pair: float(pair[1]))
    return float(value) >= threshold, float(value), {"label": str(label), "score": float(value), "threshold": threshold}


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline openWakeWord score test over a WAV file.")
    parser.add_argument("--model", required=True, help="Path to a valid .onnx/.tflite model")
    parser.add_argument("--audio", required=True, help="Path to the .wav file to test")
    parser.add_argument("--threshold", type=float, default=0.7, help="Detection threshold")
    args = parser.parse_args()

    if Model is None:
        print("Wake word file test unavailable")
        print(str(_IMPORT_ERROR))
        return 1

    model_path = str(args.model)
    audio_path = str(args.audio)
    try:
        model = Model(wakeword_models=[model_path], inference_framework="onnx")
    except Exception as exc:
        print("Model load failed")
        print(str(exc))
        return 1

    try:
        samples, sample_rate = _read_wav(audio_path)
    except Exception as exc:
        print("Audio file could not be read")
        print(str(exc))
        return 1

    detected, score, meta = _score_audio(model, samples, sample_rate, args.threshold)
    print(f"File: {audio_path}")
    print(f"Model: {model_path}")
    print(f"Threshold: {args.threshold}")
    print(f"Detected: {detected}")
    print(f"Score: {score:.4f}")
    if meta.get("label"):
        print(f"Label: {meta['label']}")
    if not detected:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
