"""
H.E.L.I.O.S. — Main Entry Point
"""

import argparse
import asyncio
import logging
import os
import socket
import sys
import threading
from pathlib import Path

import eel
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.brain import Brain
from core.config import load_config
from core.guardrails import GuardrailsEngine
from core.plugin_loader import load_all_plugins
from core.memory import get_memory
from core.logger import setup_logger
from core.voice import VoiceEngine
from core.wake_word import WakeWordEngine

setup_logger()
logger = logging.getLogger("helios.main")

CONFIG = load_config()

guardrails = GuardrailsEngine()
memory     = get_memory()
voice      = VoiceEngine(CONFIG.get("voice", {}))
brain      = Brain(
    api_key    = os.getenv("GROQ_API_KEY", ""),
    guardrails = guardrails,
    memory     = memory,
    config     = CONFIG,
)
brain.load_history()

load_all_plugins(Path(__file__).parent.parent / "plugins")

wake_word: WakeWordEngine | None = None

FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "out"
eel.init(str(FRONTEND_DIR) if FRONTEND_DIR.exists() else "web")


@eel.expose
def send_message(user_text: str):
    if not user_text or not user_text.strip():
        return {"error": "Mensagem vazia"}
    logger.info(f"Mensagem recebida: '{user_text[:80]}'")
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_process_message(user_text))
        return result
    except Exception as exc:
        logger.error(f"Erro ao processar: {exc}", exc_info=True)
        return {"error": str(exc), "text": f"Ups, ocorreu um erro: {exc}"}
    finally:
        try:
            loop.close()
        except Exception:
            pass


@eel.expose
def confirm_action(request_id: str, confirmed: bool):
    guardrails.resolve_confirmation(request_id, confirmed)
    return {"ok": True}


@eel.expose
def start_voice_listen():
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        text = loop.run_until_complete(voice.listen())
        return {"text": text or ""}
    except Exception as exc:
        logger.error(f"Erro de voz: {exc}")
        return {"error": str(exc)}
    finally:
        try:
            loop.close()
        except Exception:
            pass


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


# ─── Wake-word (always-on) ──────────────────────────────────────────────

def _notify_ui(user_text: str, assistant_text: str):
    """Empurra uma conversa iniciada por voz para a UI, se estiver aberta."""
    try:
        eel.on_voice_exchange(user_text, assistant_text)()   # type: ignore[attr-defined]
    except Exception:
        pass   # UI fechada (modo tray) — a conversa fica na memória na mesma


def _on_wake_word():
    """Corre na thread da wake-word: escuta o pedido, responde e fala."""
    if wake_word:
        wake_word.pause()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        user_text = loop.run_until_complete(voice.listen())
        if not user_text or not user_text.strip():
            logger.info("Wake-word: nada percebido, a voltar a escutar.")
            return
        logger.info(f"Wake-word → pedido: '{user_text[:80]}'")
        result = loop.run_until_complete(_process_message(user_text, blocking_tts=True))
        _notify_ui(user_text, result.get("text", ""))
    except Exception as exc:
        logger.error(f"Erro no fluxo da wake-word: {exc}", exc_info=True)
    finally:
        try:
            loop.close()
        except Exception:
            pass
        if wake_word:
            wake_word.resume()


def _start_wake_word():
    global wake_word
    voice_cfg = CONFIG.get("voice", {}) or {}
    if not voice_cfg.get("wake_word_enabled", False):
        logger.info("Wake-word desactivada em settings.yaml.")
        return
    wake_word = WakeWordEngine(voice_cfg, on_wake=_on_wake_word)
    if wake_word.start():
        logger.info(f"🎙️  Escuta contínua activa — diz '{wake_word.phrase}'.")


async def _process_message(user_text: str, blocking_tts: bool = False) -> dict:
    try:
        memory.save_message("user", user_text)
        full_response = ""
        status_updates = []

        async for token in brain.chat(user_text, stream=False):
            if token.startswith("_thinking_:"):
                status = token.replace("_thinking_:", "")
                status_updates.append(status)
                logger.info(f"Status: {status}")
            else:
                full_response += token

        if not full_response.strip():
            full_response = "Desculpa Simão, não consegui gerar uma resposta. Tenta de novo."

        memory.save_message("assistant", full_response)
        logger.info(f"Resposta gerada ({len(full_response)} chars): {full_response[:80]}")

        if CONFIG.get("voice", {}).get("tts_enabled", False):
            if blocking_tts:
                # Fluxo de voz: espera pela fala para não se ouvir a si próprio
                await voice.speak(full_response)
            else:
                def _falar():
                    loop2 = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop2)
                    try:
                        loop2.run_until_complete(voice.speak(full_response))
                    finally:
                        loop2.close()
                threading.Thread(target=_falar, daemon=True).start()

        return {"text": full_response, "status": status_updates, "ok": True}

    except Exception as exc:
        logger.error(f"Erro no _process_message: {exc}", exc_info=True)
        return {"text": f"Ups Simão, ocorreu um erro: {exc}", "ok": False, "error": str(exc)}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _parse_args():
    parser = argparse.ArgumentParser(description="H.E.L.I.O.S.")
    parser.add_argument("--mode", default=os.getenv("HELIOS_MODE", "chrome"),
                        help="chrome | default | edge | electron (electron = sem browser próprio)")
    parser.add_argument("--port", type=int, default=0, help="Porta do servidor Eel (0 = automática)")
    args, _unknown = parser.parse_known_args()
    return args


def main():
    args = _parse_args()

    logger.info("🌟 H.E.L.I.O.S. V7 a iniciar...")
    logger.info(f"   Frontend : {FRONTEND_DIR} ({'✅' if FRONTEND_DIR.exists() else '❌ não existe!'})")
    logger.info(f"   API Key  : {'✅' if os.getenv('GROQ_API_KEY') else '❌ FALTA!'}")
    logger.info(f"   Memória  : {memory.count_messages()} mensagens no histórico")

    _start_wake_word()

    ui_cfg = CONFIG.get("ui", {}) or {}
    port   = args.port or int(ui_cfg.get("port", 0) or 0) or _free_port()
    size   = (int(ui_cfg.get("width", 1440)), int(ui_cfg.get("height", 900)))

    # O Electron lê esta linha do stdout para saber onde ligar-se
    print(f"HELIOS_PORT={port}", flush=True)

    # Em modo Electron o Python só serve a UI — quem a mostra é o Electron
    modes = [None] if args.mode == "electron" else [args.mode, "default"]

    for mode in modes:
        try:
            eel.start("index.html", mode=mode, size=size, port=port, block=True)
            return
        except (SystemExit, KeyboardInterrupt):
            logger.info("H.E.L.I.O.S. encerrado.")
            return
        except Exception as exc:
            logger.warning(f"Falha a abrir UI (mode={mode}): {exc}")

    logger.critical("Impossível abrir a UI do H.E.L.I.O.S.")


if __name__ == "__main__":
    main()
