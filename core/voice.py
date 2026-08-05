"""
H.E.L.I.O.S. Voice Engine v3 — Vozes Humanas
Prioridade:
  1. ElevenLabs  (melhor qualidade, grátis 10k chars/mês)
  2. OpenAI TTS  (onyx/nova, muito natural, ~$0.015/1k chars)
  3. edge-tts    (gratuito, offline, Microsoft Neural)
"""

import asyncio
import io
import logging
import os
import re
import tempfile
import wave

try:
    import audioop                     # removido no Python 3.13
except ImportError:                    # pragma: no cover
    audioop = None                     # type: ignore[assignment]

logger = logging.getLogger("helios.voice")


class VoiceEngine:
    def __init__(self, config: dict):
        self.config        = config
        self.tts_enabled   = config.get("tts_enabled", True)
        self.tts_provider  = config.get("tts_provider", "auto")   # auto | elevenlabs | openai | edge
        self.edge_voice    = config.get("edge_voice", "pt-PT-DuarteNeural")
        self.edge_rate     = config.get("edge_rate", "-5%")
        # STT: local (faster-whisper) por defeito quando disponível — sem rede, menor latência
        self.stt_provider  = config.get("stt_provider", "auto")   # auto | faster_whisper | google
        self.whisper_model = config.get("whisper_model", "tiny")  # tiny | base | small
        self.whisper_compute = config.get("whisper_compute_type", "int8")
        self.listen_seconds  = int(config.get("listen_seconds", 7))
        self.silence_stop    = bool(config.get("stop_on_silence", True))
        self._mixer_init   = False
        self._stop_flag    = False
        self._whisper      = None

    # ─── FALAR ────────────────────────────────────────────────────────────────

    async def speak(self, text: str):
        if not self.tts_enabled or not text.strip():
            return

        self._stop_flag = False
        clean = _clean_for_tts(text)
        if not clean:
            return

        logger.info(f"TTS ({self.tts_provider}): '{clean[:60]}...'")

        provider = self._choose_provider()
        try:
            if provider == "elevenlabs":
                await self._speak_elevenlabs(clean)
            elif provider == "openai":
                await self._speak_openai(clean)
            else:
                await self._speak_edge(clean)
        except Exception as e:
            logger.error(f"TTS falhou ({provider}): {e}")
            # Fallback automático
            if provider != "edge":
                logger.info("A tentar fallback edge-tts...")
                try:
                    await self._speak_edge(clean)
                except Exception as e2:
                    logger.error(f"Fallback edge também falhou: {e2}")

    def stop(self):
        self._stop_flag = True
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass

    def _choose_provider(self) -> str:
        if self.tts_provider != "auto":
            return self.tts_provider
        # Auto-deteta qual está disponível
        if os.getenv("ELEVENLABS_API_KEY"):
            return "elevenlabs"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        return "edge"

    # ─── ELEVENLABS ───────────────────────────────────────────────────────────

    async def _speak_elevenlabs(self, text: str):
        """
        ElevenLabs — melhor qualidade, indistinguível de humano.
        Grátis: 10.000 chars/mês em elevenlabs.io
        """
        import httpx

        api_key  = os.getenv("ELEVENLABS_API_KEY", "")
        # Lê voice_id do .env primeiro, depois do settings.yaml
        voice_id = os.getenv("ELEVENLABS_VOICE_ID") or self.config.get("elevenlabs_voice_id", "pNInz6obpgDQGcFmaJgB")
        # Vozes recomendadas para PT:
        # pNInz6obpgDQGcFmaJgB — Adam (masculina, muito natural)
        # EXAVITQu4vr4xnSDxMaL — Bella (feminina, suave)
        # VR6AewLTigWG4xSOukaG — Arnold (masculina, profunda)
        # 21m00Tcm4TlvDq8ikWAM — Rachel (feminina, clara)

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",  # Suporta PT nativo
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "style": 0.3,
                "use_speaker_boost": True,
            }
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            audio_bytes = resp.content

        await asyncio.get_event_loop().run_in_executor(
            None, self._play_bytes_mp3, audio_bytes
        )

    # ─── OPENAI TTS ───────────────────────────────────────────────────────────

    async def _speak_openai(self, text: str):
        """
        OpenAI TTS — vozes onyx/nova muito naturais.
        Custo: ~$0.015 por 1000 chars (muito barato)
        """
        import httpx

        api_key = os.getenv("OPENAI_API_KEY", "")
        voice   = self.config.get("openai_voice", "onyx")
        # Vozes: onyx (masculina profunda), nova (feminina), echo (masculina)
        # alloy (neutra), fable (britânica), shimmer (feminina suave)

        url = "https://api.openai.com/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "tts-1-hd",  # Alta qualidade
            "input": text,
            "voice": voice,
            "speed": self.config.get("openai_speed", 1.0),
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            audio_bytes = resp.content

        await asyncio.get_event_loop().run_in_executor(
            None, self._play_bytes_mp3, audio_bytes
        )

    # ─── EDGE-TTS (fallback) ──────────────────────────────────────────────────

    async def _speak_edge(self, text: str):
        """Microsoft Neural TTS — gratuito, sem limites."""
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            self.edge_voice,
            rate=self.edge_rate,
            volume=self.config.get("edge_volume", "+0%"),
        )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp_path = tmp.name

        try:
            await communicate.save(tmp_path)
            await asyncio.get_event_loop().run_in_executor(
                None, self._play_file, tmp_path
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # ─── REPRODUÇÃO ───────────────────────────────────────────────────────────

    def _play_bytes_mp3(self, audio_bytes: bytes):
        """Reproduz bytes MP3 com pygame."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        try:
            self._play_file(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _play_file(self, path: str):
        """Reproduz ficheiro de áudio com pygame."""
        try:
            import pygame
            import time

            if not self._mixer_init:
                pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=1024)
                pygame.mixer.init()
                self._mixer_init = True

            pygame.mixer.music.load(path)
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                if self._stop_flag:
                    pygame.mixer.music.stop()
                    break
                time.sleep(0.05)

            pygame.mixer.music.unload()
        except Exception as e:
            logger.error(f"Erro ao reproduzir: {e}")

    # ─── STT ──────────────────────────────────────────────────────────────────

    async def listen(self, duration_seconds: int | None = None) -> str | None:
        try:
            audio = await asyncio.get_event_loop().run_in_executor(
                None, self._record_audio, duration_seconds or self.listen_seconds
            )
            if audio:
                return await asyncio.get_event_loop().run_in_executor(
                    None, self._transcribe, audio
                )
        except Exception as e:
            logger.error(f"STT erro: {e}")
        return None

    def _record_audio(self, duration: int) -> bytes | None:
        """
        Grava até 'duration' segundos. Com stop_on_silence, pára assim que detecta
        ~1s de silêncio depois de o Simão começar a falar (menos latência).
        """
        try:
            import pyaudio
            CHUNK, FORMAT, CHANNELS, RATE = 1024, pyaudio.paInt16, 1, 16000
            SILENCE_RMS    = int(self.config.get("silence_threshold", 500))
            SILENCE_CHUNKS = int(RATE / CHUNK)     # ~1 segundo
            detect_silence = self.silence_stop and audioop is not None

            p = pyaudio.PyAudio()
            stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE,
                            input=True, frames_per_buffer=CHUNK)

            frames: list[bytes] = []
            silent_run = 0
            spoke      = False
            for _ in range(int(RATE / CHUNK * duration)):
                chunk = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(chunk)
                if not detect_silence:
                    continue
                if audioop.rms(chunk, 2) >= SILENCE_RMS:
                    spoke, silent_run = True, 0
                elif spoke:
                    silent_run += 1
                    if silent_run >= SILENCE_CHUNKS:
                        break

            stream.stop_stream(); stream.close(); p.terminate()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(p.get_sample_size(FORMAT))
                wf.setframerate(RATE)
                wf.writeframes(b"".join(frames))
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Erro a gravar: {e}")
            return None

    def _transcribe(self, audio_bytes: bytes) -> str | None:
        if self.stt_provider in ("auto", "faster_whisper"):
            text = self._transcribe_whisper(audio_bytes)
            if text is not None:
                return text
            if self.stt_provider == "faster_whisper":
                return None
        return self._transcribe_google(audio_bytes)

    def _transcribe_whisper(self, audio_bytes: bytes) -> str | None:
        """STT local com faster-whisper (sem rede, sem enviar áudio para terceiros)."""
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.debug("faster-whisper não instalado — a usar STT online.")
            return None

        try:
            if self._whisper is None:
                logger.info(f"A carregar faster-whisper '{self.whisper_model}' ({self.whisper_compute})...")
                self._whisper = WhisperModel(
                    self.whisper_model,
                    device=self.config.get("whisper_device", "cpu"),
                    compute_type=self.whisper_compute,
                )

            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            try:
                segments, _info = self._whisper.transcribe(
                    tmp_path, language="pt", vad_filter=True, beam_size=1
                )
                text = " ".join(seg.text.strip() for seg in segments).strip()
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

            logger.info(f"STT (faster-whisper): '{text}'")
            return text or None
        except Exception as e:
            logger.warning(f"faster-whisper falhou: {e}")
            return None

    def _transcribe_google(self, audio_bytes: bytes) -> str | None:
        try:
            import speech_recognition as sr
            r = sr.Recognizer()
            with sr.AudioFile(io.BytesIO(audio_bytes)) as src:
                audio = r.record(src)
            text = r.recognize_google(audio, language="pt-PT")
            logger.info(f"STT (google): '{text}'")
            return text
        except Exception as e:
            logger.warning(f"STT falhou: {e}")
            return None


# ─── Limpeza de texto ─────────────────────────────────────────────────────────

def _clean_for_tts(text: str) -> str:
    text = re.sub(r'```[\s\S]*?```', 'bloco de código.', text)
    text = re.sub(r'`[^`]+`', '', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'[^\w\s\.,!?;:\-áéíóúàâêôãõüçÁÉÍÓÚÀÂÊÔÃÕÜÇ]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # Limita a 500 chars para TTS (evita custos excessivos e demoras)
    if len(text) > 500:
        # Corta na última frase completa
        sentences = text[:500].rsplit('.', 1)
        text = sentences[0] + '.' if len(sentences) > 1 else text[:500]
    return text
