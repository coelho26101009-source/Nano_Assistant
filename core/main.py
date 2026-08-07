"""H.E.L.I.O.S. — Main Entry Point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys
import threading
from pathlib import Path

import eel

from core.app_paths import FRONTEND_DIR, PLUGINS_DIR, ROOT
from core.brain import Brain
from core.config import load_config
from core.guardrails import GuardrailsEngine
from core.plugin_loader import load_all_plugins
from core.memory import get_memory
from core.logger import setup_logger
from core.voice import VoiceEngine
from core.wake_word import WakeWordEngine

sys.path.insert(0, str(ROOT))
setup_logger()
logger = logging.getLogger("helios.main")

CONFIG = load_config()
guardrails = GuardrailsEngine()
memory = get_memory()
voice = VoiceEngine(CONFIG.get("voice", {}))
brain = Brain(
    api_key=os.getenv("GROQ_API_KEY", ""),
    guardrails=guardrails,
    memory=memory,
    config=CONFIG,
)
brain.load_history()
load_all_plugins(PLUGINS_DIR)

wake_word: WakeWordEngine | None = None
eel.init(str(FRONTEND_DIR) if FRONTEND_DIR.exists() else str(ROOT / "web"))


@eel.expose
def send_message(user_text: str):
    if not user_text or not user_text.strip():
        return {"error": "Mensagem vazia"}
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_process_message(user_text))
    except Exception as exc:
        logger.error("Erro ao processar mensagem", exc_info=True)
        return {"error": str(exc), "text": "Ups, ocorreu um erro ao processar o pedido."}
    finally:
        loop.close()


@eel.expose
def confirm_action(request_id: str, confirmed: bool):
    guardrails.resolve_confirmation(request_id, confirmed)
    return {"ok": True}


@eel.expose
def start_voice_listen():
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return {"text": loop.run_until_complete(voice.listen()) or ""}
    except Exception as exc:
        logger.error("Erro de voz", exc_info=True)
        return {"error": str(exc)}
    finally:
        loop.close()


@eel.expose
def get_conversation_history():
    return memory.get_recent_messages(limit=50)


@eel.expose
def clear_conversation():
    brain.reset_conversation()
    return {"ok": True}


@eel.expose
def get_loaded_plugins():
    from core.plugin_loader import list_plugins
    return list_plugins()


@eel.expose
def get_wake_word_status():
    return wake_word.status() if wake_word else {"enabled": False, "running": False}


@eel.expose
def get_memory_facts():
    return memory.get_facts()


def _notify_ui(user_text: str, assistant_text: str):
    try:
        eel.on_voice_exchange(user_text, assistant_text)()
    except Exception:
        pass


def _on_wake_word():
    if wake_word:
        wake_word.pause()
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        user_text = loop.run_until_complete(voice.listen())
        if user_text and user_text.strip():
            result = loop.run_until_complete(_process_message(user_text, blocking_tts=True))
            _notify_ui(user_text, result.get("text", ""))
    except Exception:
        logger.error("Erro no fluxo wake-word", exc_info=True)
    finally:
        loop.close()
        if wake_word:
            wake_word.resume()


def _start_wake_word():
    global wake_word
    voice_cfg = CONFIG.get("voice", {}) or {}
    if not voice_cfg.get("wake_word_enabled", False):
        return
    wake_word = WakeWordEngine(voice_cfg, on_wake=_on_wake_word)
    wake_word.start()


async def _process_message(user_text: str, blocking_tts: bool = False) -> dict:
    try:
        memory.save_message("user", user_text)
        full_response = ""
        status_updates: list[str] = []
        async for token in brain.chat(user_text, stream=False):
            if token.startswith("_thinking_:"):
                status_updates.append(token.removeprefix("_thinking_:"))
            else:
                full_response += token

        full_response = full_response.strip() or "Desculpa Simão, não consegui gerar uma resposta."
        memory.save_message("assistant", full_response)

        if CONFIG.get("voice", {}).get("tts_enabled", False):
            if blocking_tts:
                await voice.speak(full_response)
            else:
                threading.Thread(target=lambda: asyncio.run(voice.speak(full_response)), daemon=True).start()

        return {"text": full_response, "status": status_updates, "ok": True}
    except Exception as exc:
        logger.error("Erro no _process_message", exc_info=True)
        return {"text": "Ups Simão, ocorreu um erro ao processar o pedido.", "ok": False, "error": str(exc)}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _parse_args():
    parser = argparse.ArgumentParser(description="H.E.L.I.O.S.")
    parser.add_argument("--mode", default=os.getenv("HELIOS_MODE", "electron"))
    parser.add_argument("--port", type=int, default=0)
    return parser.parse_known_args()[0]


def main():
    args = _parse_args()
    logger.info("HELIOS V8 a iniciar")
    logger.info("Frontend: %s", FRONTEND_DIR)
    logger.info("Groq: %s", "configurado" if os.getenv("GROQ_API_KEY") else "não configurado")

    _start_wake_word()
    ui_cfg = CONFIG.get("ui", {}) or {}
    port = args.port or int(ui_cfg.get("port", 0) or 0) or _free_port()
    size = (int(ui_cfg.get("width", 1440)), int(ui_cfg.get("height", 900)))
    print(f"HELIOS_PORT={port}", flush=True)

    modes = [None] if args.mode == "electron" else [args.mode, "default"]
    for mode in modes:
        try:
            eel.start("index.html", mode=mode, size=size, port=port, block=True)
            return
        except (SystemExit, KeyboardInterrupt):
            return
        except Exception:
            logger.warning("Falha a iniciar UI em modo %s", mode, exc_info=True)
    raise RuntimeError("Impossível iniciar a UI do HELIOS")


if __name__ == "__main__":
    main()
