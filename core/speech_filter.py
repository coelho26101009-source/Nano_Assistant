"""Reject silence and speech-to-text hallucinations before they reach the Brain.

Whisper does not return an empty string for silence. Given near-silent or noisy
audio it confidently emits stock filler — "Obrigado.", "Thank you.", "Legendas
pela comunidade Amara.org", a stray "Nano" — because those phrases are frequent
in its training data.

That produced two real bugs:

* A false wake. A hallucinated fragment containing "nano" tripped the detector
  while nobody was speaking.
* A phantom request. After a genuine wake, the user said nothing; the command
  window captured seven seconds of silence, Whisper invented a phrase, and Nano
  answered "Olá, como posso ajudá-lo?" to a question no one had asked.

Two independent gates fix this:

1. `has_speech_energy` runs on the raw PCM *before* transcription. Silence never
   reaches the model, so it cannot hallucinate in the first place. This is cheap
   (one pass of arithmetic) and is the primary defence.
2. `is_hallucination` runs on the transcript, catching whatever slips through.
"""
from __future__ import annotations

import array
import audioop
import logging
import math
import re
import wave
from io import BytesIO

logger = logging.getLogger("nano.speech_filter")

# RMS below this (16-bit signed, so full scale is 32768) is treated as silence.
# Typical room tone sits well under 100; speech at a normal distance is 800+.
DEFAULT_SILENCE_RMS = 220.0

# A chunk must contain at least this fraction of non-silent frames to count as
# speech, so a single door slam does not read as someone talking.
DEFAULT_MIN_VOICED_RATIO = 0.06

# Phrases Whisper emits when it has nothing real to transcribe. Matched against
# the fully normalized transcript, so punctuation and case do not matter.
_HALLUCINATION_PHRASES = frozenset({
    "obrigado", "obrigada", "obrigado.", "muito obrigado", "tchau", "adeus",
    "thank you", "thanks", "thank you very much", "bye", "bye bye", "you",
    "legendas pela comunidade amara org", "amara org", "www amara org",
    "legendas pela comunidade", "subtitles by the amara org community",
    "transcricao", "transcription", "subscribe", "like and subscribe",
    "ate a proxima", "ate breve", "fim", "the end", "silencio", "musica",
    "music", "applause", "aplausos", "risos", "laughter", "ok", "okay",
    "hmm", "mm", "mhm", "ah", "oh", "eh", "uh", "um", "yeah", "sim", "nao",
})

# Bracketed annotations Whisper adds for non-speech audio: [MUSIC], (applause).
_ANNOTATION_PATTERN = re.compile(r"^[\[\(\*].{0,40}[\]\)\*]$")

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    lowered = str(text or "").strip().lower()
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", lowered)).strip()


def rms_of_wav(audio_bytes: bytes) -> float:
    """Root-mean-square amplitude of a 16-bit mono WAV, or 0.0 if unreadable."""
    if not audio_bytes:
        return 0.0
    try:
        with wave.open(BytesIO(audio_bytes)) as handle:
            frames = handle.readframes(handle.getnframes())
            width = handle.getsampwidth()
        if not frames:
            return 0.0
        return float(audioop.rms(frames, width))
    except Exception as exc:
        logger.debug("could not measure audio energy: %s", exc)
        return 0.0


def voiced_ratio(audio_bytes: bytes, *, silence_rms: float = DEFAULT_SILENCE_RMS) -> float:
    """Fraction of ~30 ms windows whose energy is above the silence floor."""
    if not audio_bytes:
        return 0.0
    try:
        with wave.open(BytesIO(audio_bytes)) as handle:
            sample_rate = handle.getframerate()
            width = handle.getsampwidth()
            channels = handle.getnchannels()
            frames = handle.readframes(handle.getnframes())
        if not frames or width != 2:
            return 0.0

        window_frames = max(1, int(sample_rate * 0.03))
        window_bytes = window_frames * width * channels
        if window_bytes <= 0:
            return 0.0

        total = voiced = 0
        for start in range(0, len(frames) - window_bytes + 1, window_bytes):
            window = frames[start:start + window_bytes]
            total += 1
            if audioop.rms(window, width) >= silence_rms:
                voiced += 1
        return (voiced / total) if total else 0.0
    except Exception as exc:
        logger.debug("could not compute voiced ratio: %s", exc)
        return 0.0


def has_speech_energy(
    audio_bytes: bytes,
    *,
    silence_rms: float = DEFAULT_SILENCE_RMS,
    min_voiced_ratio: float = DEFAULT_MIN_VOICED_RATIO,
) -> bool:
    """True when the chunk plausibly contains speech.

    The primary defence: silence never reaches the transcriber, so it cannot
    hallucinate. Deliberately permissive — it only has to exclude near-silence,
    not judge whether the sound is actually a human voice.
    """
    if not audio_bytes:
        return False
    overall = rms_of_wav(audio_bytes)
    if overall < silence_rms * 0.5:
        return False
    return voiced_ratio(audio_bytes, silence_rms=silence_rms) >= min_voiced_ratio


def is_hallucination(text: str) -> bool:
    """True when a transcript looks like Whisper filler rather than a request."""
    normalized = _normalize(text)
    if not normalized:
        return True
    if _ANNOTATION_PATTERN.match(str(text).strip()):
        return True
    if normalized in _HALLUCINATION_PHRASES:
        return True
    # A single very short token carries no instruction and is almost always
    # noise ("ah", "hm", "you"). Two characters or fewer, no digits.
    if len(normalized) <= 2 and not any(ch.isdigit() for ch in normalized):
        return True
    # One repeated word, e.g. "obrigado obrigado obrigado".
    words = normalized.split()
    if len(words) >= 3 and len(set(words)) == 1:
        return True
    return False


def is_usable_command(text: str, *, min_words: int = 1) -> bool:
    """True when a transcript is worth sending to the Brain."""
    if is_hallucination(text):
        return False
    return len(_normalize(text).split()) >= min_words


def describe(audio_bytes: bytes) -> dict:
    """Diagnostics for the wake debug tool. No audio is retained."""
    return {
        "bytes": len(audio_bytes or b""),
        "rms": round(rms_of_wav(audio_bytes), 1),
        "voiced_ratio": round(voiced_ratio(audio_bytes), 3),
        "has_speech": has_speech_energy(audio_bytes),
    }


__all__ = [
    "DEFAULT_MIN_VOICED_RATIO",
    "DEFAULT_SILENCE_RMS",
    "describe",
    "has_speech_energy",
    "is_hallucination",
    "is_usable_command",
    "rms_of_wav",
    "voiced_ratio",
]
