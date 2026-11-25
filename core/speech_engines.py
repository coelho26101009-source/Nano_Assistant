"""NANO's speaking voice: several engines, one chain, never mute.

THE PROBLEM
-----------
NANO spoke through ONE engine: edge-tts, Microsoft's free neural voices. They
are good (``pt-PT-DuarteNeural`` is one of the best free European Portuguese
voices there is), but the engine needs the internet. Offline, or when the
service hiccuped, NANO went silent: the answer appeared on screen and nothing
was heard. And there was no way to get a more human voice than that one.

WHAT THIS DOES
--------------
Four engines behind one interface (:class:`Engine`), tried in order by a
:class:`SpeechChain` until one produces audio:

``edge``        Microsoft's neural voices through edge-tts. Free, online, the
                default. Nothing to install.
``local``       Natural voices that run on this PC, offline, on the processor
                (sherpa-onnx): Piper "dii" for European Portuguese and Kokoro
                for English. Installed from Settings → Voz with one click
                (core/voice_local_pack.py). Text never leaves the PC.
``elevenlabs``  The most human voices available, with the person's OWN
                ElevenLabs key. Paid by them, so it is only ever used when they
                choose it: never as an automatic fallback.
``windows``     The voice that comes with Windows (System.Speech). Robotic,
                but always there: the last resort, so NANO is never mute.

The person picks the engine in Settings → Voz (``tts_engine``). "auto" is the
default: Edge first, then the local voice, then Windows. In LOCAL mode (the
model runs on this PC) "auto" puts the local voice FIRST when it is installed,
so a local conversation stays local. A chosen engine always comes first and
the automatic order follows it, so a failure is never silence.

An engine that fails is skipped for a while (``cooldown``): offline, every
sentence would otherwise wait for Edge to time out before falling back.

Nothing here is a tool. The model cannot choose an engine, a voice, a file or
a command: it only provides the text to be read.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("nano.speech")

ENGINES = ("auto", "edge", "local", "elevenlabs", "windows")
DEFAULT_ENGINE = "auto"
#: "auto" never includes ElevenLabs: it is paid, so only a choice turns it on.
AUTO_ORDER = ("edge", "local", "windows")
AUTO_ORDER_LOCAL_MODE = ("local", "edge", "windows")

#: Speed in percent, as Settings shows it (-30 … +30).
MIN_SPEED, MAX_SPEED = -30, 30

# ── local voices (sherpa-onnx models, see core/voice_local_pack.py) ──────────
#: id -> (model, speaker id, label, language)
LOCAL_VOICES: dict[str, tuple[str, int, str, str]] = {
    "dii": ("piper-pt_PT-dii-high", 0, "Dii (Portugal)", "pt-PT"),
    "george": ("kokoro-multi-lang-v1_0", 26, "George (UK)", "en"),
    "lewis": ("kokoro-multi-lang-v1_0", 27, "Lewis (UK)", "en"),
    "daniel": ("kokoro-multi-lang-v1_0", 24, "Daniel (UK)", "en"),
    "michael": ("kokoro-multi-lang-v1_0", 16, "Michael (US)", "en"),
    "emma": ("kokoro-multi-lang-v1_0", 21, "Emma (UK)", "en"),
    "heart": ("kokoro-multi-lang-v1_0", 3, "Heart (US)", "en"),
}
DEFAULT_LOCAL_VOICE = {"pt-PT": "dii", "en": "george"}

# ── ElevenLabs ───────────────────────────────────────────────────────────────
ELEVENLABS_API = "https://api.elevenlabs.io/v1"
#: Flash v2.5: about 75 ms of model latency and half the credits per letter;
#: Multilingual v2: their most natural model, slower and dearer.
ELEVENLABS_MODELS = ("eleven_flash_v2_5", "eleven_multilingual_v2")
DEFAULT_ELEVENLABS_MODEL = "eleven_flash_v2_5"
#: "Daniel": a calm British voice from ElevenLabs' default library, close to
#: the JARVIS manner. Any voice of the person's own account can replace it.
DEFAULT_ELEVENLABS_VOICE = "onwK4e9ZLuTAKqWW03F9"
_VOICE_ID = re.compile(r"^[A-Za-z0-9]{8,40}$")
#: Models that accept language_code (the others reject the field).
_LANGUAGE_CODE_MODELS = frozenset({"eleven_flash_v2_5", "eleven_turbo_v2_5"})

#: How long a failed engine is skipped, by kind of failure (seconds).
COOLDOWN_NETWORK = 45.0
COOLDOWN_QUOTA = 15 * 60.0
COOLDOWN_AUTH = 60 * 60.0
#: Longest wait for one piece of speech from an online engine.
ONLINE_TIMEOUT = 15.0


class SynthesisError(Exception):
    """An engine could not produce audio. ``cooldown`` > 0 skips it for a while."""

    def __init__(self, message: str, *, reason: str = "error", cooldown: float = COOLDOWN_NETWORK):
        super().__init__(message)
        self.reason = reason
        self.cooldown = cooldown


@dataclass
class SpeechRequest:
    """Everything an engine needs besides the text. Built fresh for each answer."""

    language: str = "pt-PT"            # interface language: "pt-PT" or "en"
    edge_voice: str = "pt-PT-DuarteNeural"
    local_voice: str = ""              # LOCAL_VOICES id; empty follows the language
    elevenlabs_voice: str = ""
    elevenlabs_model: str = DEFAULT_ELEVENLABS_MODEL
    speed: int = 0                     # percent, MIN_SPEED … MAX_SPEED
    volume: str = "+0%"
    pitch: str = "+0Hz"
    engine: str = DEFAULT_ENGINE
    local_mode: bool = False           # the AI mode is LOCAL

    @classmethod
    def from_config(cls, cfg: dict[str, Any], *, local_mode: bool = False) -> "SpeechRequest":
        voice = str(cfg.get("voice") or cfg.get("voice_name") or "pt-PT-DuarteNeural")
        language = str(cfg.get("language") or "")
        if language not in ("pt-PT", "en"):
            language = "pt-PT" if voice.lower().startswith("pt-") else "en"
        engine = str(cfg.get("engine") or DEFAULT_ENGINE)
        return cls(
            language=language,
            edge_voice=voice,
            local_voice=str(cfg.get("local_voice") or ""),
            elevenlabs_voice=str(cfg.get("elevenlabs_voice") or ""),
            elevenlabs_model=str(cfg.get("elevenlabs_model") or DEFAULT_ELEVENLABS_MODEL),
            speed=parse_speed(cfg.get("speed")),
            volume=str(cfg.get("volume") or "+0%"),
            pitch=str(cfg.get("pitch") or "+0Hz"),
            engine=engine if engine in ENGINES else DEFAULT_ENGINE,
            local_mode=local_mode,
        )


def parse_speed(value: Any) -> int:
    """``"+10%"``, ``10`` or ``"10"`` -> 10, clamped to the range Settings offers."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        number = int(value)
    else:
        match = re.fullmatch(r"\s*([+-]?\d{1,3})\s*%?\s*", str(value or ""))
        number = int(match.group(1)) if match else 0
    return max(MIN_SPEED, min(MAX_SPEED, number))


def local_voice_for(request: SpeechRequest) -> str:
    """The local voice that speaks the request's language."""
    chosen = LOCAL_VOICES.get(request.local_voice)
    if chosen and chosen[3] == request.language:
        return request.local_voice
    return DEFAULT_LOCAL_VOICE.get(request.language, DEFAULT_LOCAL_VOICE["en"])


def local_voice_options(language: str) -> list[dict[str, str]]:
    return [{"id": key, "label": label} for key, (_m, _s, label, lang) in LOCAL_VOICES.items()
            if lang == language]


def valid_setting(key: str, value: Any) -> bool | None:
    """Validation for the speech keys; None when ``key`` is not one of them."""
    if key == "tts_engine":
        return isinstance(value, str) and value in ENGINES
    if key == "tts_local_voice":
        return isinstance(value, str) and (value == "" or value in LOCAL_VOICES)
    if key == "tts_elevenlabs_voice":
        return isinstance(value, str) and (value == "" or bool(_VOICE_ID.match(value)))
    if key == "tts_elevenlabs_model":
        return isinstance(value, str) and value in ELEVENLABS_MODELS
    if key == "tts_speed":
        return type(value) is int and MIN_SPEED <= value <= MAX_SPEED
    return None


KEYS = ("tts_engine", "tts_local_voice", "tts_elevenlabs_voice", "tts_elevenlabs_model", "tts_speed")


def scratch_path(suffix: str) -> str:
    """A unique temp file for one piece of audio (see core/voice.py for why)."""
    handle, path = tempfile.mkstemp(prefix="nano-audio-", suffix=suffix)
    os.close(handle)
    return path


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def write_wav(path: str, samples: Any, sample_rate: int) -> None:
    """Float samples (-1…1) to a 16-bit mono WAV, with the standard library only."""
    pcm = array("h", (int(max(-1.0, min(1.0, float(x))) * 32767) for x in samples))
    if sys.byteorder == "big":  # pragma: no cover - every supported PC is little-endian
        pcm.byteswap()
    with wave.open(path, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(int(sample_rate))
        out.writeframes(pcm.tobytes())


# ═════════════════════════════════════════════════════════════════════════════
#  Engines
# ═════════════════════════════════════════════════════════════════════════════

class Engine:
    id = ""
    online = False      # sends the text over the internet

    def available(self) -> bool:
        raise NotImplementedError

    def voice_label(self, request: SpeechRequest) -> str:
        return ""

    async def synthesize(self, text: str, request: SpeechRequest) -> str:
        """Audio for ``text`` in a new scratch file (the caller deletes it)."""
        raise NotImplementedError


class EdgeEngine(Engine):
    """Microsoft's neural voices (edge-tts). Free; needs the internet."""

    id = "edge"
    online = True

    def __init__(self, timeout: float = ONLINE_TIMEOUT):
        self.timeout = timeout

    def available(self) -> bool:
        import importlib.util

        try:
            return importlib.util.find_spec("edge_tts") is not None
        except (ImportError, ValueError):
            return "edge_tts" in sys.modules

    def voice_label(self, request: SpeechRequest) -> str:
        name = request.edge_voice.rsplit("-", 1)[-1]
        return name.replace("MultilingualNeural", "").replace("Neural", "")

    async def synthesize(self, text: str, request: SpeechRequest) -> str:
        import edge_tts

        kwargs: dict[str, Any] = {"rate": f"{request.speed:+d}%", "volume": request.volume}
        if request.pitch and request.pitch not in ("+0Hz", "0Hz"):
            kwargs["pitch"] = request.pitch
        path = scratch_path(".mp3")
        try:
            communicate = edge_tts.Communicate(text, request.edge_voice, **kwargs)
            await asyncio.wait_for(communicate.save(path), timeout=self.timeout)
            if os.path.getsize(path) == 0:
                raise SynthesisError("edge-tts returned no audio", reason="empty")
            return path
        except SynthesisError:
            _discard(path)
            raise
        except asyncio.TimeoutError as exc:
            _discard(path)
            raise SynthesisError("edge-tts timed out", reason="timeout") from exc
        except asyncio.CancelledError:
            _discard(path)
            raise
        except Exception as exc:
            _discard(path)
            raise SynthesisError(f"edge-tts failed: {type(exc).__name__}", reason="network") from exc


class LocalEngine(Engine):
    """Natural voices on this PC (sherpa-onnx): Piper pt-PT, Kokoro English."""

    id = "local"

    def __init__(self, models_dir: Callable[[], Path] | None = None, threads: int | None = None):
        self._models_dir = models_dir
        self._threads = threads or max(1, min(4, (os.cpu_count() or 2) // 2))
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()
        #: One synthesis at a time per process: a sherpa-onnx voice is not
        #: documented as safe to call from two threads at once (a preview and
        #: a spoken answer could otherwise overlap).
        self._generate_lock = threading.Lock()

    def _dir(self) -> Path:
        if self._models_dir is not None:
            return self._models_dir()
        from core import voice_local_pack

        return voice_local_pack.models_dir()

    @staticmethod
    def _package() -> bool:
        import importlib.util

        try:
            return importlib.util.find_spec("sherpa_onnx") is not None
        except (ImportError, ValueError):
            return False

    def ready_for(self, language: str) -> bool:
        model = LOCAL_VOICES[DEFAULT_LOCAL_VOICE.get(language, "george")][0]
        return self._package() and self._model_ready(model)

    def _model_ready(self, model: str) -> bool:
        from core import voice_local_pack

        return voice_local_pack.model_ready(model, self._dir())

    def available(self) -> bool:
        # Per language, really; this answers "is there any local voice at all".
        return self._package() and any(self._model_ready(m) for m in {v[0] for v in LOCAL_VOICES.values()})

    def voice_label(self, request: SpeechRequest) -> str:
        return LOCAL_VOICES[local_voice_for(request)][2]

    def _tts(self, model: str) -> Any:
        with self._lock:
            if model in self._cache:
                return self._cache[model]
            from core import voice_local_pack

            config = voice_local_pack.sherpa_config(model, self._dir(), threads=self._threads)
            import sherpa_onnx

            tts = sherpa_onnx.OfflineTts(config)
            self._cache[model] = tts
            return tts

    def _generate(self, text: str, request: SpeechRequest) -> str:
        voice = local_voice_for(request)
        model, speaker, _label, _lang = LOCAL_VOICES[voice]
        if not self._package():
            raise SynthesisError("sherpa-onnx is not installed", reason="not_installed", cooldown=0)
        if not self._model_ready(model):
            raise SynthesisError(f"local voice {model} is not downloaded", reason="not_installed", cooldown=0)
        tts = self._tts(model)
        speed = max(0.6, min(1.5, 1.0 + request.speed / 100.0))
        with self._generate_lock:
            audio = tts.generate(text, sid=speaker, speed=speed)
        samples = getattr(audio, "samples", None)
        rate = int(getattr(audio, "sample_rate", 0) or 0)
        if not samples or rate <= 0:
            raise SynthesisError("local voice produced no audio", reason="empty", cooldown=0)
        path = scratch_path(".wav")
        try:
            write_wav(path, samples, rate)
        except Exception:
            _discard(path)
            raise
        return path

    async def synthesize(self, text: str, request: SpeechRequest) -> str:
        try:
            return await asyncio.to_thread(self._generate, text, request)
        except SynthesisError:
            raise
        except Exception as exc:
            raise SynthesisError(f"local voice failed: {type(exc).__name__}", reason="error",
                                 cooldown=COOLDOWN_NETWORK) from exc

    def warm(self, language: str) -> bool:
        """Load the voice for ``language`` now, so the first answer does not wait
        2-3 s for it. Safe to call from any thread; False when not installed."""
        model = LOCAL_VOICES[DEFAULT_LOCAL_VOICE.get(language, "george")][0]
        if not (self._package() and self._model_ready(model)):
            return False
        try:
            self._tts(model)
            return True
        except Exception:
            logger.warning("Não consegui carregar a voz offline %s", model, exc_info=True)
            return False

    def unload(self) -> None:
        with self._lock:
            self._cache.clear()


class ElevenLabsEngine(Engine):
    """ElevenLabs, with the person's own key (secret_store "elevenlabs_api_key")."""

    id = "elevenlabs"
    online = True

    def __init__(self, key: Callable[[], str] | None = None, client_factory: Callable[[], Any] | None = None,
                 timeout: float = ONLINE_TIMEOUT):
        self._key = key
        self._client_factory = client_factory
        self.timeout = timeout

    def api_key(self) -> str:
        if self._key is not None:
            return str(self._key() or "")
        from core import secret_store

        return secret_store.get_secret("elevenlabs_api_key")

    def available(self) -> bool:
        try:
            return bool(self.api_key())
        except Exception:
            return False

    def voice_label(self, request: SpeechRequest) -> str:
        return "ElevenLabs"

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        import httpx

        from core import http_tls

        return httpx.AsyncClient(timeout=self.timeout, verify=http_tls.shared_context())

    def payload(self, text: str, request: SpeechRequest) -> dict[str, Any]:
        model = request.elevenlabs_model if request.elevenlabs_model in ELEVENLABS_MODELS \
            else DEFAULT_ELEVENLABS_MODEL
        body: dict[str, Any] = {
            "text": text,
            "model_id": model,
            "voice_settings": {
                # A little expressive, still steady: JARVIS does not perform.
                "stability": 0.5, "similarity_boost": 0.75, "style": 0.0,
                "use_speaker_boost": True,
                "speed": round(max(0.7, min(1.2, 1.0 + request.speed / 100.0)), 2),
            },
        }
        if model in _LANGUAGE_CODE_MODELS:
            body["language_code"] = "pt" if request.language == "pt-PT" else "en"
        return body

    async def synthesize(self, text: str, request: SpeechRequest) -> str:
        key = self.api_key()
        if not key:
            raise SynthesisError("no ElevenLabs key", reason="no_key", cooldown=0)
        voice = request.elevenlabs_voice if _VOICE_ID.match(request.elevenlabs_voice or "") \
            else DEFAULT_ELEVENLABS_VOICE
        url = f"{ELEVENLABS_API}/text-to-speech/{voice}"
        headers = {"xi-api-key": key, "Accept": "audio/mpeg", "Content-Type": "application/json"}
        try:
            async with self._client() as client:
                response = await asyncio.wait_for(
                    client.post(url, params={"output_format": "mp3_44100_128"},
                                headers=headers, json=self.payload(text, request)),
                    timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise SynthesisError("ElevenLabs timed out", reason="timeout") from exc
        except Exception as exc:
            raise SynthesisError(f"ElevenLabs unreachable: {type(exc).__name__}", reason="network") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 401:
            raise SynthesisError("ElevenLabs refused the key", reason="auth", cooldown=COOLDOWN_AUTH)
        if status in (402, 429) or (status == 400 and "quota" in _body_text(response)):
            raise SynthesisError("ElevenLabs quota or rate limit", reason="quota", cooldown=COOLDOWN_QUOTA)
        if status == 404 or (status == 400 and "voice" in _body_text(response)):
            raise SynthesisError("ElevenLabs voice not found", reason="voice", cooldown=COOLDOWN_QUOTA)
        if status != 200:
            raise SynthesisError(f"ElevenLabs HTTP {status}", reason="network")
        audio = bytes(getattr(response, "content", b"") or b"")
        if not audio:
            raise SynthesisError("ElevenLabs returned no audio", reason="empty")
        path = scratch_path(".mp3")
        with open(path, "wb") as handle:
            handle.write(audio)
        return path

    async def list_voices(self) -> list[dict[str, str]]:
        """The voices of the person's account: id, name and a short description."""
        key = self.api_key()
        if not key:
            raise SynthesisError("no ElevenLabs key", reason="no_key", cooldown=0)
        try:
            async with self._client() as client:
                response = await client.get(f"{ELEVENLABS_API}/voices", headers={"xi-api-key": key})
        except Exception as exc:
            raise SynthesisError(f"ElevenLabs unreachable: {type(exc).__name__}", reason="network") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 401:
            raise SynthesisError("ElevenLabs refused the key", reason="auth", cooldown=0)
        if status != 200:
            raise SynthesisError(f"ElevenLabs HTTP {status}", reason="network", cooldown=0)
        voices = []
        for item in (response.json() or {}).get("voices", [])[:200]:
            voice_id = str(item.get("voice_id") or "")
            if not _VOICE_ID.match(voice_id):
                continue
            labels = item.get("labels") or {}
            details = ", ".join(str(labels[k]) for k in ("accent", "gender", "age")
                                if isinstance(labels.get(k), str) and labels.get(k))
            name = " ".join(str(item.get("name") or voice_id).split())[:60]
            voices.append({"id": voice_id, "label": f"{name} ({details[:40]})" if details else name})
        return voices


def _body_text(response: Any) -> str:
    try:
        return str(getattr(response, "text", "") or "").lower()[:2000]
    except Exception:
        return ""


#: Reads the text from a UTF-8 file (stdin would arrive in the console code
#: page and lose every accent), picks an installed voice for the language and
#: writes a WAV. Fixed script: only the environment values change, and they are
#: a temp-file path, a culture name and a number.
_SAPI_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "Add-Type -AssemblyName System.Speech;"
    "$t=[IO.File]::ReadAllText($env:NANO_TTS_IN,[Text.Encoding]::UTF8);"
    "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "$v=$s.GetInstalledVoices()|Where-Object{$_.Enabled -and "
    "$_.VoiceInfo.Culture.Name -like ($env:NANO_TTS_CULTURE+'*')}|Select-Object -First 1;"
    "if($v){$s.SelectVoice($v.VoiceInfo.Name)};"
    "$s.Rate=[int]$env:NANO_TTS_RATE;"
    "$s.SetOutputToWaveFile($env:NANO_TTS_OUT);"
    "$s.Speak($t);$s.Dispose()"
)


class WindowsEngine(Engine):
    """The voice built into Windows. Robotic, offline, always there."""

    id = "windows"

    def __init__(self, runner: Callable[..., Any] = subprocess.run):
        self._runner = runner

    def available(self) -> bool:
        if sys.platform != "win32":
            return False
        # Tests switch it off: a test simulating "every engine failed" must not
        # be rescued by the real Windows voice on a Windows CI runner.
        if os.environ.get("NANO_TTS_SYSTEM_VOICE", "1") == "0":
            return False
        return shutil.which("powershell.exe") is not None or shutil.which("powershell") is not None

    def voice_label(self, request: SpeechRequest) -> str:
        return "Windows"

    def _run(self, text: str, request: SpeechRequest) -> str:
        source = scratch_path(".txt")
        out = scratch_path(".wav")
        try:
            with open(source, "w", encoding="utf-8") as handle:
                handle.write(text)
            env = dict(os.environ)
            env.update({"NANO_TTS_IN": source, "NANO_TTS_OUT": out,
                        "NANO_TTS_CULTURE": "pt" if request.language == "pt-PT" else "en",
                        "NANO_TTS_RATE": str(max(-10, min(10, round(request.speed / 10))))})
            exe = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
            kwargs: dict[str, Any] = {"capture_output": True, "timeout": 30, "env": env}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = self._runner([exe, "-NoProfile", "-NonInteractive", "-Command", _SAPI_SCRIPT], **kwargs)
            if getattr(result, "returncode", 1) != 0 or not os.path.exists(out) or os.path.getsize(out) <= 44:
                raise SynthesisError("Windows voice failed", reason="error")
            return out
        except SynthesisError:
            _discard(out)
            raise
        except Exception as exc:
            _discard(out)
            raise SynthesisError(f"Windows voice failed: {type(exc).__name__}", reason="error") from exc
        finally:
            _discard(source)

    async def synthesize(self, text: str, request: SpeechRequest) -> str:
        return await asyncio.to_thread(self._run, text, request)


# ═════════════════════════════════════════════════════════════════════════════
#  The chain
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class _EngineState:
    skip_until: float = 0.0
    reason: str = ""
    failures: int = 0


@dataclass
class SpeechChain:
    """Tries the engines in order until one speaks. Remembers who failed and why."""

    engines: dict[str, Engine] = field(default_factory=dict)
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if not self.engines:
            self.engines = {"edge": EdgeEngine(), "local": LocalEngine(),
                            "elevenlabs": ElevenLabsEngine(), "windows": WindowsEngine()}
        self._state: dict[str, _EngineState] = {name: _EngineState() for name in self.engines}
        self._lock = threading.Lock()
        self.last_engine = ""
        self.last_voice = ""
        self.last_fallback_reason = ""

    def order(self, request: SpeechRequest) -> list[str]:
        auto = AUTO_ORDER_LOCAL_MODE if request.local_mode else AUTO_ORDER
        if request.local_mode and request.engine == "auto":
            local = self.engines.get("local")
            # Only when the voice for THIS language is on disk; otherwise Edge
            # stays first, exactly as before.
            if not (isinstance(local, LocalEngine) and local.ready_for(request.language)):
                auto = AUTO_ORDER
        first = [request.engine] if request.engine in self.engines else []
        ordered = first + [name for name in auto if name not in first]
        return [name for name in ordered if name in self.engines]

    def available(self) -> bool:
        return any(self._safe_available(engine) for engine in self.engines.values())

    @staticmethod
    def _safe_available(engine: Engine) -> bool:
        try:
            return bool(engine.available())
        except Exception:
            return False

    def _cooling(self, name: str) -> bool:
        with self._lock:
            return self._state[name].skip_until > self.clock()

    def _record_failure(self, name: str, error: SynthesisError) -> None:
        with self._lock:
            state = self._state[name]
            state.failures += 1
            state.reason = error.reason
            if error.cooldown > 0:
                state.skip_until = self.clock() + error.cooldown

    def _record_success(self, name: str) -> None:
        with self._lock:
            self._state[name] = _EngineState()

    def reset(self, name: str | None = None) -> None:
        """Forget failures (after a new key, an install, or a manual test)."""
        with self._lock:
            for key in ([name] if name else list(self._state)):
                if key in self._state:
                    self._state[key] = _EngineState()

    async def synthesize(self, text: str, request: SpeechRequest) -> tuple[str, str]:
        """``(audio path, engine id)``. Raises SynthesisError when every engine failed."""
        errors: list[str] = []
        names = self.order(request)
        for index, name in enumerate(names):
            engine = self.engines[name]
            # A cooling engine is skipped, unless it is the last one there is.
            if self._cooling(name) and index < len(names) - 1:
                continue
            if not self._safe_available(engine):
                continue
            try:
                path = await engine.synthesize(text, request)
            except SynthesisError as exc:
                logger.warning("Voz %s falhou (%s); a tentar a seguinte.", name, exc.reason)
                self._record_failure(name, exc)
                errors.append(f"{name}:{exc.reason}")
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # an engine bug must not silence NANO
                logger.exception("Voz %s falhou de forma inesperada", name)
                self._record_failure(name, SynthesisError(type(exc).__name__))
                errors.append(f"{name}:error")
                continue
            self._record_success(name)
            self.last_engine = name
            self.last_voice = engine.voice_label(request)
            self.last_fallback_reason = errors[0] if errors and names and name != names[0] else ""
            return path, name
        raise SynthesisError("no speech engine could speak: " + (", ".join(errors) or "none available"),
                             reason="all_failed", cooldown=0)

    def status(self, request: SpeechRequest | None = None) -> dict[str, Any]:
        now = self.clock()
        engines = {}
        with self._lock:
            states = {name: (state.skip_until, state.reason, state.failures)
                      for name, state in self._state.items()}
        for name, engine in self.engines.items():
            skip_until, reason, failures = states.get(name, (0.0, "", 0))
            engines[name] = {
                "available": self._safe_available(engine),
                "coolingSeconds": max(0, round(skip_until - now)) if skip_until > now else 0,
                "lastError": reason if failures else "",
            }
        payload: dict[str, Any] = {"engines": engines, "lastEngine": self.last_engine,
                                   "lastVoice": self.last_voice, "fallbackReason": self.last_fallback_reason}
        if request is not None:
            payload["order"] = self.order(request)
        return payload


# ═════════════════════════════════════════════════════════════════════════════
#  Pieces of speech
# ═════════════════════════════════════════════════════════════════════════════

_SENTENCE = re.compile(r"(?<=[.!?…;:])\s+")
#: The first piece is short so the voice starts fast; later pieces are longer
#: so each request carries enough context for natural intonation.
FIRST_PIECE_MIN = 24
PIECE_MAX = 260


def split_for_speech(text: str) -> list[str]:
    """Cut a reply into pieces that are synthesised while the previous one plays."""
    sentences = [s.strip() for s in _SENTENCE.split(" ".join(str(text or "").split())) if s.strip()]
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
        elif not pieces and len(current) < FIRST_PIECE_MIN:
            current = f"{current} {sentence}"
        elif pieces and len(current) + 1 + len(sentence) <= PIECE_MAX:
            current = f"{current} {sentence}"
        else:
            pieces.append(current)
            current = sentence
        if not pieces and len(current) >= FIRST_PIECE_MIN:
            pieces.append(current)
            current = ""
    if current:
        pieces.append(current)
    return pieces


__all__ = [
    "AUTO_ORDER", "DEFAULT_ELEVENLABS_MODEL", "DEFAULT_ELEVENLABS_VOICE", "DEFAULT_ENGINE",
    "DEFAULT_LOCAL_VOICE", "ELEVENLABS_MODELS", "ENGINES", "EdgeEngine", "ElevenLabsEngine", "Engine",
    "KEYS", "LOCAL_VOICES", "LocalEngine", "SpeechChain", "SpeechRequest", "SynthesisError",
    "WindowsEngine", "local_voice_for", "local_voice_options", "parse_speed", "split_for_speech",
    "valid_setting", "write_wav",
]
