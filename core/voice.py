from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import os
import threading
import time
import wave
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable

logger = logging.getLogger("nano.voice")


class ProviderError(RuntimeError):
    pass


class VoiceSessionState(str, Enum):
    IDLE = "IDLE"
    WAITING_WAKEWORD = "WAITING_WAKEWORD"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    EXECUTING = "EXECUTING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"


@dataclass
class VoiceResult:
    text: str = ""
    confidence: float | None = None
    language: str = "pt-PT"
    duration: float = 0.0
    provider: str = "unknown"
    ok: bool = False
    error: str | None = None


class BaseProvider:
    name = "base"
    local = True
    online = True
    capabilities: Iterable[str] = ()

    def status(self) -> dict:
        return {
            "name": self.name,
            "local": self.local,
            "online": self.online,
            "capabilities": list(self.capabilities),
            "health": "READY" if self.online else "OFFLINE",
        }


class WakeWordProvider(BaseProvider):
    name = "wake_word"
    capabilities = ("wake_word",)

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.config = cfg
        self.enabled = bool(cfg.get("enabled", False))
        self.phrase = str(cfg.get("phrase") or cfg.get("wake_word_phrase") or "Nano").strip().lower()
        self.threshold = float(cfg.get("threshold", 0.7))
        self.cooldown = float(cfg.get("cooldown", 2.0))
        self._lock = threading.Lock()
        self._last_detection = 0.0
        self._active = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._callback: Callable[[], None] | None = None
        self._error: str | None = None

    def start(self, callback: Callable[[], None] | None = None) -> bool:
        if callback is not None:
            self._callback = callback
        self._active = True
        return True

    def stop(self) -> None:
        self._active = False
        self._stop_event.set()

    def detect(self, audio_chunk: Any | None = None) -> bool:
        if not self.enabled:
            raise ProviderError("wake word disabled")
        if not self._active:
            raise ProviderError("wake word not started")
        if time.time() - self._last_detection < self.cooldown:
            return False
        if audio_chunk is None:
            self._last_detection = time.time()
            return False
        self._last_detection = time.time()
        return bool(audio_chunk)

    def status(self) -> dict:
        data = super().status()
        data.update({
            "enabled": self.enabled,
            "phrase": self.phrase,
            "threshold": self.threshold,
            "cooldown": self.cooldown,
            "active": self._active,
            "error": self._error,
        })
        return data


class LocalWakeWordProvider(WakeWordProvider):
    name = "local_wake_word"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.online = importlib.util.find_spec("pvporcupine") is not None or importlib.util.find_spec("openwakeword") is not None
        self.model_path = str(self.config.get("model_path") or self.config.get("keyword_path") or self.config.get("wake_word_keyword_path") or "").strip()
        self.provider = str(self.config.get("provider") or "openwakeword").lower()
        self._model = None
        self._is_ready = self.online
        self._error = None
        if not self.online:
            self.enabled = False
            self._error = "wake word runtime not installed"

    def _supports_phrase(self) -> bool:
        phrase = self.phrase.replace(" ", "_").lower()
        if self.model_path:
            return True
        try:
            import openwakeword
            models = getattr(openwakeword, "MODELS", {})
            return phrase in {str(name).strip().lower() for name in models.keys()}
        except Exception:
            return False

    def start(self, callback: Callable[[], None] | None = None) -> bool:
        if callback is not None:
            self._callback = callback
        if not self.online:
            self._error = "local wake word provider unavailable"
            raise ProviderError(self._error)
        if not self._supports_phrase():
            self.enabled = False
            self._error = "wake word model unavailable for the configured phrase"
            raise ProviderError(self._error)
        if self._thread and self._thread.is_alive():
            self._active = True
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_listener, daemon=True, name="nano-wake-word")
        self._thread.start()
        self._active = True
        return True

    def stop(self) -> None:
        self._stop_event.set()
        self._active = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run_listener(self) -> None:
        try:
            import numpy as np
            import pyaudio
            from openwakeword.model import Model
        except Exception as exc:  # pragma: no cover - environment specific
            self._error = str(exc)
            raise ProviderError(str(exc)) from exc

        model_names = []
        keyword_path = self.model_path or self.config.get("keyword_path") or self.config.get("wake_word_keyword_path")
        if keyword_path:
            model_names = [keyword_path]
        else:
            model_names = [self.phrase.replace(" ", "_")]
        try:
            model = Model(wakeword_models=model_names, inference_framework="onnx")
        except Exception as exc:  # pragma: no cover - environment specific
            self._error = str(exc)
            raise ProviderError(str(exc)) from exc
        self._model = model
        frame_size = 1280
        pa = pyaudio.PyAudio()
        stream = pa.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=frame_size)
        try:
            while not self._stop_event.is_set():
                if time.time() - self._last_detection < self.cooldown:
                    time.sleep(0.05)
                    continue
                chunk = stream.read(frame_size, exception_on_overflow=False)
                if not chunk:
                    continue
                audio = np.frombuffer(chunk, dtype=np.int16)
                scores = model.predict(audio)
                score_hits = []
                for name, score in scores.items():
                    if score >= self.threshold:
                        score_hits.append(str(name))
                if score_hits:
                    self._last_detection = time.time()
                    if self._callback is not None:
                        try:
                            self._callback()
                        except Exception:
                            logger.exception("wake-word callback failed")
                    self._active = False
                    break
        finally:
            try:
                stream.stop_stream(); stream.close(); pa.terminate()
            except Exception:
                pass

    def detect(self, audio_chunk: Any | None = None) -> bool:
        if not self.online:
            raise ProviderError("local wake word provider unavailable")
        if not self._supports_phrase():
            raise ProviderError("wake word model unavailable for the configured phrase")
        return super().detect(audio_chunk)


class SpeechToTextProvider(BaseProvider):
    name = "stt"
    capabilities = ("speech_to_text",)

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.language = str(self.config.get("language") or self.config.get("stt_language") or "pt-PT")

    def transcribe(self, audio_bytes: bytes) -> VoiceResult:
        raise NotImplementedError


class LocalSTTProvider(SpeechToTextProvider):
    name = "local_stt"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self._model = None
        self.online = importlib.util.find_spec("faster_whisper") is not None

    def transcribe(self, audio_bytes: bytes) -> VoiceResult:
        if not self.online:
            return VoiceResult(
                text="",
                provider=self.name,
                language=self.language,
                ok=False,
                error="STT not available locally",
            )
        try:
            from faster_whisper import WhisperModel

            if self._model is None:
                self._model = WhisperModel(
                    self.config.get("model", "tiny"),
                    device=self.config.get("device", "cpu"),
                    compute_type=self.config.get("compute_type", "int8"),
                )

            with io.NamedTemporaryFile(suffix=".wav", delete=False) as fp:
                fp.write(audio_bytes)
                tmp_path = fp.name
            try:
                segments, _info = self._model.transcribe(tmp_path, language=self.language.split("-")[0], vad_filter=True)
                text = " ".join(part.text.strip() for part in segments if part.text and part.text.strip()).strip()
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            if not text:
                return VoiceResult(text="", provider=self.name, language=self.language, ok=False, error="empty transcription")
            return VoiceResult(text=text, provider=self.name, language=self.language, duration=0.0, confidence=None, ok=True)
        except Exception as exc:
            logger.warning("local STT failed: %s", exc)
            return VoiceResult(text="", provider=self.name, language=self.language, ok=False, error=str(exc))


class TextToSpeechProvider(BaseProvider):
    name = "tts"
    capabilities = ("text_to_speech",)

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.voice_name = str(self.config.get("voice") or self.config.get("voice_name") or "pt-PT-DuarteNeural")
        self.speed = str(self.config.get("speed") or "+0%")

    async def speak(self, text: str) -> bool:
        raise NotImplementedError

    def stop(self) -> None:
        pass


class LocalTTSProvider(TextToSpeechProvider):
    name = "local_tts"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.online = importlib.util.find_spec("edge_tts") is not None

    async def speak(self, text: str) -> bool:
        if not text or not text.strip():
            return False
        if not self.online:
            raise ProviderError("local TTS provider unavailable")
        try:
            import edge_tts

            communicate = edge_tts.Communicate(text, self.voice_name, rate=self.speed, volume=self.config.get("volume", "+0%"))
            temp_fp = os.path.join(os.getcwd(), "nano_tts_tmp.mp3")
            await communicate.save(temp_fp)
            try:
                import pygame

                pygame.mixer.init()
                pygame.mixer.music.load(temp_fp)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    await asyncio.sleep(0.1)
            finally:
                try:
                    os.unlink(temp_fp)
                except OSError:
                    pass
            return True
        except Exception as exc:  # pragma: no cover - required for graceful fallback
            logger.warning("local TTS failed: %s", exc)
            raise ProviderError(str(exc)) from exc

    def stop(self) -> None:
        try:
            import pygame

            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass


class AudioInputProvider(BaseProvider):
    name = "audio_input"
    capabilities = ("microphone",)

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.sample_rate = int(self.config.get("sample_rate", 16000))
        self.channels = int(self.config.get("channels", 1))
        self.device_index = self.config.get("device_index")
        self._available = importlib.util.find_spec("pyaudio") is not None

    def list_devices(self) -> list[dict[str, Any]]:
        if not self._available:
            return []
        try:
            import pyaudio

            pa = pyaudio.PyAudio()
            devices: list[dict[str, Any]] = []
            for idx in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(idx)
                devices.append({"index": idx, "name": info.get("name", f"device-{idx}"), "maxInputChannels": info.get("maxInputChannels", 0)})
            pa.terminate()
            return devices
        except Exception:
            return []

    def capture(self, duration_seconds: int = 5, sample_rate: int | None = None) -> bytes | None:
        if not self._available:
            raise ProviderError("microphone unavailable")
        try:
            import pyaudio

            rate = sample_rate or self.sample_rate
            audio = pyaudio.PyAudio()
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=1024,
            )
            frames = []
            for _ in range(int(rate / 1024 * duration_seconds)):
                frames.append(stream.read(1024, exception_on_overflow=False))
            stream.stop_stream(); stream.close(); audio.terminate()
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                wav.setnchannels(self.channels)
                wav.setsampwidth(2)
                wav.setframerate(rate)
                wav.writeframes(b"".join(frames))
            return buffer.getvalue()
        except Exception as exc:  # pragma: no cover - depends on host environment
            logger.warning("microphone capture failed: %s", exc)
            raise ProviderError(str(exc)) from exc


class AudioOutputProvider(BaseProvider):
    name = "audio_output"
    capabilities = ("speaker",)

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    async def play(self, audio_bytes: bytes) -> bool:
        if not audio_bytes:
            return False
        try:
            import pygame

            temp_path = os.path.join(os.getcwd(), "nano_audio_output.wav")
            with open(temp_path, "wb") as handle:
                handle.write(audio_bytes)
            pygame.mixer.init(frequency=16000, size=-16, channels=1)
            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)
            os.unlink(temp_path)
            return True
        except Exception as exc:
            logger.warning("audio output failed: %s", exc)
            raise ProviderError(str(exc)) from exc


class VoiceSession:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.config = cfg
        self.state = VoiceSessionState.IDLE
        self.session_id = f"voice-{int(time.time() * 1000)}"
        self.session_timeout_seconds = float(cfg.get("session_timeout_seconds", 30.0))
        self.cooldown = float(cfg.get("cooldown_seconds", 2.0))
        self.last_state_change = time.time()
        self._triggered_by_wake_word = False

    def transition(self, new_state: VoiceSessionState) -> VoiceSessionState:
        self.state = new_state
        self.last_state_change = time.time()
        logger.info("voice session state: %s -> %s", self.state, new_state)
        return self.state

    def start(self) -> VoiceSessionState:
        return self.transition(VoiceSessionState.WAITING_WAKEWORD)

    def waiting_for_wake_word(self) -> VoiceSessionState:
        return self.transition(VoiceSessionState.WAITING_WAKEWORD)

    def start_listening(self) -> VoiceSessionState:
        return self.transition(VoiceSessionState.LISTENING)

    def transcribing(self) -> VoiceSessionState:
        return self.transition(VoiceSessionState.TRANSCRIBING)

    def thinking(self) -> VoiceSessionState:
        return self.transition(VoiceSessionState.THINKING)

    def executing(self) -> VoiceSessionState:
        return self.transition(VoiceSessionState.EXECUTING)

    def speaking(self) -> VoiceSessionState:
        return self.transition(VoiceSessionState.SPEAKING)

    def cancel(self) -> VoiceSessionState:
        self._triggered_by_wake_word = False
        return self.transition(VoiceSessionState.IDLE)

    def error(self, message: str | None = None) -> VoiceSessionState:
        logger.error("voice session error: %s", message or "unknown")
        return self.transition(VoiceSessionState.ERROR)

    def is_expired(self) -> bool:
        return (time.time() - self.last_state_change) > self.session_timeout_seconds

    def status(self) -> dict:
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "idle": self.state == VoiceSessionState.IDLE,
            "last_state_change": self.last_state_change,
            "timeout_seconds": self.session_timeout_seconds,
            "cooldown_seconds": self.cooldown,
        }


class VoiceEngine:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled", False))
        self.cloud_audio = bool(self.config.get("cloud_audio", False))
        self.local_first = bool(self.config.get("local_first", True))
        self.input_provider = AudioInputProvider(self.config.get("microphone", {}))
        self.stt_provider = LocalSTTProvider(self.config.get("stt", {}))
        self.tts_provider = LocalTTSProvider(self.config.get("tts", {}))
        self.wake_word_provider = LocalWakeWordProvider(self.config.get("wake_word", {}))
        self.output_provider = AudioOutputProvider(self.config.get("audio_output", {}))
        self.session = VoiceSession(self.config)
        self._lock = threading.Lock()
        self._wake_callback: Callable[[], None] | None = None

    @property
    def microphone_status(self) -> dict:
        return {
            "enabled": self.enabled,
            "available": self.input_provider.list_devices() != [],
            "selected": self.config.get("microphone", {}).get("device_index"),
            "sample_rate": self.input_provider.sample_rate,
        }

    def status(self) -> dict:
        return {
            "voice": self.enabled,
            "cloud_audio": self.cloud_audio,
            "local_first": self.local_first,
            "microphone": self.microphone_status,
            "session": self.session.status(),
            "wake_word": self.wake_word_provider.status(),
            "stt": self.stt_provider.status(),
            "tts": self.tts_provider.status(),
        }

    def start_wake_word(self, callback: Callable[[], None] | None = None) -> bool:
        if not self.enabled:
            logger.info("voice disabled; wake word not started")
            return False
        self.session.start()
        try:
            self.wake_word_provider.enabled = True
            if callback is not None:
                self._wake_callback = callback
                self.wake_word_provider._callback = callback
            self.wake_word_provider.start(callback)
            return True
        except Exception as exc:  # pragma: no cover - graceful fail path
            logger.warning("could not start wake word: %s", exc)
            self.session.error(str(exc))
            return False

    def stop(self) -> None:
        self.session.cancel()
        try:
            self.tts_provider.stop()
        except Exception:
            pass
        try:
            self.wake_word_provider.stop()
        except Exception:
            pass

    async def listen(self, duration_seconds: int | None = None) -> str | None:
        if not self.enabled:
            return None
        self.session.start_listening()
        try:
            audio = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.input_provider.capture(duration_seconds or int(self.config.get("listen_seconds", 5))),
            )
            if audio is None:
                return None
            self.session.transcribing()
            result = self.stt_provider.transcribe(audio)
            if result.ok:
                return result.text
            logger.warning("voice transcription failed: %s", result.error)
            return None
        except ProviderError as exc:
            logger.warning("voice listen failed: %s", exc)
            self.session.error(str(exc))
            return None
        except Exception as exc:  # pragma: no cover - host-specific faults
            logger.exception("unexpected voice listen failure: %s", exc)
            self.session.error(str(exc))
            return None

    async def speak(self, text: str) -> bool:
        if not self.enabled or not text or not text.strip():
            return False
        self.session.speaking()
        try:
            return await self.tts_provider.speak(text)
        except ProviderError as exc:
            logger.warning("voice TTS failed: %s", exc)
            self.session.error(str(exc))
            return False

    async def stream(self, text: str) -> bool:
        return await self.speak(text)

    def build_request(self, transcript: str, *, intent: str | None = None, privacy_level: str = "normal") -> dict:
        return {
            "text": transcript,
            "intent": intent or "voice",
            "source": "voice",
            "privacy_level": privacy_level,
            "requires_permission": False,
            "task_type": "CHAT",
        }


class VoiceRuntime:
    def __init__(self, voice_engine: VoiceEngine, *, brain=None, orchestrator=None, task_engine=None, permission_manager=None, event_bus=None, config: dict | None = None):
        self.voice = voice_engine
        self.brain = brain
        self.orchestrator = orchestrator
        self.task_engine = task_engine
        self.permission_manager = permission_manager
        self.event_bus = event_bus
        self.config = config or {}
        self.session = self.voice.session
        self._last_session_id = self.session.session_id

    def status(self) -> dict:
        return {
            "enabled": self.voice.enabled,
            "session": self.session.status(),
            "provider": self.voice.status(),
            "ready": bool(self.voice.enabled and self.voice.stt_provider and self.voice.tts_provider),
        }

    def _publish(self, event: str, payload: dict | None = None) -> None:
        if self.event_bus is not None:
            try:
                self.event_bus.publish(event, payload or {})
            except Exception:
                logger.debug("voice event publish failed: %s", event, exc_info=True)

    def _normalize_request(self, transcript: str, *, session_context: dict | None = None) -> dict:
        text = (transcript or "").strip()
        privacy = "strict_local" if "apagar" in text.lower() or "eliminar" in text.lower() or "reset" in text.lower() else "normal"
        return {
            "text": text,
            "source": "voice",
            "session_id": self.session.session_id,
            "privacy_level": privacy,
            "task_type": "CHAT",
            "requires_permission": self._requires_permission(text),
            "context": session_context or {},
        }

    def _requires_permission(self, text: str) -> bool:
        lowered = (text or "").lower()
        return any(keyword in lowered for keyword in ("apaga", "apagar", "eliminar", "deletar", "reset", "instala", "enviar", "submeter", "executa"))

    def _is_quick_command(self, text: str) -> bool:
        lowered = (text or "").lower()
        if not lowered:
            return True
        slow_keywords = ["analisa", "investiga", "corrige", "desenvolve", "cria", "melhora", "revisa", "configura", "planeia", "procura", "pesquisa"]
        if any(keyword in lowered for keyword in slow_keywords):
            return False
        quick_keywords = ["que horas", "horas são", "data", "como estás", "estado", "olá", "bom dia", "boa tarde", "boa noite", "abri", "abre o", "fecha", "lista", "mostra"]
        return True if any(keyword in lowered for keyword in quick_keywords) else True

    async def listen_once(self, *, duration_seconds: int | None = None) -> str | None:
        self._publish("VoiceSessionStarted", {"session_id": self.session.session_id})
        self.session.start()
        transcript = await self.voice.listen(duration_seconds=duration_seconds)
        if not transcript:
            self._publish("VoiceError", {"session_id": self.session.session_id, "error": "stt_failed"})
            return None
        self._publish("TranscriptionCompleted", {"session_id": self.session.session_id, "text_length": len(transcript.strip())})
        return transcript.strip()

    async def process_audio(self, *, duration_seconds: int | None = None) -> dict:
        transcript = await self.listen_once(duration_seconds=duration_seconds)
        if not transcript:
            return {"ok": False, "error": "no_transcript"}
        return await self.process_request(transcript)

    async def _direct_chat(self, text: str) -> str:
        if self.brain is None:
            return "O Nano não está disponível no momento."
        chunks: list[str] = []
        async for token in self.brain.chat(text, stream=True):
            if token.startswith("_thinking_:"):
                continue
            chunks.append(token)
        return "".join(chunks).strip() or "Não consegui responder a esse pedido no momento."

    async def _create_task(self, text: str) -> dict:
        if self.orchestrator is None:
            return {"ok": False, "error": "orchestrator_unavailable"}
        result = self.orchestrator.handle_request(text, metadata={"source": "voice", "session_id": self.session.session_id})
        if result.get("ok"):
            self._publish("VoiceTaskCreated", {"task_id": result.get("task_id"), "session_id": self.session.session_id})
        return result

    async def process_request(self, transcript: str, *, session_context: dict | None = None) -> dict:
        normalized = self._normalize_request(transcript, session_context=session_context)
        if not normalized["text"]:
            return {"ok": False, "error": "empty_request"}
        self._publish("VoiceRequestCreated", {"session_id": self.session.session_id, "text": normalized["text"]})
        if self.permission_manager and self._requires_permission(normalized["text"]):
            action = "voice.command"
            request_id = self.permission_manager.request_permission(
                action,
                {"text": normalized["text"]},
                task_id=self.session.session_id,
                reason="Voice command requires explicit user approval before execution.",
                target="voice-command",
                agent="voice",
                tool="voice",
            )
            self._publish("VoicePermissionRequested", {"session_id": self.session.session_id, "request_id": request_id})
            return {
                "ok": True,
                "requires_permission": True,
                "request_id": request_id,
                "response": "Preciso da tua autorização para executar esse comando.",
                "task": None,
            }

        if self._is_quick_command(normalized["text"]):
            self.session.thinking()
            response = await self._direct_chat(normalized["text"])
            self.session.cancel()
            return {"ok": True, "mode": "quick", "response": response, "task": None}

        self.session.thinking()
        task_result = await self._create_task(normalized["text"])
        if not task_result.get("ok"):
            self.session.error("task creation failed")
            return {"ok": False, "error": "task_creation_failed", "details": task_result}
        brief = "Vou tratar disso. Aviso-te quando terminar."
        self.session.cancel()
        return {"ok": True, "mode": "task", "response": brief, "task": task_result}

    async def speak_response(self, text: str) -> bool:
        if not text or not text.strip():
            return False
        self.session.speaking()
        try:
            if self.voice.enabled:
                return await self.voice.speak(text)
            return False
        except Exception as exc:
            self._publish("VoiceError", {"session_id": self.session.session_id, "error": str(exc)})
            logger.warning("voice response failed: %s", exc)
            return False
        finally:
            self.session.cancel()

    async def process_wake_word_turn(self, *, duration_seconds: int | None = None) -> dict:
        self._publish("WakeWordDetected", {"session_id": self.session.session_id})
        self.session.waiting_for_wake_word()
        if not self.voice.enabled:
            self._publish("VoiceError", {"session_id": self.session.session_id, "error": "voice_disabled"})
            return {"ok": False, "error": "voice_disabled"}
        self.session.start_listening()
        transcript = await self.listen_once(duration_seconds=duration_seconds)
        if not transcript:
            return {"ok": False, "error": "transcription_failed"}
        return await self.process_request(transcript)

    async def handle_wake_word(self, *, duration_seconds: int | None = None) -> dict:
        return await self.process_wake_word_turn(duration_seconds=duration_seconds)

    async def process_manual(self, transcript: str, *, speak: bool = False, session_context: dict | None = None) -> dict:
        result = await self.process_request(transcript, session_context=session_context)
        if speak and result.get("response"):
            await self.speak_response(str(result.get("response")))
        return result


def _clean_for_tts(text: str) -> str:
    clean = text.replace("```", " ")
    clean = clean.replace("`", " ")
    clean = clean.replace("**", " ")
    clean = clean.replace("*", " ")
    clean = " ".join(clean.split())
    return clean[:500]


__all__ = [
    "AudioInputProvider",
    "AudioOutputProvider",
    "LocalSTTProvider",
    "LocalTTSProvider",
    "LocalWakeWordProvider",
    "SpeechToTextProvider",
    "TextToSpeechProvider",
    "VoiceEngine",
    "VoiceRuntime",
    "VoiceResult",
    "VoiceSession",
    "VoiceSessionState",
    "WakeWordProvider",
    "ProviderError",
    "_clean_for_tts",
]
