"""Voice in the person's language: transcriber, voice and wake phrase.

THE PROBLEM
-----------
The app follows the system language (English by default, Portuguese second),
but voice was Portuguese only: the transcriber was forced to ``pt``, the voice
was ``pt-PT-DuarteNeural`` and the wake phrase was "ei nano". An English
speaker saying "open Spotify" was transcribed as Portuguese and answered in a
Portuguese voice.

WHAT THIS DOES
--------------
One profile per interface language. :func:`apply` puts the profile's values
into ``CONFIG["voice"]`` for every setting the person has NOT changed, so a
custom voice or a custom wake phrase is kept, and everything else follows the
language. It runs at start and whenever the language changes in Settings; the
voice providers read these values on every use, so the change is live.

The transcriber stays FORCED to one language (auto-detection was measured
and is markedly worse, see core/config.py); it is just forced to the right
one. The same multilingual Whisper model serves both, so nothing is
downloaded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VoiceProfile:
    stt_language: str
    tts_voice: str
    wake_phrase: str
    vocabulary_hint: str


PROFILES: dict[str, VoiceProfile] = {
    "en": VoiceProfile(
        stt_language="en",
        # British and calm, the JARVIS manner. Was en-US-GuyNeural, an older
        # and flatter voice; an install still on it moves (_LEGACY_DEFAULTS).
        tts_voice="en-GB-RyanNeural",
        wake_phrase="hey nano",
        vocabulary_hint=("Vocabulary: Nano, Spotify, Discord, GitHub, Visual Studio Code, "
                         "VS Code, Windows, Groq, Ollama, Claude."),
    ),
    "pt-PT": VoiceProfile(
        stt_language="pt",
        tts_voice="pt-PT-DuarteNeural",
        wake_phrase="ei nano",
        # Pinned by tests/test_stt_production.py: the benchmark measured this one.
        vocabulary_hint=("Vocabulário: Nano, Spotify, Discord, GitHub, Visual Studio Code, "
                         "VS Code, Windows, Groq, Ollama, Claude."),
    ),
}

#: Older spellings that also count as "never changed by the person".
_LEGACY_DEFAULTS = {"stt_language": {"pt-PT", "en-US", "en-GB"},
                    "tts_voice": {"en-US-GuyNeural"}}


def profile(language: str | None) -> VoiceProfile:
    return PROFILES.get(str(language or ""), PROFILES["en"])


def _is_default(field: str, value: Any) -> bool:
    if value is None or str(value).strip() == "":
        return True
    known = {getattr(p, field) for p in PROFILES.values()} | _LEGACY_DEFAULTS.get(field, set())
    return str(value).strip() in known


def apply(voice_cfg: dict, language: str | None) -> dict[str, str]:
    """Point the untouched voice settings at ``language``. Returns what changed.

    Mutates ``voice_cfg`` (and its ``stt`` / ``tts`` sub-dicts) IN PLACE: the
    voice providers hold references to those same dicts.
    """
    target = profile(language)
    stt = voice_cfg.setdefault("stt", {})
    tts = voice_cfg.setdefault("tts", {})
    changed: dict[str, str] = {}

    def put(container: dict, key: str, field: str) -> None:
        value = getattr(target, field)
        if _is_default(field, container.get(key)) and container.get(key) != value:
            container[key] = value
            changed[field] = value

    put(stt, "language", "stt_language")
    put(stt, "vocabulary_hint", "vocabulary_hint")
    put(tts, "voice", "tts_voice")
    put(voice_cfg, "wake_phrase", "wake_phrase")
    return changed


__all__ = ["PROFILES", "VoiceProfile", "apply", "profile"]
