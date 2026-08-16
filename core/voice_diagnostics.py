from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import platform
import sys
from typing import Any, Iterable

from core.config import load_config
from core.local_runtime import choose_model, model_available, ollama_available
from core.model_router import ModelRequest, ModelRouter, PrivacyLevel, TaskType
from core.voice import LocalSTTProvider, LocalTTSProvider, LocalWakeWordProvider
from core.wake_word import WakeWordEngine, resolve_wake_word_model_path, validate_wake_word_model


READY = "READY FOR LIVE TEST"
SETUP_REQUIRED = "SETUP REQUIRED"
NOT_AVAILABLE = "NOT AVAILABLE"
ARCH_READY = "ARCHITECTURE READY"
LIVE_PROVIDER_NOT_CONFIGURED = "LIVE PROVIDER NOT CONFIGURED"


def _console_symbol(ok: bool, *, warn: bool = False) -> str:
    encoding = (sys.stdout.encoding or "utf-8").lower()
    if "utf" in encoding or "utf-8" in encoding:
        if warn:
            return "⚠"
        return "✅" if ok else "❌"
    if warn:
        return "!"
    return "OK" if ok else "FAIL"


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _describe_python() -> dict[str, Any]:
    return {
        "label": "Python",
        "ok": True,
        "status": "OK",
        "detail": f"{platform.python_version()} on {platform.system()} {platform.release()} ({platform.machine()})",
    }


def _summarize_devices(inputs: Iterable[dict[str, Any]], outputs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    input_devices = list(inputs)
    output_devices = list(outputs)
    return {
        "inputs": input_devices,
        "outputs": output_devices,
        "input_ok": bool(input_devices),
        "output_ok": bool(output_devices),
        "message": (
            "Audio input device detected."
            if input_devices
            else "No audio input device was detected. Check Windows Settings → System → Sound → Input."
        ),
    }


def _detect_pyaudio_devices() -> dict[str, Any]:
    if not _has_module("pyaudio"):
        return {
            "ok": False,
            "status": "MISSING",
            "error": "PyAudio is not installed.",
            "install": "pip install PyAudio",
            "inputs": [],
            "outputs": [],
        }
    try:
        import pyaudio

        pa = pyaudio.PyAudio()
        devices: list[dict[str, Any]] = []
        for idx in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(idx)
            devices.append({
                "index": idx,
                "name": info.get("name", f"device-{idx}"),
                "max_input_channels": int(info.get("maxInputChannels") or 0),
                "max_output_channels": int(info.get("maxOutputChannels") or 0),
                "default_sample_rate": int(info.get("defaultSampleRate") or 0),
            })
        pa.terminate()
        return {
            "ok": True,
            "status": "OK",
            "inputs": [d for d in devices if d["max_input_channels"] > 0],
            "outputs": [d for d in devices if d["max_output_channels"] > 0],
            "detail": "PyAudio is installed and enumerated audio devices successfully.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "ERROR",
            "error": "PyAudio is installed but not usable.",
            "detail": str(exc),
            "install": "pip install PyAudio",
            "inputs": [],
            "outputs": [],
        }


def _validate_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    voice_cfg = cfg.get("voice") or {}
    errors: list[str] = []
    if not isinstance(voice_cfg, dict):
        return {"ok": False, "status": "INVALID", "errors": ["voice configuration is missing"], "config": cfg}
    if not voice_cfg.get("enabled") and not cfg.get("wake_word_enabled"):
        errors.append("Voice is disabled in config.")
    wake_cfg = voice_cfg.get("wake_word") or {}
    if not isinstance(wake_cfg, dict):
        wake_cfg = {}
    if wake_cfg.get("enabled") or cfg.get("wake_word_enabled"):
        if not isinstance(wake_cfg, dict):
            errors.append("Wake word section is invalid.")
    if not voice_cfg.get("microphone"):
        errors.append("Microphone configuration is missing.")
    if not voice_cfg.get("tts"):
        errors.append("TTS configuration is missing.")
    if not voice_cfg.get("stt"):
        errors.append("STT configuration is missing.")
    return {"ok": not errors, "status": "OK" if not errors else "INVALID", "errors": errors, "config": voice_cfg}


def _check_stt() -> dict[str, Any]:
    cfg = load_config().get("voice") or {}
    stt_cfg = cfg.get("stt") or {}
    provider = str(stt_cfg.get("provider") or "local").lower()
    has_faster = _has_module("faster_whisper")
    has_sr = _has_module("speech_recognition")
    if provider in {"local", "faster_whisper"}:
        if not has_faster:
            return {
                "ok": False,
                "status": SETUP_REQUIRED,
                "provider": provider,
                "reason": "Required package is not installed.",
                "install": "pip install faster-whisper",
            }
        return {
            "ok": True,
            "status": "OK",
            "provider": provider,
            "detail": "Local STT is available and ready for initialization.",
        }
    if provider == "google":
        if not has_sr:
            return {
                "ok": False,
                "status": SETUP_REQUIRED,
                "provider": provider,
                "reason": "speech_recognition is not installed.",
                "install": "pip install SpeechRecognition",
            }
        return {"ok": True, "status": "OK", "provider": provider, "detail": "Google STT dependency is available."}
    return {"ok": False, "status": SETUP_REQUIRED, "provider": provider, "reason": "Unknown STT provider selected."}


def _check_tts() -> dict[str, Any]:
    cfg = load_config().get("voice") or {}
    tts_cfg = cfg.get("tts") or {}
    provider = str(tts_cfg.get("provider") or "local").lower()
    has_edge = _has_module("edge_tts")
    if provider in {"local", "edge"}:
        if not has_edge:
            return {
                "ok": False,
                "status": SETUP_REQUIRED,
                "provider": provider,
                "reason": "Local TTS dependency is not installed.",
                "install": "pip install edge-tts",
            }
        return {"ok": True, "status": "OK", "provider": provider, "detail": "Local TTS is available."}
    return {"ok": False, "status": SETUP_REQUIRED, "provider": provider, "reason": "Unsupported TTS provider selected."}


def _check_wake_word() -> dict[str, Any]:
    cfg = load_config()
    voice_cfg = cfg.get("voice") or {}
    wake_cfg = voice_cfg.get("wake_word") or {}
    enabled = bool(wake_cfg.get("enabled", cfg.get("wake_word_enabled", False)))
    if not enabled:
        return {"ok": False, "status": "DISABLED", "provider": "none", "detail": "Wake word is disabled in config."}

    provider = str(wake_cfg.get("provider") or "openwakeword").lower()
    phrase = str(wake_cfg.get("phrase") or "Nano").strip()
    model_path = resolve_wake_word_model_path(wake_cfg)

    if provider in {"openwakeword", "open_wake_word", "local"}:
        if not _has_module("openwakeword"):
            return {
                "ok": False,
                "status": "SETUP REQUIRED",
                "provider": "openWakeWord",
                "phrase": phrase,
                "model": "NOT FOUND",
                "required": "custom wake-word model",
                "detail": "Wake word provider is installed as an architecture, but the runtime dependency is missing.",
            }
        validation = validate_wake_word_model(model_path, phrase=phrase, provider=provider)
        if not validation["ok"]:
            return {
                "ok": False,
                "status": "SETUP REQUIRED",
                "provider": "openWakeWord",
                "phrase": phrase,
                "model": validation.get("model", "NOT FOUND"),
                "required": "custom wake-word model",
                "reason": validation.get("reason", "custom wake-word model required"),
                "detail": validation.get("path", "") or "No custom model configured for the selected phrase.",
            }
        return {
            "ok": True,
            "status": "READY",
            "provider": "openWakeWord",
            "phrase": phrase,
            "model": "FOUND",
            "detail": "Live wake-word model is present and loadable.",
        }

    if provider == "porcupine":
        if not _has_module("pvporcupine"):
            return {
                "ok": False,
                "status": "SETUP REQUIRED",
                "provider": "Porcupine",
                "phrase": phrase,
                "model": "NOT FOUND",
                "required": "custom wake-word model or access key",
                "detail": "Picovoice runtime is required for Porcupine.",
            }
        return {"ok": True, "status": "READY", "provider": "Porcupine", "phrase": phrase, "model": "FOUND", "detail": "Porcupine is ready."}

    return {
        "ok": False,
        "status": ARCH_READY,
        "provider": provider,
        "phrase": phrase,
        "model": "NOT FOUND",
        "required": "custom wake-word model",
        "detail": LIVE_PROVIDER_NOT_CONFIGURED,
    }


async def _check_ollama() -> dict[str, Any]:
    cfg = load_config()
    local_cfg = cfg.get("local") or {}
    base_url = str(local_cfg.get("url") or "http://127.0.0.1:11434")
    try:
        available = await ollama_available(base_url)
    except Exception as exc:
        return {"ok": False, "status": "OFFLINE", "detail": f"Ollama health check failed: {exc}", "models": []}
    if not available:
        return {"ok": False, "status": "OFFLINE", "detail": "Ollama endpoint is not reachable.", "models": []}
    try:
        async with __import__("httpx").AsyncClient(timeout=3.0) as client:
            response = await client.get(base_url.rstrip("/") + "/api/tags")
            payload = response.json() if response.is_success else {}
            models = [str(item.get("name") or "") for item in payload.get("models", []) if item.get("name")]
            if not models:
                return {"ok": True, "status": "ONLINE", "detail": "Ollama is reachable but no models are installed.", "models": []}
            return {"ok": True, "status": "ONLINE", "detail": "Ollama is reachable.", "models": models}
    except Exception as exc:
        return {"ok": False, "status": "OFFLINE", "detail": f"Ollama is reachable but the tag lookup failed: {exc}", "models": []}


def _check_model_router() -> dict[str, Any]:
    cfg = load_config()
    router = ModelRouter(cfg)
    models = router.models()
    if not models:
        return {"ok": False, "status": "NO_MODELS", "detail": "No model metadata was discovered for the router."}
    chat = router.select({
        "task_type": TaskType.CHAT,
        "privacy_level": PrivacyLevel.NORMAL,
        "requires_tools": False,
        "requires_reasoning": False,
        "context_size": 2048,
        "latency_preference": "fast",
    })
    tools = router.select({
        "task_type": TaskType.TOOL_USE,
        "privacy_level": PrivacyLevel.NORMAL,
        "requires_tools": True,
        "requires_reasoning": True,
        "context_size": 2048,
        "latency_preference": "balanced",
    })
    coding = router.select({
        "task_type": TaskType.CODING,
        "privacy_level": PrivacyLevel.NORMAL,
        "requires_coding": True,
        "requires_tools": True,
        "context_size": 2048,
        "latency_preference": "balanced",
    })
    selected = [chat, tools, coding]
    compatible = all(item.get("model") is not None for item in selected)
    return {
        "ok": compatible,
        "status": "OK" if compatible else "NO_COMPATIBLE_MODEL",
        "detail": "Model router has at least one candidate for chat/tool/coding requests." if compatible else "Model router did not find compatible candidates for the required task types.",
        "results": selected,
    }


def _build_input_summary() -> dict[str, Any]:
    pyaudio_info = _detect_pyaudio_devices()
    if not pyaudio_info["ok"]:
        return {
            "ok": False,
            "status": "MISSING",
            "reason": pyaudio_info.get("error") or "PyAudio is not installed.",
            "detail": pyaudio_info.get("install", "pip install PyAudio"),
            "devices": [],
        }
    devices = pyaudio_info["inputs"]
    if not devices:
        return {
            "ok": False,
            "status": "NOT_DETECTED",
            "reason": "No audio input device was detected.",
            "detail": "Check Windows Settings → System → Sound → Input.",
            "devices": [],
        }
    return {
        "ok": True,
        "status": "OK",
        "reason": "A microphone is available.",
        "devices": devices,
    }


def _build_output_summary() -> dict[str, Any]:
    pyaudio_info = _detect_pyaudio_devices()
    if not pyaudio_info["ok"]:
        return {"ok": False, "status": "MISSING", "reason": pyaudio_info.get("error") or "PyAudio is not installed.", "devices": []}
    devices = pyaudio_info["outputs"]
    if not devices:
        return {"ok": False, "status": "NOT_DETECTED", "reason": "No audio output device was detected.", "devices": []}
    return {"ok": True, "status": "OK", "reason": "An audio output device is available.", "devices": devices}


def run_diagnostics(config: dict[str, Any] | None = None, *, include_live_tests: bool = False) -> dict[str, Any]:
    cfg = config or load_config()
    python_check = _describe_python()
    config_check = _validate_config(cfg)
    pyaudio_check = _detect_pyaudio_devices()
    input_check = _build_input_summary()
    output_check = _build_output_summary()
    stt_check = _check_stt()
    tts_check = _check_tts()
    wake_check = _check_wake_word()

    async def _ollama_check():
        return await _check_ollama()

    ollama_check = asyncio.run(_ollama_check())
    model_router_check = _check_model_router()
    overall = [
        python_check["ok"],
        pyaudio_check["ok"],
        input_check["ok"],
        output_check["ok"],
        stt_check["ok"],
        tts_check["ok"],
        wake_check["ok"],
        ollama_check["ok"],
        model_router_check["ok"],
    ]
    ready = all(overall)
    voice_status = READY if ready else ("SETUP REQUIRED" if input_check["ok"] and pyaudio_check["ok"] else NOT_AVAILABLE)

    return {
        "python": python_check,
        "config": config_check,
        "pyaudio": pyaudio_check,
        "audio_input": input_check,
        "audio_output": output_check,
        "stt": stt_check,
        "tts": tts_check,
        "wake_word": wake_check,
        "ollama": ollama_check,
        "model_router": model_router_check,
        "voice_system": voice_status,
        "ready_for_live_test": ready,
        "include_live_tests": include_live_tests,
        "notes": [
            "This is a software-level diagnosis and not a claim that hardware is live-tested.",
            "Live microphone, STT, and TTS results must be confirmed on the target Windows PC.",
        ],
    }


def _print_report(report: dict[str, Any]) -> None:
    print("NANO VOICE DIAGNOSTICS")
    print("=" * 30)
    print(f"Python             {_console_symbol(report['python']['ok'])} {report['python']['detail']}")
    print(f"Audio input        {_console_symbol(report['audio_input']['ok'])} {report['audio_input'].get('reason', report['audio_input'].get('status', ''))}")
    print(f"Microphone         {_console_symbol(report['audio_input']['ok'])} {report['audio_input'].get('detail', report['audio_input'].get('reason', ''))}")
    print(f"Audio output       {_console_symbol(report['audio_output']['ok'])} {report['audio_output'].get('reason', report['audio_output'].get('status', ''))}")
    print(f"PyAudio            {_console_symbol(report['pyaudio']['ok'])} {report['pyaudio'].get('detail') or report['pyaudio'].get('error') or report['pyaudio'].get('status')}")
    print(f"STT provider       {_console_symbol(report['stt']['ok'])} {report['stt'].get('detail') or report['stt'].get('reason') or report['stt'].get('provider')}")
    print(f"TTS provider       {_console_symbol(report['tts']['ok'])} {report['tts'].get('detail') or report['tts'].get('reason') or report['tts'].get('provider')}")
    print(f"Wake word          {_console_symbol(report['wake_word']['ok'])} {report['wake_word'].get('detail') or report['wake_word'].get('reason') or report['wake_word'].get('provider')}")
    print(f"Ollama             {_console_symbol(report['ollama']['ok'])} {report['ollama'].get('detail') or report['ollama'].get('status')}")
    print(f"Model available    {_console_symbol(report['model_router']['ok'])} {report['model_router'].get('detail') or report['model_router'].get('status')}")
    print()
    print(f"Voice system: {report['voice_system']}")
    if report['voice_system'] == NOT_AVAILABLE:
        print("Hardware live validation: NOT AVAILABLE")
    if not report['ready_for_live_test']:
        print("Missing setup: consult the detailed reasons above and fix the failing checks before live testing.")


def main(argv: list[str] | None = None) -> int:
    del argv
    report = run_diagnostics()
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
