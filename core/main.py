"""Nano Assistant — Main Entry Point.

Servidor Eel/Python de orquestração para o Nano Assistant, suportando
streaming bidirecional, execução de ferramentas, guardrails e modo de voz.
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import os
import socket
import sys
import threading
import uuid
from pathlib import Path

# Suporte ao caminho de arranque empacotado ou local
_APP_ROOT_ENV = os.getenv("NANO_APP_ROOT") or os.getenv("HELIOS_APP_ROOT")
if _APP_ROOT_ENV:
    _bootstrap_root = Path(_APP_ROOT_ENV).expanduser().resolve()
else:
    _bootstrap_root = Path(__file__).resolve().parent.parent
if str(_bootstrap_root) not in sys.path:
    sys.path.insert(0, str(_bootstrap_root))

import eel
import psutil
from dotenv import load_dotenv
from core.app_paths import DATA_DIR, FRONTEND_DIR, PLUGINS_DIR, ROOT
from core.brain import Brain
from core.config import load_config
from core.guardrails import GuardrailsEngine
from core.plugin_loader import load_all_plugins, list_plugins
from core.memory import get_memory
from core.logger import setup_logger
from core.local_runtime import choose_model, ollama_available, model_available
from core.voice import VoiceEngine, VoiceRuntime
from core.wake_word import WakeWordEngine
from core.errors import NanoError
from core.events import EventBus
from core.permission_manager import PermissionManager
from core.task_engine import TaskEngine
from core.context_engine import ContextEngine
from core.agent_orchestrator import AgentOrchestrator
from core.agent_registry import AgentRegistry
from core.tool_execution import ToolExecutor
from core.background_worker import BackgroundTaskWorker

if not getattr(sys, "frozen", False):
    load_dotenv(ROOT / ".env")

setup_logger()
logger = logging.getLogger("nano.main")
CONFIG = load_config()
guardrails = GuardrailsEngine()
memory = get_memory()
voice = VoiceEngine(CONFIG.get("voice", {}))
API_KEY = os.getenv("NANO_API_KEY") or os.getenv("HELIOS_API_KEY") or os.getenv("GROQ_API_KEY") or str(CONFIG.get("groq_api_key") or "")

# Agent-core foundations for Nano: queue, context, events and autonomous task planning.
event_bus = EventBus()
task_engine = TaskEngine()
permission_manager = PermissionManager()
context_engine = ContextEngine(memory, task_engine)
agent_registry = AgentRegistry()
agent_orchestrator = AgentOrchestrator(memory, task_engine=task_engine, event_bus=event_bus, context_engine=context_engine, permission_manager=permission_manager, agent_registry=agent_registry)
tool_executor = ToolExecutor(permission_manager=permission_manager, event_bus=event_bus)
background_worker = BackgroundTaskWorker(task_engine=task_engine, event_bus=event_bus, context_engine=context_engine, memory=memory, tool_executor=tool_executor, permission_manager=permission_manager)
brain = Brain(api_key=API_KEY, guardrails=guardrails, memory=memory, config=CONFIG, permission_manager=permission_manager)
brain.load_history()
load_all_plugins(PLUGINS_DIR)
voice_runtime = VoiceRuntime(
    voice,
    brain=brain,
    orchestrator=agent_orchestrator,
    task_engine=task_engine,
    permission_manager=permission_manager,
    event_bus=event_bus,
    config=CONFIG,
)

wake_word: WakeWordEngine | None = None
eel.init(str(FRONTEND_DIR) if FRONTEND_DIR.exists() else str(ROOT / "web"))

_EVENT_LOOP: asyncio.AbstractEventLoop | None = None
_EVENT_LOOP_THREAD: threading.Thread | None = None

def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    """Obtém ou inicializa a event-loop partilhada assíncrona numa thread dedicada."""
    global _EVENT_LOOP, _EVENT_LOOP_THREAD
    if _EVENT_LOOP is None or not _EVENT_LOOP.is_running():
        _EVENT_LOOP = asyncio.new_event_loop()
        _EVENT_LOOP_THREAD = threading.Thread(
            target=_EVENT_LOOP.run_forever,
            name="NanoAsyncLoop",
            daemon=True
        )
        _EVENT_LOOP_THREAD.start()
    return _EVENT_LOOP

def run_coro(coro):
    """Executa de forma segura uma corrotina na thread assíncrona principal."""
    loop = _get_or_create_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()

def _permission_confirmation_message(action_name: str, args: dict) -> str:
    target = args.get("path") or args.get("target") or args.get("url") or args.get("command")
    if target:
        return f"O Nano pretende executar '{action_name}' sobre '{target}'. Confirmas?"
    return f"O Nano pretende executar '{action_name}'. Confirmas?"

def _permission_confirmation_callback(action_name: str, args: dict) -> bool:
    """Bridge síncrono entre PermissionManager e confirmação humana na UI."""
    message = _permission_confirmation_message(action_name, args or {})
    meta = {"tool": action_name, "args": args or {}, "source": "permission_manager"}
    try:
        return bool(run_coro(guardrails.request_from_ui(message, meta)))
    except Exception:
        logger.exception("Falha na confirmação de permissão para '%s'", action_name)
        return False

permission_manager.confirmation_callback = _permission_confirmation_callback
guardrails.set_confirm_callback(guardrails.request_from_ui)

@eel.expose
def send_message(user_text: str, msg_id: str | None = None) -> dict:
    """Processa uma mensagem do utilizador via chat com streaming e tool-calling."""
    if not user_text or not user_text.strip():
        return {"error": "Mensagem vazia"}
    try:
        return run_coro(_process_message(user_text, msg_id=msg_id))
    except Exception:
        logger.exception("Erro ao processar mensagem")
        return {"error": "Falha ao processar o pedido", "text": "Ups Simão, ocorreu um erro ao processar o teu pedido."}

@eel.expose
def stop_voice():
    """Interrompe qualquer reprodução ou captura ativa de áudio."""
    voice.stop()
    return {"ok": True}

@eel.expose
def confirm_action(request_id: str, confirmed: bool) -> dict:
    """Resolve uma resposta de confirmação enviada pela UI."""
    return {"ok": guardrails.resolve_confirmation(request_id, confirmed)}

@eel.expose
def get_health_status() -> dict:
    """Retorna o estado de saúde dos componentes locais, na cloud e base de dados."""
    profile = choose_model(CONFIG)
    base_url = brain.ollama_url.removesuffix("/api/chat")
    try:
        local_ok = run_coro(ollama_available(base_url))
        model_ok = run_coro(model_available(brain.ollama_model, base_url)) if local_ok else False
    except Exception:
        local_ok = model_ok = False
    try:
        db_ok = memory.count_messages() >= 0
        message_count = memory.count_messages() if db_ok else 0
    except Exception:
        db_ok, message_count = False, 0
    return {
        "ok": bool(FRONTEND_DIR.exists() and db_ok),
        "version": "8.1.0",
        "name": "Nano Assistant",
        "cloud": {"configured": bool(API_KEY), "model": brain.groq_model},
        "local": {
            "enabled": brain.local_enabled,
            "model": brain.ollama_model,
            "available": local_ok,
            "modelReady": model_ok,
            "ramGb": round(profile.ram_gb, 1)
        },
        "memory": {"database": db_ok, "messages": message_count},
        "plugins": {"count": len(list_plugins())},
        "paths": {"dataReady": DATA_DIR.exists()},
        "agents": agent_orchestrator.get_status(),
        "worker": background_worker.status(),
        "system": get_system_stats(),
    }

@eel.expose
def list_permission_policies() -> list:
    """Lista as policies persistentes de permissões do Nano."""
    return permission_manager.list_policies()

@eel.expose
def set_permission_policy(capability: str, decision: str, scope: str = "workspace", reason: str = "") -> dict:
    """Atualiza uma policy de permissão por capability."""
    if not capability:
        return {"ok": False, "error": "missing_capability"}
    normalized_decision = str(decision).lower()
    canonical = permission_manager._canonical_capability(capability)
    if normalized_decision in {"allow", "allow_persistent", "autonomous"}:
        if permission_manager.is_critical_capability(canonical):
            return {"ok": False, "error": "critical_capability_requires_approval_gate"}
        if permission_manager.is_approval_gated(canonical):
            return {"ok": False, "error": "approval_gated_capability_cannot_be_autonomous"}
    if normalized_decision in {"allow_persistent", "allow"}:
        return {"ok": False, "error": "persistent_allow_disabled"}
    return {"ok": True, "policy": permission_manager.register_policy(capability, decision=decision, scope=scope, reason=reason or "User configured policy.")}

@eel.expose
def set_autonomy_mode(mode: str) -> dict:
    """Configura o nível de autonomia do sistema."""
    try:
        permission_manager.policy_engine.autonomy_mode = permission_manager.policy_engine.autonomy_mode.__class__(str(mode).upper())
    except Exception:
        return {"ok": False, "error": "invalid_mode"}
    return {"ok": True, "mode": permission_manager.policy_engine.autonomy_mode.value}

@eel.expose
def get_autonomy_mode() -> dict:
    """Retorna o nível atual de autonomia do sistema."""
    return {"ok": True, "mode": permission_manager.policy_engine.autonomy_mode.value}

@eel.expose
def set_emergency_stop(enabled: bool) -> dict:
    """Activa ou desactiva o kill switch global do Nano."""
    state = bool(permission_manager.set_emergency_stop(enabled))
    event_bus.publish("security.emergency_stop", {"enabled": state, "source": "backend"})
    return {"ok": True, "enabled": state}

@eel.expose
def get_emergency_stop_state() -> dict:
    """Retorna o estado global do kill switch do Nano."""
    return {"ok": True, "enabled": permission_manager.is_emergency_stopped()}

@eel.expose
def revoke_permission_policy(capability: str) -> dict:
    """Remove uma policy de permissão por capability."""
    return {"ok": permission_manager.revoke_policy(capability), "capability": capability}

@eel.expose
def get_runtime_info() -> dict:
    """Retorna informações de ambiente de execução (OS, RAM, Modelo sugerido)."""
    profile = choose_model(CONFIG)
    vm = psutil.virtual_memory()
    return {
        "version": "8.1.0",
        "name": "Nano Assistant",
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "ramTotalGb": round(vm.total / 1024**3, 1),
        "recommendedLocalModel": profile.model,
        "reason": profile.reason
    }

@eel.expose
def start_voice_listen() -> dict:
    """Inicia a escuta por voz e processa a mensagem atravs do Nano real."""
    try:
        result = run_coro(voice_runtime.process_audio())
        if result.get("response"):
            return {"ok": True, "text": result.get("response"), "task": result.get("task"), "mode": result.get("mode"), "requires_permission": result.get("requires_permission", False)}
        return {"ok": bool(result.get("ok")), "text": "", "error": result.get("error", "voice_failed")}
    except Exception:
        logger.exception("Erro de voz")
        return {"ok": False, "error": "Não foi possível usar o microfone."}

@eel.expose
def start_voice_session() -> dict:
    """Inicia uma sessão manual de voz para teste local."""
    try:
        response = run_coro(voice_runtime.listen_once())
        if not response:
            return {"ok": False, "error": "no_transcript"}
        return {"ok": True, "text": response}
    except Exception:
        logger.exception("Erro ao iniciar a sessão de voz")
        return {"ok": False, "error": "voice_session_failed"}

@eel.expose
def get_conversation_history() -> list:
    """Retorna as mensagens recentes da conversa."""
    return memory.get_recent_messages(limit=50)

@eel.expose
def start_background_worker() -> dict:
    """Liga o worker real de tarefas em segundo plano do Nano."""
    return background_worker.start()

@eel.expose
def stop_background_worker() -> dict:
    """Desliga o worker em segundo plano."""
    return background_worker.stop()

@eel.expose
def get_background_worker_status() -> dict:
    """Retorna o estado do worker e da fila de tarefas."""
    return {"worker": background_worker.status(), "agent": agent_orchestrator.get_status()}

@eel.expose
def create_agent_task(title: str, description: str = "", task_type: str = "instant", priority: int = 5, metadata: dict | None = None) -> dict:
    """Cria uma tarefa no task queue persistente do Nano."""
    task = task_engine.create_task(title=title, description=description, task_type=task_type, priority=priority, metadata=metadata or {})
    event_bus.publish("task.created", {"task_id": task["id"], "title": task["title"], "status": task["status"]})
    return {"ok": True, "task": task}

@eel.expose
def list_agent_tasks(status: str | None = None, limit: int = 25) -> list:
    """Lista tarefas persistentes do queue do Nano."""
    return task_engine.list_tasks(status=status, limit=max(1, int(limit)))

@eel.expose
def get_agent_status() -> dict:
    """Retorna o estado do agent orchestrator e da fila de tarefas."""
    return agent_orchestrator.get_status()

@eel.expose
def get_command_center_state() -> dict:
    """Retorna o estado real do command center para a UI."""
    current_task = None
    tasks = task_engine.list_tasks(limit=25)
    if tasks:
        current_task = tasks[0]
    return {
        "worker": background_worker.status(),
        "system": get_system_stats(),
        "task_summary": task_engine.get_status_summary(),
        "current_task": current_task,
        "tasks": tasks,
        "activities": event_bus.get_recent_events(15),
        "permissions": permission_manager.get_pending_permissions(),
        "agents": agent_registry.as_dict(),
        "health": {
            "worker": background_worker.status(),
            "memory": memory.get_facts(),
            "providers": {"ollama": "online", "desktop": "online", "browser": "online"},
        },
    }

@eel.expose
def get_task_detail(task_id: str) -> dict:
    """Retorna uma visão detalhada de uma tarefa."""
    task = task_engine.get_task(task_id)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    return {"ok": True, "task": task, "events": event_bus.get_recent_events(50), "permissions": permission_manager.get_pending_permissions()}

@eel.expose
def resolve_permission(request_id: str, decision: str) -> dict:
    """Responde a uma autorização pendente."""
    if not request_id or not decision:
        return {"ok": False, "error": "missing_request_or_decision"}
    normalized = str(decision).lower()
    if normalized in {"allow", "allow_persistent"}:
        return {"ok": False, "error": "persistent_allow_disabled"}
    pending = permission_manager.get_pending_permissions()
    if not any(item.get("id") == request_id for item in pending):
        return {"ok": False, "error": "request_not_found"}
    return permission_manager.resolve_permission(request_id, decision)

@eel.expose
def orchestrate_request(user_text: str, metadata: dict | None = None) -> dict:
    """Cria um plano estruturado a partir do request do utilizador."""
    if not user_text or not user_text.strip():
        return {"ok": False, "error": "empty_request"}
    return agent_orchestrator.handle_request(user_text, metadata=metadata or {})

@eel.expose
def clear_conversation() -> dict:
    """Limpa o histórico em memória da conversa ativa."""
    brain.reset_conversation()
    return {"ok": True}

@eel.expose
def get_loaded_plugins() -> dict:
    """Retorna o dicionário de plugins e respetivas ferramentas ativas."""
    return list_plugins()

@eel.expose
def get_plugin_code(plugin_name: str) -> dict:
    """Retorna o código-fonte e metadados de um plugin específico."""
    from core.plugin_loader import get_plugin_source
    return get_plugin_source(plugin_name)

@eel.expose
def get_wake_word_status() -> dict:
    """Retorna o estado do motor de wake-word."""
    return wake_word.status() if wake_word else {"enabled": False, "running": False}

@eel.expose
def get_memory_facts() -> dict:
    """Retorna factos persistentes aprendidos sobre o utilizador."""
    return memory.get_facts()

@eel.expose
def get_system_stats() -> dict:
    """Retorna métricas em tempo real de CPU, RAM e Disco."""
    memory_info = psutil.virtual_memory()
    disk = psutil.disk_usage(str(DATA_DIR))
    try:
        network = psutil.net_io_counters()
        network_sent = round(network.bytes_sent / 1024**2, 2)
        network_recv = round(network.bytes_recv / 1024**2, 2)
    except Exception:
        network_sent = 0.0
        network_recv = 0.0
    return {
        "cpu": round(psutil.cpu_percent(interval=None), 1),
        "ram": round(memory_info.percent, 1),
        "ramUsed": round(memory_info.used / 1024**3, 1),
        "ramTotal": round(memory_info.total / 1024**3, 1),
        "disk": round(disk.percent, 1),
        "diskUsed": round(disk.used / 1024**3, 1),
        "diskTotal": round(disk.total / 1024**3, 1),
        "networkSentMb": network_sent,
        "networkRecvMb": network_recv,
    }

def _emit_stream_start(msg_id: str, user_text: str | None = None):
    try:
        eel.on_stream_start(msg_id, user_text)()
    except Exception:
        pass

def _emit_stream_status(msg_id: str, status: str):
    try:
        eel.on_stream_status(msg_id, status)()
    except Exception:
        pass

def _emit_stream_chunk(msg_id: str, chunk: str):
    try:
        eel.on_stream_chunk(msg_id, chunk)()
    except Exception:
        pass

def _emit_stream_end(msg_id: str, result: dict):
    try:
        eel.on_stream_end(msg_id, result)()
    except Exception:
        pass

def _emit_wake_detected():
    try:
        eel.on_wake_detected()()
    except Exception:
        pass

def _on_wake_word():
    _emit_wake_detected()
    if wake_word:
        wake_word.pause()
    try:
        result = run_coro(voice_runtime.handle_wake_word())
        if result.get("ok") and result.get("response"):
            loop = _get_or_create_loop()
            future = asyncio.run_coroutine_threadsafe(voice_runtime.speak_response(str(result["response"])), loop)
            future.result(timeout=30)
        if result.get("requires_permission"):
            logger.info("Voice permission request pending: %s", result.get("request_id"))
    except Exception:
        logger.exception("Erro no fluxo wake-word")
    finally:
        if wake_word:
            wake_word.resume()


def _start_wake_word() -> None:
    """Start the optional wake-word listener if the runtime is enabled."""
    global wake_word
    if not CONFIG.get("voice", {}).get("wake_word", {}).get("enabled", False):
        return
    try:
        wake_word = WakeWordEngine(CONFIG.get("voice", {}).get("wake_word", {}), on_wake=_on_wake_word)
        started = wake_word.start()
        if started:
            logger.info("Wake word started")
        else:
            logger.warning("Wake word not started: %s", wake_word.status().get("error") or "setup required")
    except Exception:
        logger.exception("Erro ao iniciar wake-word")

@eel.expose
def get_audio_devices():
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        inputs = [d for d in devices if d['max_input_channels'] > 0]
        outputs = [d for d in devices if d['max_output_channels'] > 0]
        return {
            "inputs": [{"id": i, "name": d["name"]} for i, d in enumerate(inputs)],
            "outputs": [{"id": i, "name": d["name"]} for i, d in enumerate(outputs)],
        }
    except Exception as e:
        logger.error(f"Failed to query audio devices: {e}")
        return {"inputs": [], "outputs": []}

@eel.expose
def set_input_device(device_id: int):
    # Store chosen input device in config for later use by VoiceEngine
    CONFIG.setdefault("audio", {})["input_device"] = device_id
    logger.info(f"Input audio device set to {device_id}")
    return True

@eel.expose
def set_output_device(device_id: int):
    CONFIG.setdefault("audio", {})["output_device"] = device_id
    logger.info(f"Output audio device set to {device_id}")
    return True

@eel.expose
def stop_voice():
    """Stop any ongoing TTS playback immediately."""
    try:
        voice.stop()
    except Exception as e:
        logger.error(f"Failed to stop voice: {e}")
    return True



async def _process_message(user_text: str, msg_id: str | None = None, blocking_tts: bool = False) -> dict:
    if not msg_id:
        msg_id = uuid.uuid4().hex
    try:
        memory.save_message("user", user_text)
        _emit_stream_start(msg_id, user_text)
        full_response, status_updates = "", []
        async for token in brain.chat(user_text, stream=True):
            if token.startswith("_thinking_:"):
                status = token.removeprefix("_thinking_:")
                status_updates.append(status)
                _emit_stream_status(msg_id, status)
            else:
                full_response += token
                _emit_stream_chunk(msg_id, token)
        full_response = full_response.strip() or "Desculpa Simão, não consegui gerar uma resposta."
        memory.save_message("assistant", full_response)
        result = {"msg_id": msg_id, "text": full_response, "status": status_updates, "ok": True}
        _emit_stream_end(msg_id, result)
        if CONFIG.get("voice", {}).get("tts_enabled", False):
            if blocking_tts:
                await voice.speak(full_response)
            else:
                threading.Thread(target=lambda: asyncio.run(voice.speak(full_response)), daemon=True).start()
        return result
    except Exception:
        logger.exception("Erro no _process_message")
        error_result = {"msg_id": msg_id, "text": "Ups Simão, ocorreu um erro ao processar o teu pedido.", "ok": False, "error": "processing_error"}
        _emit_stream_end(msg_id, error_result)
        return error_result

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])

def _parse_args():
    parser = argparse.ArgumentParser(description="Nano Assistant")
    parser.add_argument("--mode", default=os.getenv("NANO_MODE", os.getenv("HELIOS_MODE", "electron")))
    parser.add_argument("--port", type=int, default=0)
    return parser.parse_known_args()[0]

def _open_browser_tab(port: int):
    import webbrowser
    url = f"http://localhost:{port}/index.html"
    logger.info("A abrir navegador em: %s", url)
    try:
        webbrowser.open(url)
    except Exception as exc:
        logger.debug("Falha ao abrir navegador automaticamente: %s", exc)

def main():
    args = _parse_args()
    logger.info("Nano Assistant a iniciar...")
    logger.info("Frontend: %s", FRONTEND_DIR)
    logger.info("Cloud: %s", "configurado" if API_KEY else "não configurado")
    background_worker.start()
    _start_wake_word()
    ui_cfg = CONFIG.get("ui", {}) or {}
    port = args.port or int(ui_cfg.get("port", 0) or 0) or _free_port()
    size = (int(ui_cfg.get("width", 1440)), int(ui_cfg.get("height", 900)))
    print(f"NANO_PORT={port}", flush=True)

    # Abre automaticamente a aba no Google Chrome / Navegador padrão
    threading.Timer(0.8, _open_browser_tab, args=(port,)).start()

    modes = [False] if args.mode == "electron" else [args.mode, "default", False]
    for mode in modes:
        try:
            eel.start("index.html", mode=mode, size=size, port=port, block=True)
            return
        except (SystemExit, KeyboardInterrupt):
            return
        except Exception:
            logger.warning("Falha a iniciar UI em modo %s", mode, exc_info=True)
    raise RuntimeError("Impossível iniciar a UI do Nano Assistant")

if __name__ == "__main__":
    main()
