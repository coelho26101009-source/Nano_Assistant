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
#
# This fixed value is only the fallback default. It is wrong for real hardware:
# on the measured machine the microphone's own noise floor is RMS 5-7 and a
# loud tone reaches only RMS 21, so a fixed 220 floor rejected 100% of chunks
# and the wake phrase could never fire. Prefer AdaptiveGate below, which
# derives the threshold from the microphone actually in use.
DEFAULT_SILENCE_RMS = 220.0

# Bounds for the derived threshold. The lower bound keeps a dead line from
# looking like speech; the upper bound keeps a noisy room from raising the bar
# so high that a normal voice can never clear it.
MIN_ADAPTIVE_THRESHOLD = 12.0
MAX_ADAPTIVE_THRESHOLD = 600.0

# Speech must stand this far above the measured noise floor. Chosen so the
# measured floor of ~6 yields a threshold of ~21, which the measured speaker
# tone already reached, while ordinary room tone does not.
NOISE_MULTIPLIER = 3.5

# Absolute floor added on top of the multiplier, so a perfectly silent digital
# line (RMS 0) still produces a usable, non-zero threshold.
NOISE_HEADROOM = 8.0

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


class AdaptiveGate:
    """Speech/silence decision calibrated against the microphone in use.

    A fixed RMS threshold cannot work across microphones: the same spoken
    sentence lands at RMS 3000 on a headset and RMS 30 on a far-field laptop
    input with low capture gain. Nano measured the latter, and a hard-coded
    floor of 220 silently rejected every chunk, so "Hey Nano" had a 0% success
    rate while the UI still reported "A ouvir".

    The gate observes the quietest recent chunks to estimate the noise floor,
    then requires speech to stand a fixed multiple above it, clamped to sane
    bounds. It is intentionally conservative: it only has to separate "someone
    is talking" from "room tone", not identify a voice.
    """

    # How many recent chunk energies to keep when estimating the floor.
    _WINDOW = 25

    # A chunk this far above the current floor is a sound event, not room tone,
    # so it must never be folded into the floor estimate. Without this the gate
    # ratchets itself deaf: speech that lands just under the threshold is
    # counted as "silence", raises the floor, raises the threshold, and makes
    # the next utterance even less likely to pass. Measured live climbing
    # 32.5 -> 39.5 -> 46.5 -> 60.5 while a test tone played.
    _OBSERVE_CEILING = 2.0

    # The running floor may drift this far from the value measured at startup,
    # in either direction. Enough to follow a fan switching on; not enough to
    # let a conversation walk the threshold out of reach.
    _MAX_DRIFT_UP = 2.0
    _MAX_DRIFT_DOWN = 0.25

    def __init__(self, *, multiplier: float = NOISE_MULTIPLIER,
                 headroom: float = NOISE_HEADROOM,
                 min_threshold: float = MIN_ADAPTIVE_THRESHOLD,
                 max_threshold: float = MAX_ADAPTIVE_THRESHOLD,
                 min_voiced_ratio: float = DEFAULT_MIN_VOICED_RATIO):
        self.multiplier = float(multiplier)
        self.headroom = float(headroom)
        self.min_threshold = float(min_threshold)
        self.max_threshold = float(max_threshold)
        self.min_voiced_ratio = float(min_voiced_ratio)
        self._energies: list[float] = []
        self._noise_floor: float | None = None
        # The floor measured at startup. It anchors the running estimate so a
        # noisy stretch can nudge the threshold but never walk it away.
        self._baseline: float | None = None
        self.calibrated = False
        # Counters the UI/diagnostics can read to tell "the mic is dead" from
        # "the mic works but nobody spoke".
        self.chunks_seen = 0
        self.speech_chunks = 0
        self.silent_chunks = 0
        self.last_rms = 0.0
        self.peak_rms = 0.0

    # ----------------------------------------------------------- calibration

    def observe(self, rms: float) -> None:
        """Feed one chunk's energy into the noise-floor estimate.

        Sound events are rejected rather than averaged in. A chunk far above
        the current floor is somebody talking, a door, a notification -- not
        the room's baseline -- and folding it in is what let the threshold
        ratchet upward until nothing could ever pass.
        """
        value = max(0.0, float(rms))
        current = self._noise_floor
        if current is not None and value > max(self.min_threshold, current * self._OBSERVE_CEILING):
            return
        self._energies.append(value)
        if len(self._energies) > self._WINDOW:
            self._energies.pop(0)
        self._recompute()

    def calibrate(self, samples: list[float]) -> float:
        """Seed the floor from a bounded ambient sample. Returns the threshold."""
        for sample in samples:
            value = max(0.0, float(sample))
            self._energies.append(value)
        self._energies = self._energies[-self._WINDOW:]
        self._recompute()
        # Anchor every later estimate to what the room measured at startup.
        self._baseline = self._noise_floor
        self.calibrated = bool(self._energies)
        return self.threshold

    def _recompute(self) -> None:
        if not self._energies:
            return
        # The floor is the low end of what has been heard, not the mean: the
        # mean rises while someone is speaking and would raise the bar out of
        # reach mid-conversation.
        ordered = sorted(self._energies)
        index = max(0, int(len(ordered) * 0.25) - 1) if len(ordered) >= 4 else 0
        floor = ordered[index]
        # Bounded drift around the startup measurement. Ambient conditions do
        # change, but the threshold must stay in the neighbourhood of the room
        # Nano actually calibrated in.
        if self._baseline is not None:
            ceiling = max(self.min_threshold, self._baseline * self._MAX_DRIFT_UP)
            floor = min(floor, ceiling)
            floor = max(floor, self._baseline * self._MAX_DRIFT_DOWN)
        self._noise_floor = floor

    @property
    def noise_floor(self) -> float:
        return float(self._noise_floor if self._noise_floor is not None else 0.0)

    @property
    def threshold(self) -> float:
        """Energy a chunk must reach to be considered speech."""
        derived = self.noise_floor * self.multiplier + self.headroom
        return max(self.min_threshold, min(self.max_threshold, derived))

    # ---------------------------------------------------------------- gating

    def has_speech(self, audio_bytes: bytes) -> bool:
        """True when this chunk plausibly contains speech, and learn from it."""
        if not audio_bytes:
            return False
        rms = rms_of_wav(audio_bytes)
        self.chunks_seen += 1
        self.last_rms = rms
        self.peak_rms = max(self.peak_rms, rms)

        threshold = self.threshold
        speaking = rms >= threshold and voiced_ratio(
            audio_bytes, silence_rms=threshold) >= self.min_voiced_ratio

        if speaking:
            self.speech_chunks += 1
        else:
            self.silent_chunks += 1
            # Only quiet chunks update the floor, so a long sentence cannot
            # drag the threshold up behind itself.
            self.observe(rms)
        return speaking

    def stats(self) -> dict:
        """Diagnostics for Voice/Advanced. Contains no audio and no transcript."""
        return {
            "calibrated": self.calibrated,
            "noise_floor": round(self.noise_floor, 1),
            "threshold": round(self.threshold, 1),
            "last_rms": round(self.last_rms, 1),
            "peak_rms": round(self.peak_rms, 1),
            "chunks_seen": self.chunks_seen,
            "speech_chunks": self.speech_chunks,
            "silent_chunks": self.silent_chunks,
        }

    def looks_dead(self, *, min_chunks: int = 8) -> bool:
        """True when enough audio was captured but none of it carried energy.

        This is what turns a cheerful "A ouvir" into an honest MIC_SILENT: the
        thread is alive and chunks are arriving, but the input is effectively a
        dead line, so no wake phrase can ever be detected.
        """
        if self.chunks_seen < min_chunks:
            return False
        if self.speech_chunks > 0:
            return False
        # A peak that never even reaches the minimum threshold means the signal
        # is indistinguishable from digital silence, not merely a quiet room.
        return self.peak_rms < self.min_threshold


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
    "AdaptiveGate",
    "DEFAULT_MIN_VOICED_RATIO",
    "DEFAULT_SILENCE_RMS",
    "MAX_ADAPTIVE_THRESHOLD",
    "MIN_ADAPTIVE_THRESHOLD",
    "NOISE_HEADROOM",
    "NOISE_MULTIPLIER",
    "describe",
    "has_speech_energy",
    "is_hallucination",
    "is_usable_command",
    "rms_of_wav",
    "voiced_ratio",
]
