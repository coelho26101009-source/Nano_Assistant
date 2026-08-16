from __future__ import annotations

import argparse
import time

import numpy as np
import pyaudio

from core.config import load_config
from core.wake_word import resolve_wake_word_model_path, validate_wake_word_model


try:
    from openwakeword.model import Model
except Exception as exc:  # pragma: no cover - environment specific
    Model = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _parse_args() -> argparse.Namespace:
    cfg = load_config().get("voice", {}).get("wake_word", {})
    parser = argparse.ArgumentParser(description="Manual openWakeWord verification for the Nano wake phrase.")
    parser.add_argument("--phrase", default=str(cfg.get("phrase") or "Nano"), help="Phrase to listen for (default: Nano)")
    parser.add_argument("--model-path", default=resolve_wake_word_model_path(cfg), help="Path to a custom .onnx/.tflite openWakeWord model")
    parser.add_argument("--duration", type=float, default=10.0, help="Seconds to listen before exiting")
    parser.add_argument("--threshold", type=float, default=float(cfg.get("threshold", 0.7)), help="Detection threshold")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if Model is None:
        print("Wake word test unavailable")
        print("Provider: openWakeWord")
        print("Reason:")
        print(str(_IMPORT_ERROR))
        print("Required:")
        print("pip install openwakeword onnxruntime")
        return 1

    validation = validate_wake_word_model(args.model_path, phrase=args.phrase, provider="openwakeword")
    if not validation["ok"]:
        print("Wake word: SETUP REQUIRED")
        print("Provider: openWakeWord")
        print(f"Phrase: {args.phrase}")
        print(f"Model: {validation.get('model', 'NOT FOUND')}")
        print("Required: custom wake-word model")
        print(f"Reason: {validation.get('reason', 'Model is not configured')}")
        return 1

    print("NANO WAKE WORD TEST")
    print(f"Provider: openWakeWord")
    print(f"Phrase: {args.phrase}")
    print(f"Model path: {validation.get('path')}")
    print(f"Threshold: {args.threshold}")
    print(f"Duration: {args.duration}s")

    try:
        model = Model(wakeword_models=[validation["path"]], inference_framework="onnx")
    except Exception as exc:
        print("Wake word: SETUP REQUIRED")
        print("Provider: openWakeWord")
        print(f"Phrase: {args.phrase}")
        print("Model: INVALID")
        print(f"Reason: {exc}")
        return 1

    pa = pyaudio.PyAudio()
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1280)
    try:
        started = time.monotonic()
        while time.monotonic() - started < args.duration:
            chunk = stream.read(1280, exception_on_overflow=False)
            if not chunk:
                continue
            audio = np.frombuffer(chunk, dtype=np.int16)
            scores = model.predict(audio)
            if scores:
                best_name, best_score = max(scores.items(), key=lambda item: item[1])
                best = float(best_score)
                if best >= args.threshold:
                    print(f"Wake word detected")
                    print(f"Phrase: {args.phrase}")
                    print(f"Score: {best:.4f}")
                    return 0
                print(f"Listening... current score: {best:.4f} | phrase={args.phrase}")
            time.sleep(0.05)
        print("Wake word: NO DETECTION")
        print(f"Phrase: {args.phrase}")
        print(f"Threshold: {args.threshold}")
        print("Result: no wake-word activation recorded during the test window.")
        return 0
    finally:
        try:
            stream.stop_stream(); stream.close(); pa.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
