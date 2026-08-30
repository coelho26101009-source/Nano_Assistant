"""
Nano Wake Word Engine
Continuous listening on a daemon thread for an activation phrase ("Nano").

Supported backends (in preference order when backend = "auto"):
  1. pvporcupine   — Picovoice, very lightweight (~1% CPU), free for personal use.
                     Requires PICOVOICE_ACCESS_KEY in .env.
  2. openwakeword  — 100% offline, no key, small ONNX/tflite model.

If no backend is installed, the engine just logs a warning and the rest of
Nano keeps working normally (manual click).
"""

import logging
import os
import struct
import threading
import time
from typing import Callable

logger = logging.getLogger("helios.wake_word")

DEFAULT_PHRASE      = "nano"
DEFAULT_SENSITIVITY = 0.6
SAMPLE_RATE         = 16000


def _normalize_phrase(name: str | None) -> str:
    return str(name or "").strip().lower().replace(" ", "_")


def resolve_wake_word_model_path(config: dict | None = None) -> str:
    cfg = config or {}
    for key in ("model_path", "keyword_path", "wake_word_keyword_path", "modelFile", "model_file"):
        value = cfg.get(key)
        if value:
            return str(value)
    return ""


def validate_wake_word_model(model_path: str | None, *, phrase: str | None = None, provider: str = "openwakeword") -> dict:
    phrase_name = _normalize_phrase(phrase or "Nano")
    provider_name = str(provider or "openwakeword").lower()
    path = (model_path or "").strip()
    if not path:
        return {
            "ok": False,
            "status": "MISSING",
            "provider": provider_name,
            "phrase": phrase_name,
            "model": "NOT FOUND",
            "required": "custom wake-word model",
            "reason": "The wake-word model path is empty.",
        }
    expanded = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(expanded):
        return {
            "ok": False,
            "status": "MISSING",
            "provider": provider_name,
            "phrase": phrase_name,
            "model": "NOT FOUND",
            "required": "custom wake-word model",
            "reason": f"Model file does not exist: {expanded}",
            "path": expanded,
        }
    ext = os.path.splitext(expanded)[1].lower()
    if ext not in {".onnx", ".tflite"}:
        return {
            "ok": False,
            "status": "INVALID",
            "provider": provider_name,
            "phrase": phrase_name,
            "model": "INVALID",
            "required": "custom wake-word model",
            "reason": f"Unsupported model extension '{ext}'. Use .onnx or .tflite.",
            "path": expanded,
        }
    if provider_name == "openwakeword":
        try:
            from openwakeword.model import Model
            Model(wakeword_models=[expanded], inference_framework="onnx")
        except Exception as exc:
            return {
                "ok": False,
                "status": "LOAD_ERROR",
                "provider": provider_name,
                "phrase": phrase_name,
                "model": "INVALID",
                "required": "custom wake-word model",
                "reason": f"Model could not be loaded by the provider: {exc}",
                "path": expanded,
            }
    return {
        "ok": True,
        "status": "READY",
        "provider": provider_name,
        "phrase": phrase_name,
        "model": "FOUND",
        "required": "custom wake-word model",
        "path": expanded,
    }


def _openwakeword_models() -> list[str]:
    try:
        import openwakeword
        models = getattr(openwakeword, "MODELS", {})
        return [str(name).strip().lower() for name in models.keys()]
    except Exception:
        return []


class WakeWordEngine:
    """
    Usage:
        ww = WakeWordEngine(config, on_wake=lambda: ...)
        ww.start()      # does not block
        ww.pause()      # during the response, so it does not hear itself
        ww.resume()
        ww.stop()
    """

    def __init__(self, config: dict | None, on_wake: Callable[[], None]):
        cfg = config or {}
        self.enabled     = bool(cfg.get("enabled", cfg.get("wake_word_enabled", False)))
        self.phrase      = str(cfg.get("phrase") or cfg.get("wake_word_phrase") or DEFAULT_PHRASE).strip().lower()
        self.provider    = str(cfg.get("provider") or cfg.get("wake_word_provider") or "openwakeword").lower()
        self.sensitivity = float(cfg.get("threshold", cfg.get("wake_word_sensitivity", DEFAULT_SENSITIVITY)))
        self.backend     = str(cfg.get("backend") or cfg.get("wake_word_backend") or self.provider or "auto").lower()
        self.cooldown    = float(cfg.get("cooldown", cfg.get("cooldown_seconds", cfg.get("wake_word_cooldown_seconds", 2.0))))
        self.framework   = str(cfg.get("framework") or cfg.get("wake_word_framework", "onnx")).lower()
        self.model_path  = resolve_wake_word_model_path(cfg)
        self.keyword_path = self.model_path or cfg.get("keyword_path") or cfg.get("wake_word_keyword_path") or None

        self.on_wake  = on_wake
        self._thread: threading.Thread | None = None
        self._stop    = threading.Event()
        self._paused  = threading.Event()
        self.active_backend: str | None = None
        self.last_error: str | None = None

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> bool:
        if not self.enabled:
            logger.info("Wake word disabled (wake_word_enabled: false).")
            return False
        validation = validate_wake_word_model(self.model_path or self.keyword_path, phrase=self.phrase, provider=self.provider)
        if not validation["ok"]:
            logger.warning(
                "Wake-word architecture is ready but the live keyword model for '%s' is not usable: %s",
                self.phrase,
                validation.get("reason", "model missing"),
            )
            if not (self.model_path or self.keyword_path):
                self.last_error = "No live wake-word model configured for the selected phrase."
            else:
                self.last_error = validation.get("reason") or "No live wake-word model configured for the selected phrase."
            return False
        if self._thread and self._thread.is_alive():
            return True

        self._stop.clear()
        self._paused.clear()
        self._thread = threading.Thread(target=self._run, name="nano-wake-word", daemon=True)
        self._thread.start()
        return True

    def _can_start(self) -> bool:
        validation = validate_wake_word_model(self.model_path or self.keyword_path, phrase=self.phrase, provider=self.provider)
        return bool(validation.get("ok"))

    def stop(self):
        self._stop.set()

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict:
        validation = validate_wake_word_model(self.model_path or self.keyword_path, phrase=self.phrase, provider=self.provider)
        return {
            "enabled":     self.enabled,
            "running":     self.running,
            "paused":      self._paused.is_set(),
            "phrase":      self.phrase,
            "backend":     self.active_backend or self.backend,
            "provider":    self.provider,
            "model_path":  self.model_path or self.keyword_path,
            "model_status": validation.get("status"),
            "sensitivity": self.sensitivity,
            "error":       self.last_error or validation.get("reason"),
        }

    # ─── Loop ─────────────────────────────────────────────────────────────────

    def _run(self):
        order = ["porcupine", "openwakeword"] if self.backend == "auto" else [self.backend]
        for backend in order:
            try:
                if backend == "porcupine":
                    self._listen_porcupine()
                elif backend == "openwakeword":
                    self._listen_openwakeword()
                else:
                    self.last_error = f"Unknown wake-word backend: {backend}"
                    logger.error(self.last_error)
                return
            except _BackendUnavailable as exc:
                logger.warning(f"Wake word ({backend}) unavailable: {exc}")
                self.last_error = str(exc)
            except Exception as exc:
                logger.error(f"Wake word ({backend}) failed: {exc}", exc_info=True)
                self.last_error = str(exc)
                return

        logger.warning(
            "No wake-word backend available. "
            "Install 'pvporcupine' (with PICOVOICE_ACCESS_KEY) or 'openwakeword' plus a compatible live model."
        )

    def _trigger(self):
        logger.info(f"🔔 Wake word '{self.phrase}' detected.")
        try:
            self.on_wake()
        except Exception as exc:
            logger.error(f"Error in the wake-word callback: {exc}", exc_info=True)
        time.sleep(self.cooldown)

    # ─── Backend: Picovoice Porcupine ────────────────────────────────────────

    def _listen_porcupine(self):
        try:
            import pvporcupine
        except ImportError as exc:
            raise _BackendUnavailable("pvporcupine not installed (pip install pvporcupine)") from exc

        access_key = os.getenv("PICOVOICE_ACCESS_KEY", "")
        if not access_key:
            raise _BackendUnavailable("PICOVOICE_ACCESS_KEY missing from .env")

        kwargs = {"access_key": access_key, "sensitivities": [self.sensitivity]}
        if self.keyword_path:
            kwargs["keyword_paths"] = [self.keyword_path]
        else:
            builtin = set(pvporcupine.KEYWORDS)
            if self.phrase not in builtin:
                raise _BackendUnavailable(
                    f"'{self.phrase}' is not a built-in Porcupine keyword. "
                    f"Create a .ppn file at console.picovoice.ai and set "
                    f"wake_word_keyword_path, or use one of these: {sorted(builtin)}"
                )
            kwargs["keywords"] = [self.phrase]

        porcupine = pvporcupine.create(**kwargs)
        self.active_backend = "porcupine"
        logger.info(f"Wake word active (porcupine): '{self.phrase}'")

        stream_ctx = _MicStream(porcupine.sample_rate, porcupine.frame_length)
        try:
            with stream_ctx as mic:
                while not self._stop.is_set():
                    if self._paused.is_set():
                        time.sleep(0.2)
                        continue
                    pcm = mic.read()
                    if pcm is None:
                        continue
                    frame = struct.unpack_from("h" * porcupine.frame_length, pcm)
                    if porcupine.process(frame) >= 0:
                        self._trigger()
        finally:
            porcupine.delete()
            self.active_backend = None

    # ─── Backend: openWakeWord ───────────────────────────────────────────────

    def _listen_openwakeword(self):
        try:
            import numpy as np
            from openwakeword.model import Model
        except ImportError as exc:
            raise _BackendUnavailable(
                "openwakeword/numpy not installed (pip install openwakeword numpy)"
            ) from exc

        if not self.keyword_path and self.phrase not in _openwakeword_models():
            raise _BackendUnavailable(
                f"'{self.phrase}' has no live openWakeWord model available. "
                f"The package is installed, but the wake-word model is not configured. "
                f"Use wake_word.keyword_path for a custom .onnx file, or install a "
                f"compatible model before enabling detection."
            )

        model = self._load_openwakeword_model(Model)
        available = [_normalize(name) for name in getattr(model, "models", {}).keys()]
        if self.keyword_path:
            targets = available
        else:
            targets = [name for name in available if self.phrase in name]
            if not targets:
                raise _BackendUnavailable(
                    f"'{self.phrase}' has no openWakeWord model installed. "
                    f"Available: {available}. Train a model for '{self.phrase}' "
                    f"(github.com/dscripka/openWakeWord) and point wake_word_keyword_path "
                    f"at the .onnx file, or use one of the phrases above."
                )

        self.active_backend = "openwakeword"
        logger.info(f"Wake word active (openwakeword): '{self.phrase}' → {targets}")

        frame_length = 1280   # 80 ms @ 16 kHz, the size openWakeWord expects
        try:
            with _MicStream(SAMPLE_RATE, frame_length) as mic:
                while not self._stop.is_set():
                    if self._paused.is_set():
                        time.sleep(0.2)
                        continue
                    pcm = mic.read()
                    if pcm is None:
                        continue
                    audio  = np.frombuffer(pcm, dtype=np.int16)
                    scores = model.predict(audio)
                    if self._matches(scores, targets):
                        model.reset()
                        self._trigger()
        finally:
            self.active_backend = None

    def _load_openwakeword_model(self, model_cls):
        """Load the model with the configured runtime; onnx is the most portable."""
        if self.keyword_path:
            kwargs = {"wakeword_models": [self.keyword_path]}
        elif self.phrase in _openwakeword_models():
            kwargs = {"wakeword_models": [self.phrase]}
        else:
            raise _BackendUnavailable(f"openWakeWord found no wake-word model for '{self.phrase}'.")

        frameworks = [self.framework] + [f for f in ("onnx", "tflite") if f != self.framework]
        last_error: Exception | None = None
        for framework in frameworks:
            try:
                return model_cls(inference_framework=framework, **kwargs)
            except Exception as exc:
                logger.warning(f"openWakeWord ({framework}) failed to load: {exc}")
                last_error = exc
        raise _BackendUnavailable(f"openWakeWord failed to load on any runtime: {last_error}")

    def _matches(self, scores: dict, targets: list[str]) -> bool:
        for name, score in scores.items():
            if score < self.sensitivity:
                continue
            normalized = _normalize(name)
            if any(target in normalized or normalized in target for target in targets):
                return True
        return False


def _normalize(name: str) -> str:
    return name.lower().replace("_", " ").strip()


class _BackendUnavailable(RuntimeError):
    """Backend not installed or misconfigured — try the next one."""


class _MicStream:
    """PyAudio microphone stream with overflow-tolerant reads."""

    def __init__(self, sample_rate: int, frame_length: int):
        self.sample_rate  = sample_rate
        self.frame_length = frame_length
        self._pa     = None
        self._stream = None

    def __enter__(self):
        try:
            import pyaudio
        except ImportError as exc:
            raise _BackendUnavailable("pyaudio not installed (pip install pyaudio)") from exc

        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            rate=self.sample_rate,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=self.frame_length,
        )
        return self

    def read(self) -> bytes | None:
        try:
            return self._stream.read(self.frame_length, exception_on_overflow=False)
        except Exception as exc:
            logger.debug(f"Microphone read failed: {exc}")
            time.sleep(0.1)
            return None

    def __exit__(self, *_exc):
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        finally:
            if self._pa is not None:
                self._pa.terminate()
