"""Diagnostic for the "Hey Nano" wake phrase. Run it, speak, see what happens.

    python -m core.wake_phrase_debug

It walks the exact runtime path the real engine uses -- config, microphone,
local STT, phrase matching -- and prints the result of each stage, so a failure
can be pinned to the mic, the transcription, or the phrase match instead of
being guessed at.

SAFETY: this tool only listens and prints. It never calls the brain, never
resolves a capability, and never executes a tool. The wake callback here just
prints a line.
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
import time


def _ok(flag: bool) -> str:
    return "OK  " if flag else "FAIL"


def _print_header(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def run_checks() -> tuple[bool, object | None]:
    """Static checks. Returns (ready, VoiceEngine or None)."""
    _print_header("1. INTERPRETER")
    print(f"  python      : {sys.version.split()[0]}")
    print(f"  executable  : {sys.executable}")

    _print_header("2. DEPENDENCIES (must be in THIS interpreter)")
    deps = {}
    for module in ("pyaudio", "faster_whisper", "numpy", "onnxruntime"):
        found = importlib.util.find_spec(module) is not None
        deps[module] = found
        print(f"  [{_ok(found)}] {module}")
    if not deps["pyaudio"]:
        print("\n  -> pip install PyAudio")
    if not deps["faster_whisper"]:
        print("\n  -> pip install faster-whisper")

    _print_header("3. CONFIG")
    from core.config import CONFIG_PATH, load_config

    cfg = load_config()
    voice_cfg = cfg.get("voice", {}) or {}
    print(f"  settings.yaml       : {CONFIG_PATH}")
    print(f"  exists              : {CONFIG_PATH.exists()}")
    print(f"  voice.enabled       : {voice_cfg.get('enabled')}")
    print(f"  wake_phrase_enabled : {voice_cfg.get('wake_phrase_enabled')}")
    print(f"  wake_phrase         : {voice_cfg.get('wake_phrase')!r}")
    print(f"  allow_nano_only     : {voice_cfg.get('wake_phrase_allow_nano_only')}")
    print(f"  cooldown_seconds    : {voice_cfg.get('wake_phrase_cooldown_seconds')}")
    print(f"  chunk_seconds       : {voice_cfg.get('wake_phrase_chunk_seconds')}")
    print(f"  stt model / language: {(voice_cfg.get('stt') or {}).get('model')} / {(voice_cfg.get('stt') or {}).get('language')}")

    if not voice_cfg.get("enabled"):
        print("\n  !! voice.enabled is false -> the wake phrase will never start.")
    if not voice_cfg.get("wake_phrase_enabled"):
        print("\n  !! wake_phrase_enabled is false -> the wake phrase will never start.")

    _print_header("4. MICROPHONE")
    if deps["pyaudio"]:
        import pyaudio

        pa = pyaudio.PyAudio()
        try:
            default = pa.get_default_input_device_info()
            print(f"  default input : [{default['index']}] {default['name']}")
            print(f"  native rate   : {int(default['defaultSampleRate'])} Hz "
                  f"(Nano captures at 16000 Hz mono)")
        except Exception as exc:
            print(f"  !! no default input device: {exc}")
        print("  available inputs:")
        for index in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(index)
            if info.get("maxInputChannels", 0) > 0:
                print(f"    [{index}] {str(info['name'])[:56]}")
        pa.terminate()
    else:
        print("  skipped (pyaudio missing)")

    _print_header("5. VOICE ENGINE / PROVIDERS")
    from core.voice import VoiceEngine

    engine = VoiceEngine(voice_cfg)
    provider = engine.wake_phrase_provider
    inner = provider._engine

    print(f"  [{_ok(inner._audio_available())}] microphone provider available")
    print(f"  [{_ok(inner._stt_available())}] local STT provider available")
    print(f"  [{_ok(engine.input_provider is inner._audio)}] microphone shared with VoiceEngine (one instance)")
    print(f"  [{_ok(engine.stt_provider is inner._stt)}] STT shared with VoiceEngine (one model in RAM)")

    status = provider.status()
    print(f"  readiness   : {status['readiness']}")
    print(f"  phrase      : {status['phrase']!r}")
    if status.get("error"):
        print(f"  error       : {status['error']}")

    ready = status["readiness"] in {"READY", "LISTENING"}
    return ready, engine


def listen(engine, seconds: float) -> None:
    """Run the real engine and narrate every stage until the timer expires."""
    provider = engine.wake_phrase_provider
    inner = provider._engine
    detections: list[str] = []

    _print_header(f"6. LIVE LISTEN ({seconds:.0f}s)")
    print(f'  Say "{inner.phrase}" into the microphone.')
    if inner.allow_nano_only:
        print('  (plain "nano" also triggers)')
    print("  Every transcription is printed, matched or not.\n")

    def on_wake(transcript: str) -> None:
        # Deliberately inert: print only. No brain, no tools, no actions.
        detections.append(transcript)
        print(f"  >>> WAKE FIRED  transcript={transcript!r}\n")

    if not provider.start(on_wake):
        print(f"  !! engine did not start: {inner.last_error or 'unknown reason'}")
        return

    seen = 0
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            time.sleep(0.25)
            if inner.transcripts_seen > seen:
                seen = inner.transcripts_seen
                text = inner.last_transcript or ""
                from core.wake_phrase import normalize_transcript

                normalized = normalize_transcript(text)
                matched = inner.detector.matches(text)
                mark = "MATCH  " if matched else "no match"
                print(f"  [{mark}] heard={text!r} -> normalized={normalized!r}")
    except KeyboardInterrupt:
        print("\n  interrupted")
    finally:
        provider.stop()
        time.sleep(0.3)

    _print_header("7. RESULT")
    print(f"  audio chunks captured : {inner.chunks_captured}")
    print(f"  transcripts produced  : {inner.transcripts_seen}")
    print(f"  wake events fired     : {len(detections)}")
    print()
    if inner.chunks_captured == 0:
        print("  DIAGNOSIS: the microphone produced nothing. Check the input")
        print("  device, Windows mic privacy settings, and that no other app")
        print("  holds the microphone exclusively.")
    elif inner.transcripts_seen == 0:
        print("  DIAGNOSIS: audio was captured but the local STT found no speech.")
        print("  Speak louder/closer, check you are using the right input device,")
        print("  and confirm the mic is not muted in Windows.")
    elif not detections:
        print("  DIAGNOSIS: speech WAS transcribed but never matched the phrase.")
        print("  Compare the transcripts above with the configured phrase; adjust")
        print("  voice.wake_phrase in config/settings.yaml if the transcription is")
        print("  consistently different.")
    else:
        print("  DIAGNOSIS: wake phrase detection is working end to end.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Diagnose "Hey Nano" wake phrase detection.')
    parser.add_argument("--seconds", type=float, default=20.0, help="how long to listen (default 20)")
    parser.add_argument("--checks-only", action="store_true", help="run checks, do not listen")
    parser.add_argument("--verbose", action="store_true", help="show engine DEBUG logs")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.verbose:
        # Keep the noise to our own module.
        for noisy in ("httpcore", "httpx", "urllib3", "faster_whisper", "asyncio", "filelock"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    print("=" * 62)
    print(" NANO — WAKE PHRASE DIAGNOSTIC")
    print("=" * 62)

    try:
        ready, engine = run_checks()
    except Exception as exc:  # pragma: no cover - diagnostic must never crash hard
        print(f"\n!! checks failed: {type(exc).__name__}: {exc}")
        return 2

    if args.checks_only:
        print()
        return 0 if ready else 1

    if not ready or engine is None:
        _print_header("6. LIVE LISTEN")
        print("  skipped: the checks above must pass first.")
        return 1

    listen(engine, args.seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
