from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import os
import tempfile
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable

from core import speech_filter
from core.wake_word import WakeWordEngine
from core.wake_phrase import WakePhraseEngine, WakePhraseState

logger = logging.getLogger("nano.voice")

# PortAudio (behind PyAudio) is NOT safe to initialise and terminate
# concurrently from multiple threads. Two call sites do exactly that: the wake
# phrase thread captures audio every few seconds, while the UI readiness poll
# enumerates devices every few seconds from the web-server thread. Overlapping
# them crashed the whole process with an access violation (0xC0000005) about
# 20 seconds after the UI connected. Every PyAudio construction/teardown in
# this process must be serialised through this lock.
_PORTAUDIO_LOCK = threading.RLock()

# Device enumeration is stable in practice, so it is cached: the readiness poll
# then costs nothing and does not have to touch PortAudio at all.
_DEVICE_CACHE_TTL_SECONDS = 30.0

# Set while a long-lived capture stream is open. PyAudio.terminate() tears down
# PortAudio process-wide, so no other component may construct or destroy a
# PyAudio instance while a stream is live -- that is exactly the access
# violation (0xC0000005) this module already had once. Everything that would
# otherwise enumerate devices checks this flag and serves cached data instead.
_MIC_STREAM_OPEN = threading.Event()


def microphone_busy() -> bool:
    """True while Nano holds its single long-lived capture stream open."""
    return _MIC_STREAM_OPEN.is_set()


class ProviderError(RuntimeError):
    pass


class VoiceReadiness(str, Enum):
    """Distinguishes architecture-ready from live-ready.

    Reporting READY because a provider object exists told the user the voice
    stack worked when the runtime packages, models or microphone were absent.
    Each state below corresponds to something actually checked.
    """

    DISABLED = "DISABLED"
    SETUP_REQUIRED = "SETUP_REQUIRED"
    MODEL_MISSING = "MODEL_MISSING"
    PROVIDER_READY = "PROVIDER_READY"
    READY = "READY"
    ERROR = "ERROR"


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
        self._callback: Callable[[], None] | None = None
        self._engine = WakeWordEngine(cfg, self._dispatch_wake)
        self.online = importlib.util.find_spec("openwakeword") is not None or importlib.util.find_spec("pvporcupine") is not None
        self._error: str | None = None
        self._active = False

    def _dispatch_wake(self) -> None:
        if self._callback is None:
            return
        self._callback()

    def start(self, callback: Callable[[], None] | None = None) -> bool:
        if callback is not None:
            self._callback = callback
        if not self.online:
            self._error = "wake word runtime not installed"
            raise ProviderError(self._error)
        started = self._engine.start()
        self._active = bool(started)
        self.enabled = bool(self._engine.enabled)
        self._error = self._engine.last_error
        return started

    def stop(self) -> None:
        self._engine.stop()
        self._active = False

    def pause(self) -> None:
        self._engine.pause()

    def resume(self) -> None:
        self._engine.resume()

    def status(self) -> dict:
        data = self._engine.status()
        model_status = str(data.get("model_status") or "")
        if not self.enabled:
            health = "DISABLED"
        elif not self.online:
            health = "OFFLINE"
        elif model_status == "READY":
            health = "READY"
        else:
            health = "SETUP REQUIRED"
        data.update({
            "name": self.name,
            "local": True,
            "online": self.online,
            "health": health,
            "active": self._active,
            "error": self._error or data.get("error"),
        })
        return data


class LocalWakeWordProvider(WakeWordProvider):
    name = "local_wake_word"


class WakePhraseProvider(BaseProvider):
    """Wraps WakePhraseEngine ("Hey Nano") the same way WakeWordProvider wraps
    the ONNX/Porcupine engine: this is a second, independent local wake path
    that needs no trained model, built on the project's existing local STT.
    """

    name = "wake_phrase"
    capabilities = ("wake_phrase",)

    def __init__(self, config: dict | None, *, audio_provider: "AudioInputProvider", stt_provider: "LocalSTTProvider"):
        cfg = config or {}
        self.config = cfg
        self._callback: Callable[[str], None] | None = None
        self._engine = WakePhraseEngine(cfg, self._dispatch_wake, audio_provider=audio_provider, stt_provider=stt_provider)
        self.enabled = self._engine.enabled
        self.online = True

    def _dispatch_wake(self, transcript: str) -> None:
        if self._callback is None:
            return
        self._callback(transcript)

    def start(self, callback: Callable[[str], None] | None = None) -> bool:
        if callback is not None:
            self._callback = callback
        started = self._engine.start()
        self.enabled = self._engine.enabled
        return started

    def stop(self) -> None:
        self._engine.stop()

    def pause(self) -> None:
        self._engine.pause()

    def pause_and_wait(self, timeout: float = 5.0) -> bool:
        """Pause and block until the engine is genuinely off the microphone."""
        return self._engine.pause_and_wait(timeout)

    def resume(self) -> None:
        self._engine.resume()

    @property
    def running(self) -> bool:
        return self._engine.running

    def set_state(self, state: WakePhraseState) -> None:
        self._engine.set_state(state)

    def mark_command_listening(self) -> None:
        self._engine.mark_command_listening()

    def mark_processing(self) -> None:
        self._engine.mark_processing()

    def mark_idle(self) -> None:
        self._engine.mark_idle()

    def status(self) -> dict:
        data = self._engine.status()
        data.update({"name": self.name, "local": True, "online": True})
        return data


class SpeechToTextProvider(BaseProvider):
    name = "stt"
    capabilities = ("speech_to_text",)

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.language = str(self.config.get("language") or self.config.get("stt_language") or "pt-PT")

    def transcribe(self, audio_bytes: bytes) -> VoiceResult:
        raise NotImplementedError


class LocalSTTProvider(SpeechToTextProvider):
    """faster-whisper, loaded ONCE per process and reused for every turn.

    THE DECODING SETTINGS HERE ARE BENCHMARK OUTPUT, NOT PREFERENCES.

    Thirty recordings of the user's own voice, on this microphone, replayed
    through every candidate over byte-identical WAVs (see
    docs/architecture/SPEECH_ACCURACY.md):

        tiny  / cpu / int8            WER 67.3%   entities  4/14
        base  / cpu / int8            WER 61.0%   entities  4/14
        small / cpu / int8            WER 32.7%   entities  6/14
        small / cpu / int8 + vocab    WER 27.0%   entities 13/14

    Two consequences worth defending against future "tidying":

    * The language is FORCED. Auto-detection was measured and is markedly
      worse; it must never be reintroduced.
    * ``initial_prompt`` is what took critical entities from 6/14 to 13/14.
      It must stay SHORT -- it is decoder context, so a long prompt costs
      tokens and drags the transcript towards the prompt's own wording.

    MODEL LIFETIME. The model is built lazily on the first transcription and
    cached on the instance for the life of the process. It is NOT rebuilt per
    voice turn: ``small`` costs ~1.3 s to construct, which would be added to
    every single Ctrl+Shift+Space. One instance is shared with the wake-phrase
    engine, so there is never a second copy in RAM.
    """

    name = "local_stt"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self._model = None
        self.online = importlib.util.find_spec("faster_whisper") is not None

    @property
    def model_name(self) -> str:
        return str(self.config.get("model") or "small")

    @property
    def device(self) -> str:
        return str(self.config.get("device") or "cpu")

    @property
    def compute_type(self) -> str:
        return str(self.config.get("compute_type") or "int8")

    @property
    def decode_language(self) -> str:
        """The language code actually handed to faster-whisper.

        Accepts both "pt" and the older "pt-PT" spelling, and NEVER returns an
        empty string: an empty language means auto-detection, which the
        benchmark measured as substantially worse.
        """
        return (self.language.split("-")[0] or "pt")

    @property
    def vocabulary_hint(self) -> str | None:
        """The short initial_prompt, or None when it is switched off."""
        if not self.config.get("vocabulary_hint_enabled", True):
            return None
        hint = str(self.config.get("vocabulary_hint") or "").strip()
        return hint or None

    @property
    def model_loaded(self) -> bool:
        return self._model is not None

    def describe(self) -> dict:
        """What this provider WILL do, without loading anything."""
        return {
            "model": self.model_name,
            "device": self.device,
            "compute_type": self.compute_type,
            "language": self.decode_language,
            "vocabulary_hint_enabled": self.vocabulary_hint is not None,
            "vocabulary_hint_chars": len(self.vocabulary_hint or ""),
            "model_loaded": self.model_loaded,
        }

    def _ensure_model(self):
        """Build the model once, and record exactly what was built.

        The log line is the thing that proves production is running what the
        benchmark chose. Without it, a silent fallback to `tiny` -- a stale
        config, a bad merge, an old settings.yaml -- would be invisible until
        somebody noticed the transcripts had got worse again. It contains no
        audio, no transcript and no secret.
        """
        if self._model is not None:
            return self._model

        from faster_whisper import WhisperModel

        started = time.perf_counter()
        self._model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
        )
        hint = self.vocabulary_hint
        logger.info(
            "STT model loaded: model=%s device=%s compute_type=%s language=%s "
            "vocabulary_hint_enabled=%s vocabulary_hint_chars=%d load_seconds=%.2f",
            self.model_name, self.device, self.compute_type, self.decode_language,
            hint is not None, len(hint or ""), time.perf_counter() - started,
        )
        return self._model

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
            model = self._ensure_model()

            options: dict[str, Any] = {
                "language": self.decode_language,
                "vad_filter": True,
            }
            hint = self.vocabulary_hint
            if hint:
                options["initial_prompt"] = hint

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fp:
                fp.write(audio_bytes)
                tmp_path = fp.name
            try:
                started = time.perf_counter()
                segments, _info = model.transcribe(tmp_path, **options)
                text = " ".join(part.text.strip() for part in segments if part.text and part.text.strip()).strip()
                elapsed = time.perf_counter() - started
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            # Lengths and timings only -- never the transcript itself, which is
            # the user's private speech.
            logger.info("STT transcribe: model=%s %.2fs -> %d chars",
                        self.model_name, elapsed, len(text))
            if not text:
                return VoiceResult(text="", provider=self.name, language=self.language, ok=False, error="empty transcription")
            return VoiceResult(text=text, provider=self.name, language=self.language, duration=elapsed, confidence=None, ok=True)
        except Exception as exc:
            logger.warning("local STT failed: %s", exc)
            return VoiceResult(text="", provider=self.name, language=self.language, ok=False, error=str(exc))

    def status(self) -> dict:
        data = super().status()
        data.update(self.describe())
        return data


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


def _new_scratch_audio_path(suffix: str) -> str:
    """A UNIQUE file, in the OS temp directory, for exactly one utterance.

    Both audio paths used to write to one fixed name in os.getcwd() --
    "nano_tts_tmp.mp3" and "nano_audio_output.wav", in the project root. That
    is half of why Nano spoke exactly once per launch; see _play_audio_file for
    the other half. A per-utterance name means that even if a handle is somehow
    still held, the next utterance is not blocked by it, and scratch audio
    stops appearing as stray files in the repository.
    """
    handle, path = tempfile.mkstemp(prefix="nano-audio-", suffix=suffix)
    os.close(handle)
    return path


async def _play_audio_file(path: str, **init_kwargs: Any) -> None:
    """Play a file through the pygame mixer and RELEASE IT afterwards.

    THE BUG THIS FIXES, precisely:

    pygame.mixer.music.load() keeps the file open for as long as it is the
    loaded track. Nothing ever called unload(), so on Windows the file stayed
    locked after playback. The cleanup `os.unlink` then raised PermissionError
    into an `except OSError: pass`, which discarded it silently, and the NEXT
    synthesis tried to write the same locked path and died with
    "[Errno 13] Permission denied: nano_tts_tmp.mp3".

    The visible result was that Nano spoke on the first voice turn of a process
    and was mute on every one after it -- while still emitting SPEAKING and
    still showing the answer in the chat, because the failure happened inside
    TTS, milliseconds after the phase event, far too fast to see.
    """
    import pygame

    if not pygame.mixer.get_init():
        pygame.mixer.init(**init_kwargs)
    pygame.mixer.music.load(path)
    try:
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            await asyncio.sleep(0.1)
    finally:
        # Non-negotiable, and it must run even if playback raised: this is the
        # call whose absence made Nano a one-utterance application.
        try:
            pygame.mixer.music.unload()
        except Exception:
            # Very old pygame has no unload(); stopping at least frees the
            # device, and the unique filename keeps the next turn working.
            try:
                pygame.mixer.music.stop()
            except Exception:
                logger.debug("could not release the mixer", exc_info=True)


def _discard_scratch_audio(path: str) -> None:
    """Delete one utterance's scratch file, and SAY SO if it cannot be."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        # Never silent again. A file that cannot be removed means something
        # still holds it, which is exactly the condition that broke TTS.
        logger.warning("Ficheiro de audio temporario nao removido (%s): %s", path, exc)


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
            temp_fp = _new_scratch_audio_path(".mp3")
            try:
                await communicate.save(temp_fp)
                await _play_audio_file(temp_fp)
            finally:
                _discard_scratch_audio(temp_fp)
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

    # Shared across instances: the device list is a property of the machine,
    # and caching it class-wide keeps the readiness poll off PortAudio.
    _device_cache: list[dict[str, Any]] | None = None
    _device_cache_at: float = 0.0

    _FRAMES_PER_BUFFER = 1024

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.sample_rate = int(self.config.get("sample_rate", 16000))
        self.channels = int(self.config.get("channels", 1))
        self.device_index = self.config.get("device_index")
        self._available = importlib.util.find_spec("pyaudio") is not None

        # THE ONE SPEECH GATE FOR THIS MICROPHONE.
        #
        # It lives here, on the single audio-input authority, because every
        # trigger records through this provider and so every trigger must judge
        # speech by the same number. It used to belong to WakePhraseEngine, and
        # that ownership was the bug: with the wake detector switched off
        # nothing calibrated a gate, so the hotkey and UI turns fell back to
        # speech_filter's fixed 220 RMS floor while Settings -> Test Microphone
        # measured against this gate's 12. A real voice at RMS 124 therefore
        # passed the test button and was rejected by the hotkey, on the same
        # microphone, seconds apart.
        #
        # Uncalibrated it already yields a sane floor (min_threshold), and it
        # keeps learning from every quiet chunk any path feeds it, so the more
        # Nano listens the better calibrated it gets -- regardless of which
        # trigger did the listening.
        self.gate = speech_filter.AdaptiveGate()
        # The single long-lived stream, when one is open.
        self._pyaudio: Any = None
        self._stream: Any = None
        self._stream_rate: int | None = None

    def list_devices(self) -> list[dict[str, Any]]:
        """Enumerate input devices, cached and serialised against capture().

        Called by the UI readiness poll every few seconds. It must never open
        PortAudio while the wake-phrase thread is capturing, and it must never
        block that thread, so it serves a cache and skips the refresh entirely
        if the lock is busy.
        """
        if not self._available:
            return []

        now = time.monotonic()
        cached = type(self)._device_cache
        if cached is not None and (now - type(self)._device_cache_at) < _DEVICE_CACHE_TTL_SECONDS:
            return cached

        # Constructing a second PyAudio while the persistent stream is live and
        # then terminating it would tear PortAudio down under the running
        # stream, so the cache is served instead. Never refresh here.
        if _MIC_STREAM_OPEN.is_set():
            return cached if cached is not None else []

        # Non-blocking: if a capture holds PortAudio, serve the previous list
        # rather than waiting (or worse, racing it).
        if not _PORTAUDIO_LOCK.acquire(blocking=False):
            return cached if cached is not None else []
        try:
            import pyaudio

            pa = pyaudio.PyAudio()
            try:
                devices: list[dict[str, Any]] = []
                for idx in range(pa.get_device_count()):
                    info = pa.get_device_info_by_index(idx)
                    devices.append({
                        "index": idx,
                        "name": info.get("name", f"device-{idx}"),
                        "maxInputChannels": info.get("maxInputChannels", 0),
                    })
            finally:
                pa.terminate()
            type(self)._device_cache = devices
            type(self)._device_cache_at = now
            return devices
        except Exception as exc:
            logger.debug("device enumeration failed: %s", exc)
            return cached if cached is not None else []
        finally:
            _PORTAUDIO_LOCK.release()

    # ------------------------------------------------------- persistent stream

    def open_stream(self, sample_rate: int | None = None) -> bool:
        """Open the single long-lived capture stream. Idempotent.

        Reopening PyAudio for every 2.5 s chunk cost 78-125 ms of measured dead
        time per iteration and, worse, guaranteed a hard boundary every chunk
        where "Hey Nano" could be cut in half. One stream plus a rolling buffer
        removes both problems.
        """
        if not self._available:
            raise ProviderError("microphone unavailable")
        with _PORTAUDIO_LOCK:
            if self._stream is not None:
                return True
            try:
                import pyaudio

                rate = sample_rate or self.sample_rate
                self._pyaudio = pyaudio.PyAudio()
                self._stream = self._pyaudio.open(
                    format=pyaudio.paInt16,
                    channels=self.channels,
                    rate=rate,
                    input=True,
                    input_device_index=self.device_index,
                    frames_per_buffer=self._FRAMES_PER_BUFFER,
                )
                self._stream_rate = rate
                _MIC_STREAM_OPEN.set()
                logger.info("[Mic] persistent stream open (rate=%d device=%s)",
                            rate, self.device_index)
                return True
            except Exception as exc:
                self._teardown_stream_locked()
                logger.warning("could not open persistent microphone stream: %s", exc)
                raise ProviderError(str(exc)) from exc

    def close_stream(self) -> None:
        with _PORTAUDIO_LOCK:
            self._teardown_stream_locked()

    def _teardown_stream_locked(self) -> None:
        """Caller must hold _PORTAUDIO_LOCK."""
        _MIC_STREAM_OPEN.clear()
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
            self._pyaudio = None

    @property
    def stream_open(self) -> bool:
        return self._stream is not None

    def read_stream(self, duration_seconds: float) -> bytes | None:
        """Read a bounded slice from the open stream as a WAV payload.

        Reading does not initialise or terminate PortAudio, so it deliberately
        does NOT hold _PORTAUDIO_LOCK: holding it for the whole read would
        block the readiness poll for seconds at a time.
        """
        stream = self._stream
        if stream is None:
            return None
        rate = self._stream_rate or self.sample_rate
        wanted = max(1, int(rate / self._FRAMES_PER_BUFFER * float(duration_seconds)))
        frames: list[bytes] = []
        for _ in range(wanted):
            if self._stream is None:            # closed underneath us
                return None
            try:
                frames.append(stream.read(self._FRAMES_PER_BUFFER, exception_on_overflow=False))
            except Exception as exc:
                logger.warning("[Mic] stream read failed: %s", exc)
                return None
        return self._to_wav(b"".join(frames), rate)

    def _to_wav(self, raw: bytes, rate: int) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(raw)
        return buffer.getvalue()

    def capture(self, duration_seconds: int = 5, sample_rate: int | None = None) -> bytes | None:
        """Record one bounded chunk.

        If the persistent stream is open this reads from it, so a command turn
        never opens a second microphone. Otherwise it falls back to the
        original open/read/close cycle, fully serialised.
        """
        if not self._available:
            raise ProviderError("microphone unavailable")

        if self._stream is not None and (sample_rate is None or sample_rate == self._stream_rate):
            payload = self.read_stream(duration_seconds)
            if payload is not None:
                return payload
            # Stream died mid-read; fall through and reopen the one-shot way.

        # Held for the whole open/read/close cycle: PortAudio cannot tolerate
        # another thread initialising or terminating it while a stream is live.
        with _PORTAUDIO_LOCK:
            audio = None
            stream = None
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
            except Exception as exc:  # pragma: no cover - depends on host environment
                logger.warning("microphone capture failed: %s", exc)
                raise ProviderError(str(exc)) from exc
            finally:
                # Always tear down, even on a partial failure, or the next
                # capture inherits a half-open PortAudio and crashes.
                if stream is not None:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass
                if audio is not None:
                    try:
                        audio.terminate()
                    except Exception:
                        pass

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(b"".join(frames))
        return buffer.getvalue()


class AudioOutputProvider(BaseProvider):
    name = "audio_output"
    capabilities = ("speaker",)

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    async def play(self, audio_bytes: bytes) -> bool:
        if not audio_bytes:
            return False
        try:
            temp_path = _new_scratch_audio_path(".wav")
            try:
                with open(temp_path, "wb") as handle:
                    handle.write(audio_bytes)
                # Same lifecycle as the TTS path, for the same reason: this one
                # also deleted its file OUTSIDE a finally, so any playback error
                # leaked it and locked the next call out.
                await _play_audio_file(temp_path, frequency=16000, size=-16, channels=1)
            finally:
                _discard_scratch_audio(temp_path)
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
        """Move to a new state and log the real transition.

        The previous state was being overwritten before the log call, so every
        line read "X -> X" and the actual sequence was unrecoverable from the
        log. The old value is captured first.
        """
        previous = self.state
        self.state = new_state
        self.last_state_change = time.time()
        if previous != new_state:
            logger.info("voice session state: %s -> %s", previous.value, new_state.value)
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
        # "Hey Nano" wake-phrase detection reuses the same audio/STT providers
        # rather than opening a second microphone/model of its own.
        self.wake_phrase_provider = WakePhraseProvider(
            self.config, audio_provider=self.input_provider, stt_provider=self.stt_provider
        )
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

    def readiness(self) -> tuple[VoiceReadiness, list[str]]:
        """Report what is actually installed and reachable, never what exists as an object."""
        blockers: list[str] = []
        if not self.enabled:
            return VoiceReadiness.DISABLED, ["voice disabled in configuration"]

        if self.session.state == VoiceSessionState.ERROR:
            return VoiceReadiness.ERROR, [str(self.session.status().get("error") or "voice session error")]

        if not getattr(self.input_provider, "_available", False):
            blockers.append("microphone runtime (pyaudio) not installed")
        if not self.stt_provider.online:
            blockers.append("speech-to-text runtime (faster-whisper) not installed")
        if not self.tts_provider.online:
            blockers.append("text-to-speech runtime (edge-tts) not installed")
        if blockers:
            return VoiceReadiness.SETUP_REQUIRED, blockers

        if not self.input_provider.list_devices():
            return VoiceReadiness.PROVIDER_READY, ["no input device detected"]

        # A WAKE PATH IS NOT REQUIRED FOR VOICE TO BE READY.
        #
        # This used to end with: if the ONNX wake-word model is unusable AND
        # the "Ei Nano" phrase detector is not listening, report MODEL_MISSING.
        # That was defensible while a wake phrase was the only way to talk to
        # Nano. It stopped being true the moment Ctrl+Shift+Space became the
        # primary activation, and it did real damage: with the wake phrase off
        # by default, readiness became MODEL_MISSING, and the UI treats
        # anything other than READY as "voice unavailable" -- so the microphone
        # button in the composer was rendered DISABLED and start_voice_turn
        # refused before it ever reached the backend. Manual activation was
        # switched off by the absence of an automatic one.
        #
        # Readiness here answers one question: can Nano hear the user and
        # answer, when the user asks it to? That needs a microphone, a
        # transcriber and a voice -- all checked above. The two wake paths are
        # optional, opt-in extras and report their own state separately in
        # status() under "wake_word" and "wake_phrase", so nothing is hidden by
        # not conflating them with this.
        return VoiceReadiness.READY, []

    def status(self) -> dict:
        readiness, blockers = self.readiness()
        return {
            "voice": self.enabled,
            "cloud_audio": self.cloud_audio,
            "local_first": self.local_first,
            "microphone": self.microphone_status,
            "session": self.session.status(),
            "wake_word": self.wake_word_provider.status(),
            "wake_phrase": self.wake_phrase_provider.status(),
            "stt": self.stt_provider.status(),
            "tts": self.tts_provider.status(),
            "readiness": readiness.value,
            "blockers": blockers,
        }

    def start_wake_word(self, callback: Callable[[], None] | None = None) -> bool:
        if not self.enabled:
            logger.info("voice disabled; wake word not started")
            return False
        self.session.start()
        try:
            if callback is not None:
                self._wake_callback = callback
            started = self.wake_word_provider.start(callback)
            if not started:
                self.session.error(self.wake_word_provider.status().get("error") or "wake word start failed")
            return bool(started)
        except Exception as exc:  # pragma: no cover - graceful fail path
            logger.warning("could not start wake word: %s", exc)
            self.session.error(str(exc))
            return False

    def start_wake_phrase(self, callback: Callable[[str], None] | None = None) -> bool:
        """Start the STT-based "Hey Nano" detector. Independent of start_wake_word."""
        if not self.enabled:
            logger.info("voice disabled; wake phrase not started")
            return False
        try:
            return bool(self.wake_phrase_provider.start(callback))
        except Exception as exc:  # pragma: no cover - graceful fail path
            logger.warning("could not start wake phrase: %s", exc)
            return False

    def stop_playback(self) -> None:
        """Stop what is being spoken RIGHT NOW. Voice stays available.

        This is what the UI's Stop button needs. It used to call stop(), which
        also tears down the wake detectors -- so interrupting a single spoken
        reply silently disabled "Ei Nano" for the rest of the session, and the
        only way back was to toggle the setting off and on. Stopping a sound is
        not the same request as shutting down the subsystem that makes sounds.
        """
        self.session.cancel()
        try:
            self.tts_provider.stop()
        except Exception:
            logger.debug("could not stop TTS playback", exc_info=True)

    def shutdown(self) -> None:
        """Tear down the whole voice subsystem. Only for application exit."""
        self.stop_playback()
        try:
            self.wake_word_provider.stop()
        except Exception:
            pass
        try:
            self.wake_phrase_provider.stop()
        except Exception:
            pass

    def stop(self) -> None:
        """Backwards-compatible alias for the full teardown."""
        self.shutdown()

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
        # How long Nano waits for a command after the wake chime before giving
        # up and returning to listening. Bounded so a stuck turn cannot hold
        # the microphone indefinitely.
        voice_cfg = (self.config.get("voice") or {}) if "voice" in self.config else self.config
        self.command_timeout_seconds = max(3, min(15, int(
            voice_cfg.get("wake_command_timeout_seconds", 7)
        )))

        # --- one voice turn at a time -------------------------------------
        # Every trigger (wake phrase, global hotkey, UI button) funnels through
        # run_voice_turn, and exactly one may hold the microphone. The guard is
        # NON-BLOCKING on purpose: a second trigger must be told "already
        # listening" immediately, not queued behind the first. Queueing would
        # mean the user presses the hotkey, nothing appears to happen, and a
        # turn starts later against a command they are no longer speaking.
        self._turn_lock = threading.Lock()
        self._turn_source: str | None = None
        self._turn_started_at: float | None = None
        self._turn_phase: str = "IDLE"
        # UI notifications, injected by the host so this module never imports
        # eel. Absent hooks are simply not called.
        self._on_phase: Callable[[str, str], None] | None = None
        self._on_exchange: Callable[[str, str, str], None] | None = None
        self._on_activation: Callable[[str], None] | None = None

    # ------------------------------------------------------------- observers

    def set_observer(
        self,
        *,
        on_phase: Callable[[str, str], None] | None = None,
        on_exchange: Callable[[str, str, str], None] | None = None,
        on_activation: Callable[[str], None] | None = None,
    ) -> None:
        """Attach the host's UI notifications to the voice turn.

        Keeps the choreography here and the transport in the host: this module
        stays importable and testable with no eel, no browser and no UI.
        """
        self._on_phase = on_phase
        self._on_exchange = on_exchange
        self._on_activation = on_activation

    def _phase(self, phase: str, detail: str = "") -> None:
        self._turn_phase = phase
        if self._on_phase is None:
            return
        try:
            self._on_phase(phase, detail)
        except Exception:
            logger.debug("voice phase notification failed: %s", phase, exc_info=True)

    def _exchange(self, turn_id: str, user_text: str, assistant_text: str) -> None:
        if self._on_exchange is None:
            return
        try:
            self._on_exchange(turn_id, user_text, assistant_text)
        except Exception:
            logger.debug("voice exchange notification failed", exc_info=True)

    def _activation(self, transcript: str = "") -> None:
        if self._on_activation is None:
            return
        try:
            self._on_activation(transcript)
        except Exception:
            logger.debug("voice activation notification failed", exc_info=True)

    def turn_status(self) -> dict:
        """Whether a voice turn is running, and which trigger started it."""
        active = self._turn_source is not None
        return {
            "active": active,
            "source": self._turn_source,
            "phase": self._turn_phase,
            "elapsed_seconds": (
                round(time.time() - self._turn_started_at, 1)
                if active and self._turn_started_at else None
            ),
        }

    def status(self) -> dict:
        readiness, blockers = self.voice.readiness()
        return {
            "enabled": self.voice.enabled,
            "session": self.session.status(),
            "provider": self.voice.status(),
            "readiness": readiness.value,
            "blockers": blockers,
            # 'ready' used to be `enabled and provider_object and provider_object`,
            # which is true whenever voice is enabled. It now means live-ready.
            "ready": readiness == VoiceReadiness.READY,
        }

    def _command_has_speech(self, audio: bytes) -> bool:
        """Energy gate for a spoken command, calibrated to this microphone.

        ONE GATE, OWNED BY THE MICROPHONE. There is no longer a "calibrated?"
        branch and no static fallback, because that branch is what broke the
        hotkey: it reached for the WAKE DETECTOR's gate, and with the wake
        phrase off by default nothing had ever calibrated one, so every command
        turn fell through to speech_filter's fixed 220 RMS floor.

        Measured on this machine: Settings -> Test Microphone judged the user's
        voice against 12 and reported "Voz detetada (nivel 124)", while the
        hotkey judged the same voice against 220 and answered "nao ouvi nada".
        Same microphone, same person, eighteen times the bar.

        No threshold was changed to fix it. The command path simply asks the
        same gate the test button asks.
        """
        return self.voice.input_provider.gate.has_speech(audio)

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
            # Every control token, not just "_thinking_:". This path feeds TTS,
            # and it used to READ THE RATE-LIMIT JSON ALOUD because it filtered
            # one sentinel by name instead of the whole class of them.
            if token.startswith("_") and ":" in token.split("\n", 1)[0][:24]:
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
            # The transcript travels with the result so the UI can show what
            # was actually said rather than the wake phrase that preceded it.
            return {"ok": True, "mode": "quick", "response": response,
                    "transcript": normalized["text"], "task": None}

        self.session.thinking()
        task_result = await self._create_task(normalized["text"])
        if not task_result.get("ok"):
            self.session.error("task creation failed")
            return {"ok": False, "error": "task_creation_failed", "details": task_result}
        brief = "Vou tratar disso. Aviso-te quando terminar."
        self.session.cancel()
        return {"ok": True, "mode": "task", "response": brief,
                "transcript": normalized["text"], "task": task_result}

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
        """One wake turn: listen for a command, or cancel cleanly if none comes.

        The critical rule here is that silence must never become a request.
        Previously a wake with no follow-up captured several seconds of silence,
        the transcriber invented filler, and Nano answered a question nobody
        asked. Now the audio is energy-gated and the transcript is checked for
        hallucinations; if either says "no real speech", the turn is cancelled
        and Nano goes back to listening without involving the Brain at all.
        """
        self._publish("WakeWordDetected", {"session_id": self.session.session_id})
        self.session.waiting_for_wake_word()
        if not self.voice.enabled:
            self._publish("VoiceError", {"session_id": self.session.session_id, "error": "voice_disabled"})
            return {"ok": False, "error": "voice_disabled"}

        window = duration_seconds or self.command_timeout_seconds
        self.session.start_listening()
        self._publish("VoiceCommandListening", {"session_id": self.session.session_id, "timeout": window})

        try:
            audio = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self.voice.input_provider.capture(window)
            )
        except ProviderError as exc:
            self.session.error(str(exc))
            self._publish("VoiceError", {"session_id": self.session.session_id, "error": "microphone_failed"})
            return {"ok": False, "error": "microphone_failed", "detail": str(exc)}

        # NO AUDIO and TOO QUIET are different failures and must not share one
        # message. They did, and it cost a human test to find out why: the
        # hotkey reported "nao ouvi nada" whether the microphone had returned
        # nothing at all or had returned a perfectly good voice that the gate
        # then rejected. The log said the same sentence either way, so the one
        # number that would have identified the bug -- the measured energy next
        # to the threshold it was compared against -- was never written down.
        if not audio:
            self.session.cancel()
            self._publish("VoiceWakeCancelled", {"session_id": self.session.session_id,
                                                 "reason": "no_audio"})
            logger.warning("Voice turn cancelled: the microphone returned no audio at all "
                           "in %ss (device=%s).", window, self.voice.input_provider.device_index)
            return {"ok": False, "error": "no_audio", "cancelled": True,
                    "detail": "O microfone nao devolveu audio nenhum."}

        gate = self.voice.input_provider.gate
        if not self._command_has_speech(audio):
            self.session.cancel()
            self._publish("VoiceWakeCancelled", {"session_id": self.session.session_id,
                                                 "reason": "no_speech"})
            logger.info(
                "Voice turn cancelled: nothing loud enough in %ss "
                "(rms=%.1f, threshold=%.1f, calibrated=%s, bytes=%d).",
                window, gate.last_rms, gate.threshold, gate.calibrated, len(audio),
            )
            return {"ok": False, "error": "no_speech", "cancelled": True,
                    "detail": "Nenhum comando detetado; a voltar a escutar.",
                    # Measured, so the UI and the log can say WHY rather than
                    # only that it happened.
                    "rms": round(gate.last_rms, 1),
                    "threshold": round(gate.threshold, 1)}

        self.session.transcribing()
        self._phase("TRANSCRIBING", "A transcrever…")
        result = self.voice.stt_provider.transcribe(audio)
        transcript = (result.text or "").strip() if result.ok else ""

        if not transcript or not speech_filter.is_usable_command(transcript):
            self.session.cancel()
            self._publish("VoiceWakeCancelled", {"session_id": self.session.session_id, "reason": "no_usable_command"})
            logger.info("Wake turn cancelled: transcript %r is not a usable command.", transcript)
            return {"ok": False, "error": "no_usable_command", "cancelled": True,
                    "detail": "Não percebi nenhum comando; a voltar a escutar."}

        self._publish("TranscriptionCompleted", {"session_id": self.session.session_id, "text_length": len(transcript)})
        self._phase("PROCESSING", "A pensar…")
        return await self.process_request(transcript)

    async def handle_wake_word(self, *, duration_seconds: int | None = None) -> dict:
        return await self.process_wake_word_turn(duration_seconds=duration_seconds)

    # ==================================================================
    #  THE VOICE TURN
    # ==================================================================

    #: Triggers that may start a voice turn. "hotkey" is reserved for the
    #: global shortcut that becomes the primary V1 activation; nothing
    #: registers it yet.
    TURN_SOURCES = ("wake_phrase", "hotkey", "ui")

    def _busy_result(self) -> dict:
        status = self.turn_status()
        return {
            "ok": False,
            "busy": True,
            "error": "voice_turn_in_progress",
            "active_source": status.get("source"),
            "phase": status.get("phase"),
            "detail": "O Nano já está a ouvir. Espera que este turno termine.",
        }

    def _take_microphone(self) -> bool:
        """Make this thread the only reader of the microphone.

        The wake-phrase engine holds a persistent capture stream and reads from
        it continuously. A hotkey or UI turn arriving mid-read would be a second
        reader on that same stream, so the engine is paused and we WAIT for its
        in-flight read to finish before capturing anything.

        A wake-phrase turn does not need this: that engine calls us from its own
        loop thread, so by construction it is not reading.
        """
        provider = getattr(self.voice, "wake_phrase_provider", None)
        waiter = getattr(provider, "pause_and_wait", None)
        if waiter is None:
            return True
        try:
            return bool(waiter(5.0))
        except Exception:
            logger.warning("could not pause the wake detector", exc_info=True)
            return False

    def _release_microphone(self, resumed: bool) -> None:
        provider = getattr(self.voice, "wake_phrase_provider", None)
        if provider is None or not resumed:
            return
        try:
            provider.resume()
        except Exception:
            logger.debug("could not resume the wake detector", exc_info=True)

    def _mark(self, method: str) -> None:
        """Best-effort wake-engine state marker; absent engine is fine."""
        provider = getattr(self.voice, "wake_phrase_provider", None)
        fn = getattr(provider, method, None)
        if fn is None:
            return
        try:
            fn()
        except Exception:
            logger.debug("wake state marker %s failed", method, exc_info=True)

    def _idle_phase(self) -> tuple[str, str]:
        """Where the UI should return to once a turn ends."""
        provider = getattr(self.voice, "wake_phrase_provider", None)
        try:
            running = bool(getattr(provider, "running", False))
            phrase = (provider.status().get("phrase") if provider else None) or "ei nano"
        except Exception:
            running, phrase = False, "ei nano"
        if running:
            return "WAKE_LISTENING", f"\"{phrase}\" — A ouvir"
        return "IDLE", ""

    def _tts_settings(self) -> tuple[bool, bool]:
        """The live (tts_enabled, voice_reply_tts) pair.

        Read on every turn, never cached at construction: core.main's
        update_setting mutates CONFIG["voice"] in place and this runtime holds
        a reference to that same dict, so a change made in Settings applies to
        the very next turn. Caching these in __init__ would make the first
        turn's settings permanent.
        """
        config = self.config or {}
        voice_cfg = (config.get("voice") or {}) if "voice" in config else config
        return (bool(voice_cfg.get("tts_enabled", True)),
                bool(voice_cfg.get("voice_reply_tts", True)))

    def speaking_decision(self, source: str, response: str, *, speak: bool = True) -> tuple[bool, str]:
        """Whether this turn speaks, and -- always -- why not.

        THE CONTRACT: a voice-originated turn with a real answer speaks, unless
        the user turned speech off, the caller asked for silence, or there is
        nothing to say. Every other outcome is a bug, and the reason string
        exists so a silent turn can never again be diagnosed by guessing.

        typed_chat_tts is deliberately NOT consulted here. Typing and talking
        are separate conversations: core.main._should_speak governs the typed
        path, this governs the spoken one, and neither may switch the other.
        """
        if not speak:
            return False, "caller_requested_silence"
        if not response or not str(response).strip():
            return False, "empty_response"
        tts_enabled, voice_reply_tts = self._tts_settings()
        if not tts_enabled:
            return False, "tts_enabled=false"
        if not voice_reply_tts:
            return False, "voice_reply_tts=false"
        if not self.voice.enabled:
            return False, "voice_disabled"
        return True, "ok"

    async def run_voice_turn(
        self,
        source: str = "ui",
        *,
        transcript: str = "",
        duration_seconds: int | None = None,
        chime: bool = True,
        speak: bool = True,
    ) -> dict:
        """One complete spoken turn, from acknowledgement to silence.

        THIS IS THE ONLY PLACE THE CHOREOGRAPHY LIVES. It used to be inlined in
        main._on_wake_phrase, which meant any second trigger -- the global
        hotkey being the imminent one -- would have had to reimplement the
        chime, the phase events, the on-screen exchange, the TTS dispatch and
        the return to listening, and the two copies would have drifted. Every
        trigger now calls this and differs only in ``source``.

        The sequence, in order:

            acknowledgement chime      immediate, local, before anything slow
            COMMAND_LISTENING          UI phase + wake-engine state
            capture                    bounded by command_timeout_seconds
            speech gate                calibrated; silence never becomes a request
            STT + hallucination filter
            PROCESSING                 UI phase
            Brain                      through the normal request pipeline
            visible exchange           so a spoken answer leaves a record
            SPEAKING + TTS
            back to IDLE / WAKE_LISTENING

        Safety: this resolves no capability and runs no tool. Everything it
        triggers goes through process_request -> Brain -> policy -> permission
        -> execution, exactly as a typed message does.
        """
        if source not in self.TURN_SOURCES:
            source = "ui"

        # Non-blocking: a second trigger is answered honestly, not queued.
        if not self._turn_lock.acquire(blocking=False):
            logger.info("Voice turn from %r refused: a %r turn is already active.",
                        source, self._turn_source)
            return self._busy_result()

        self._turn_source = source
        self._turn_started_at = time.time()
        took_microphone = False
        try:
            if not self.voice.enabled:
                self._publish("VoiceError", {"session_id": self.session.session_id,
                                             "error": "voice_disabled"})
                return {"ok": False, "error": "voice_disabled", "source": source}

            # 1. Audible acknowledgement FIRST. The user needs to know within a
            #    moment that Nano heard them, otherwise they talk into the gap.
            #    Synthesised locally: no TTS model, no network, a few ms.
            if chime:
                from core import audio_feedback

                if not audio_feedback.acknowledge_wake():
                    logger.warning("Activação sem som de confirmação audível.")

            self._activation(transcript)
            self._phase("WAKE_DETECTED", "Nano acordou")

            # 2. Take sole ownership of the microphone.
            if source != "wake_phrase":
                took_microphone = True
                if not self._take_microphone():
                    self._phase(*self._idle_phase())
                    return {"ok": False, "error": "microphone_busy", "source": source,
                            "detail": "O microfone ainda está ocupado; tenta outra vez."}

            self._mark("mark_command_listening")
            self._phase("COMMAND_LISTENING", "A ouvir comando…")

            # 3. Capture -> gate -> STT -> Brain, through the existing pipeline.
            result = await self.process_wake_word_turn(duration_seconds=duration_seconds)
            result["source"] = source

            self._mark("mark_processing")

            if result.get("requires_permission"):
                logger.info("Voice permission request pending: %s", result.get("request_id"))

            response = result.get("response")
            if result.get("ok") and response:
                # A spoken answer the user cannot see afterwards leaves no
                # record of what was asked or answered.
                turn_id = uuid.uuid4().hex
                result["turn_id"] = turn_id
                self._exchange(turn_id,
                               str(result.get("transcript") or transcript or ""),
                               str(response))

                should_speak, reason = self.speaking_decision(source, str(response), speak=speak)
                tts_enabled, voice_reply_tts = self._tts_settings()
                # One line per turn recording the whole decision. No transcript
                # and no answer text -- only lengths and flags. Without this a
                # turn that does not speak is indistinguishable from a turn that
                # spoke silently, which is exactly how the last two voice bugs
                # stayed hidden until a human noticed.
                logger.info(
                    "voice_turn_post_brain: source=%s response_chars=%d tts_enabled=%s "
                    "voice_reply_tts=%s cancelled=%s should_speak=%s reason=%s",
                    source, len(str(response)), tts_enabled, voice_reply_tts,
                    bool(result.get("cancelled")), should_speak, reason,
                )
                result["should_speak"] = should_speak
                result["speak_skip_reason"] = None if should_speak else reason

                if should_speak:
                    self._phase("SPEAKING", "A falar…")
                    logger.info("voice_tts: started (source=%s, %d chars)", source, len(str(response)))
                    spoken = await self.speak_response(str(response))
                    result["spoken"] = spoken
                    if spoken:
                        logger.info("voice_tts: completed (source=%s)", source)
                    else:
                        logger.warning(
                            "voice_tts: FAILED (source=%s) - a reply was generated but "
                            "nothing was heard: %r", source, str(response)[:80])
                        self._signal_error()
                else:
                    result["spoken"] = False
                    logger.info("voice_tts: skipped (source=%s, reason=%s)", source, reason)
            elif result.get("cancelled"):
                # Silence after an activation is a normal, quiet outcome: no
                # Brain request was made and nothing is spoken.
                logger.info("Turno de voz cancelado: %s", result.get("error"))
            elif not result.get("ok"):
                logger.warning("Turno de voz falhou: %s", result.get("error"))
                self._signal_error()

            return result
        except Exception as exc:
            logger.exception("Erro no turno de voz (%s)", source)
            self._signal_error()
            return {"ok": False, "error": "voice_turn_failed", "detail": str(exc),
                    "source": source}
        finally:
            self._mark("mark_idle")
            self._release_microphone(took_microphone)
            self._phase(*self._idle_phase())
            self._turn_source = None
            self._turn_started_at = None
            self._turn_lock.release()

    @staticmethod
    def _signal_error() -> None:
        try:
            from core import audio_feedback

            audio_feedback.signal_error()
        except Exception:
            logger.debug("could not play the error tone", exc_info=True)

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
    "VoiceReadiness",
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
    "WakePhraseProvider",
    "ProviderError",
    "_clean_for_tts",
]
