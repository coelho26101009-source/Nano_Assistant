"""
H.E.L.I.O.S. Wake Word Engine
Escuta contínua em thread daemon por uma palavra de activação ("Helios").

Backends suportados (por ordem de preferência quando backend = "auto"):
  1. pvporcupine   — Picovoice, muito leve (~1% CPU), grátis para uso pessoal.
                     Requer PICOVOICE_ACCESS_KEY no .env.
  2. openwakeword  — 100% offline, sem chave, ONNX/tflite pequeno.

Se nenhum backend estiver instalado, o motor limita-se a registar um aviso e
o resto do H.E.L.I.O.S. continua a funcionar normalmente (clique manual).
"""

import logging
import os
import struct
import threading
import time
from typing import Callable

logger = logging.getLogger("helios.wake_word")

DEFAULT_PHRASE      = "helios"
DEFAULT_SENSITIVITY = 0.6
SAMPLE_RATE         = 16000


class WakeWordEngine:
    """
    Uso:
        ww = WakeWordEngine(config, on_wake=lambda: ...)
        ww.start()      # não bloqueia
        ww.pause()      # durante a resposta, para não se ouvir a si próprio
        ww.resume()
        ww.stop()
    """

    def __init__(self, config: dict | None, on_wake: Callable[[], None]):
        cfg = config or {}
        self.enabled     = bool(cfg.get("wake_word_enabled", False))
        self.phrase      = str(cfg.get("wake_word_phrase", DEFAULT_PHRASE)).strip().lower()
        self.sensitivity = float(cfg.get("wake_word_sensitivity", DEFAULT_SENSITIVITY))
        self.backend     = str(cfg.get("wake_word_backend", "auto")).lower()
        self.cooldown    = float(cfg.get("wake_word_cooldown_seconds", 2.0))
        self.framework   = str(cfg.get("wake_word_framework", "onnx")).lower()
        self.keyword_path = cfg.get("wake_word_keyword_path") or None

        self.on_wake  = on_wake
        self._thread: threading.Thread | None = None
        self._stop    = threading.Event()
        self._paused  = threading.Event()
        self.active_backend: str | None = None
        self.last_error: str | None = None

    # ─── Ciclo de vida ────────────────────────────────────────────────────────

    def start(self) -> bool:
        if not self.enabled:
            logger.info("Wake-word desactivada (wake_word_enabled: false).")
            return False
        if self._thread and self._thread.is_alive():
            return True

        self._stop.clear()
        self._paused.clear()
        self._thread = threading.Thread(target=self._run, name="helios-wake-word", daemon=True)
        self._thread.start()
        return True

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
        return {
            "enabled":     self.enabled,
            "running":     self.running,
            "paused":      self._paused.is_set(),
            "phrase":      self.phrase,
            "backend":     self.active_backend or self.backend,
            "sensitivity": self.sensitivity,
            "error":       self.last_error,
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
                    self.last_error = f"Backend de wake-word desconhecido: {backend}"
                    logger.error(self.last_error)
                return
            except _BackendUnavailable as exc:
                logger.warning(f"Wake-word ({backend}) indisponível: {exc}")
                self.last_error = str(exc)
            except Exception as exc:
                logger.error(f"Wake-word ({backend}) falhou: {exc}", exc_info=True)
                self.last_error = str(exc)
                return

        logger.warning(
            "Nenhum backend de wake-word disponível. "
            "Instala 'pvporcupine' (com PICOVOICE_ACCESS_KEY) ou 'openwakeword'."
        )

    def _trigger(self):
        logger.info(f"🔔 Wake-word '{self.phrase}' detectada.")
        try:
            self.on_wake()
        except Exception as exc:
            logger.error(f"Erro no callback da wake-word: {exc}", exc_info=True)
        time.sleep(self.cooldown)

    # ─── Backend: Picovoice Porcupine ────────────────────────────────────────

    def _listen_porcupine(self):
        try:
            import pvporcupine
        except ImportError as exc:
            raise _BackendUnavailable("pvporcupine não instalado (pip install pvporcupine)") from exc

        access_key = os.getenv("PICOVOICE_ACCESS_KEY", "")
        if not access_key:
            raise _BackendUnavailable("PICOVOICE_ACCESS_KEY em falta no .env")

        kwargs = {"access_key": access_key, "sensitivities": [self.sensitivity]}
        if self.keyword_path:
            kwargs["keyword_paths"] = [self.keyword_path]
        else:
            builtin = set(pvporcupine.KEYWORDS)
            if self.phrase not in builtin:
                raise _BackendUnavailable(
                    f"'{self.phrase}' não é uma keyword incluída no Porcupine. "
                    f"Cria um ficheiro .ppn em console.picovoice.ai e define "
                    f"wake_word_keyword_path, ou usa uma destas: {sorted(builtin)}"
                )
            kwargs["keywords"] = [self.phrase]

        porcupine = pvporcupine.create(**kwargs)
        self.active_backend = "porcupine"
        logger.info(f"Wake-word activa (porcupine): '{self.phrase}'")

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
                "openwakeword/numpy não instalados (pip install openwakeword numpy)"
            ) from exc

        model = self._load_openwakeword_model(Model)
        available = [_normalize(name) for name in model.models]
        if self.keyword_path:
            targets = available
        else:
            targets = [name for name in available if self.phrase in name]
            if not targets:
                raise _BackendUnavailable(
                    f"'{self.phrase}' não tem modelo openWakeWord instalado. "
                    f"Disponíveis: {available}. Treina um modelo para '{self.phrase}' "
                    f"(github.com/dscripka/openWakeWord) e aponta wake_word_keyword_path "
                    f"para o .onnx, ou usa uma das frases acima."
                )

        self.active_backend = "openwakeword"
        logger.info(f"Wake-word activa (openwakeword): '{self.phrase}' → {targets}")

        frame_length = 1280   # 80 ms @ 16 kHz, tamanho esperado pelo openWakeWord
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
        """Carrega o modelo com o runtime configurado; onnx é o mais portátil."""
        kwargs = {"wakeword_models": [self.keyword_path]} if self.keyword_path else {}
        frameworks = [self.framework] + [f for f in ("onnx", "tflite") if f != self.framework]
        last_error: Exception | None = None
        for framework in frameworks:
            try:
                return model_cls(inference_framework=framework, **kwargs)
            except Exception as exc:
                logger.warning(f"openWakeWord ({framework}) não carregou: {exc}")
                last_error = exc
        raise _BackendUnavailable(f"openWakeWord não carregou em nenhum runtime: {last_error}")

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
    """Backend não instalado ou mal configurado — tentar o próximo."""


class _MicStream:
    """Stream de microfone PyAudio com leitura tolerante a overflow."""

    def __init__(self, sample_rate: int, frame_length: int):
        self.sample_rate  = sample_rate
        self.frame_length = frame_length
        self._pa     = None
        self._stream = None

    def __enter__(self):
        try:
            import pyaudio
        except ImportError as exc:
            raise _BackendUnavailable("pyaudio não instalado (pip install pyaudio)") from exc

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
            logger.debug(f"Leitura do microfone falhou: {exc}")
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
