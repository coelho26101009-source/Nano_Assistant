"""Nano Assistant — Main Entry Point.

Servidor Eel/Python de orquestração para o Nano Assistant, suportando
streaming bidirecional, execução de ferramentas, guardrails e modo de voz.
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import os
import re
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
from core.config import CONFIG_PATH, load_config
from core.guardrails import GuardrailsEngine
from core.plugin_loader import load_all_plugins, list_plugins, start_plugin_services, stop_plugin_services
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
from core import audio_feedback, ollama_service

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

# Plugins must be loaded before the executor absorbs them, and the executor must
# own every plugin tool before the Brain can call one: plugin_loader refuses to
# dispatch a handler to anyone but a bound execution authority.
load_all_plugins(PLUGINS_DIR)
_PLUGIN_TOOLS_REGISTERED = tool_executor.register_plugin_tools()
logger.info("Ferramentas de plugin sob a autoridade central: %d", _PLUGIN_TOOLS_REGISTERED)

background_worker = BackgroundTaskWorker(task_engine=task_engine, event_bus=event_bus, context_engine=context_engine, memory=memory, tool_executor=tool_executor, permission_manager=permission_manager)
brain = Brain(api_key=API_KEY, guardrails=guardrails, memory=memory, config=CONFIG, permission_manager=permission_manager, tool_executor=tool_executor)
brain.load_history()
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
# Result of the startup Ollama probe/launch; surfaced to the UI as model.boot.
OLLAMA_BOOT: dict = {"available": False, "started": False, "reused": False, "detail": "não avaliado"}
eel.init(str(FRONTEND_DIR) if FRONTEND_DIR.exists() else str(ROOT / "web"))

_EVENT_LOOP: asyncio.AbstractEventLoop | None = None
_EVENT_LOOP_THREAD: threading.Thread | None = None
_EVENT_LOOP_LOCK = threading.Lock()
DEFAULT_COROUTINE_TIMEOUT = 120.0


class LoopReentrancyError(RuntimeError):
    """Raised when run_coro is called from the loop thread it would block."""


def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    """Obtém ou inicializa a event-loop partilhada assíncrona numa thread dedicada."""
    global _EVENT_LOOP, _EVENT_LOOP_THREAD
    with _EVENT_LOOP_LOCK:
        if _EVENT_LOOP is None or not _EVENT_LOOP.is_running():
            _EVENT_LOOP = asyncio.new_event_loop()
            _EVENT_LOOP_THREAD = threading.Thread(
                target=_EVENT_LOOP.run_forever,
                name="NanoAsyncLoop",
                daemon=True
            )
            _EVENT_LOOP_THREAD.start()
        return _EVENT_LOOP


def run_coro(coro, *, timeout: float | None = DEFAULT_COROUTINE_TIMEOUT):
    """Executa uma corrotina na loop partilhada a partir de outra thread.

    Chamar isto de dentro da própria loop agendaria trabalho na loop que
    estamos a bloquear — o deadlock que travava todas as confirmações vindas
    do chat. Passou a ser um erro explícito em vez de uma espera infinita.
    """
    loop = _get_or_create_loop()
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        coro.close()
        raise LoopReentrancyError(
            "run_coro() foi chamado a partir da loop partilhada. Use await directamente."
        )
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)

def _permission_confirmation_message(action_name: str, args: dict) -> str:
    target = args.get("path") or args.get("target") or args.get("url") or args.get("command")
    if target:
        return f"O Nano pretende executar '{action_name}' sobre '{target}'. Confirmas?"
    return f"O Nano pretende executar '{action_name}'. Confirmas?"


def _confirmation_meta(action_name: str, args: dict) -> dict:
    """Argumentos mostrados ao humano, sem segredos.

    O diálogo tem de mostrar o que está realmente a ser aprovado: aprovar
    'alterar Wi-Fi' sem ver os argumentos não é consentimento informado.
    """
    safe_args = {
        key: str(value)[:200]
        for key, value in (args or {}).items()
        if not key.startswith("_") and not re.search(r"secret|token|password|key|credential", key, re.I)
    }
    return {"tool": action_name, "args": safe_args, "source": "permission_manager"}


async def _permission_confirmation_async(action_name: str, args: dict) -> bool:
    """Confirmação humana sem bloquear a loop que a pediu."""
    message = _permission_confirmation_message(action_name, args or {})
    try:
        return bool(await guardrails.request_from_ui(message, _confirmation_meta(action_name, args)))
    except Exception:
        logger.exception("Falha na confirmação de permissão para '%s'", action_name)
        return False


def _permission_confirmation_callback(action_name: str, args: dict) -> bool:
    """Bridge síncrono usado pelo worker em background (nunca pela loop)."""
    message = _permission_confirmation_message(action_name, args or {})
    try:
        return bool(run_coro(guardrails.request_from_ui(message, _confirmation_meta(action_name, args)), timeout=90))
    except LoopReentrancyError:
        logger.error("Confirmação síncrona pedida dentro da loop para '%s'; use o caminho assíncrono.", action_name)
        return False
    except Exception:
        logger.exception("Falha na confirmação de permissão para '%s'", action_name)
        return False

permission_manager.confirmation_callback = _permission_confirmation_callback
permission_manager.async_confirmation_callback = _permission_confirmation_async
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
    """Interrompe qualquer reprodução ou captura ativa de áudio.

    Esta é a única exposição Eel de 'stop_voice'. Uma segunda definição com o
    mesmo nome fazia com que eel._expose falhasse no import e a aplicação nunca
    arrancasse.
    """
    try:
        voice.stop()
    except Exception as exc:
        logger.error("Falha ao parar a voz: %s", exc)
        return {"ok": False, "error": "stop_failed"}
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

def _summarize_task_row(task: dict) -> dict:
    """A task row without its unbounded metadata/result blobs.

    get_task_detail() still returns the full row when the user opens a task.
    """
    summary = {key: value for key, value in task.items() if key not in {"metadata", "result"}}
    result = task.get("result")
    summary["has_result"] = result is not None
    plan = (task.get("metadata") or {}).get("plan") if isinstance(task.get("metadata"), dict) else None
    if isinstance(plan, dict):
        summary["task_kind"] = plan.get("task_type")
    return summary


@eel.expose
def get_command_center_state() -> dict:
    """Retorna o estado real do command center para a UI."""
    # The Command Center polls this every few seconds, so it must stay cheap.
    # Task rows carry 'metadata' and 'result' blobs that are only needed in the
    # task detail view; shipping them on every poll was loading (and JSON
    # parsing) hundreds of MB per minute.
    tasks = [_summarize_task_row(task) for task in task_engine.list_tasks(limit=25)]
    current_task = tasks[0] if tasks else None
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
            "providers": get_provider_health(),
        },
        "emergency_stop": permission_manager.is_emergency_stopped(),
        "autonomy_mode": permission_manager.policy_engine.autonomy_mode.value,
    }


def probe_local_model() -> dict:
    """Report the local model provider state. Read-only: starts nothing.

    Startup is where Ollama may be launched (see _start_ollama). This function
    is called repeatedly by the UI poller, so it must never spawn anything.
    """
    return ollama_service.describe(
        brain.ollama_model,
        brain.ollama_url.removesuffix("/api/chat"),
        local_enabled=brain.local_enabled,
    )


def _start_ollama() -> None:
    """Bring up the Ollama API at startup, reusing it if it is already running.

    Only the SERVER is started. No model is preloaded, no warm-up inference is
    sent, nothing is pulled: on 16 GB the idle server costs tens of megabytes
    while an 8B model costs gigabytes, so the model must wait for a real
    request. If Ollama is missing or refuses to start, Nano continues to boot —
    voice, wake phrase and the UI stay usable and the state is reported honestly.
    """
    global OLLAMA_BOOT
    if not brain.local_enabled:
        OLLAMA_BOOT = {"available": False, "started": False, "reused": False, "detail": "Modelos locais desativados."}
        return

    base_url = brain.ollama_url.removesuffix("/api/chat")
    autostart = bool(CONFIG.get("local", {}).get("autostart", True))
    try:
        OLLAMA_BOOT = ollama_service.ensure_running(
            base_url,
            autostart=autostart,
            keep_alive=str(CONFIG.get("local", {}).get("keep_alive") or ollama_service.DEFAULT_KEEP_ALIVE),
        )
    except Exception as exc:
        logger.exception("Falha inesperada ao preparar o Ollama")
        OLLAMA_BOOT = {"available": False, "started": False, "reused": False, "detail": str(exc)}
        return

    logger.info("Ollama: %s", OLLAMA_BOOT.get("detail"))
    if OLLAMA_BOOT.get("available"):
        status = probe_local_model()
        if status["state"] != ollama_service.OllamaState.READY:
            # Say exactly what is missing instead of quietly using another model.
            logger.warning("Modelo local indisponível: %s", status.get("detail"))
    else:
        logger.warning("Nano continua a arrancar sem modelo local. %s", OLLAMA_BOOT.get("detail"))


@eel.expose
def get_local_model_status() -> dict:
    """Exposed so the UI (and the user) can see exactly why local chat is down."""
    status = probe_local_model()
    status["boot"] = OLLAMA_BOOT
    return status


@eel.expose
def get_system_readiness() -> dict:
    """Consolidated, honest readiness for every subsystem the UI displays.

    Every value here is derived from a real check. Where a subsystem cannot be
    verified the state is UNKNOWN or SETUP_REQUIRED — never READY by default.
    """
    voice_status = voice_runtime.status()
    wake = wake_word.status() if wake_word else {}
    wake_model = str(wake.get("model_status") or "").upper()
    wake_enabled = bool(CONFIG.get("voice", {}).get("wake_word", {}).get("enabled", False))

    if not wake_enabled:
        wake_state = "DISABLED"
    elif wake.get("running"):
        wake_state = "READY"
    elif wake_model == "READY":
        wake_state = "MODEL_LOADING"
    elif wake_model in {"MISSING", "INVALID", "LOAD_ERROR"}:
        wake_state = "MODEL_MISSING"
    else:
        wake_state = "SETUP_REQUIRED"

    # "Hey Nano" — the STT-based phrase detector. Its readiness comes straight
    # from WakePhraseEngine.readiness(); nothing here is asserted or guessed.
    wake_phrase_status = voice.wake_phrase_provider.status()

    # Ollama is the USER's process, never Nano's. We only probe the API; we
    # never spawn `ollama serve`, never open Ollama Desktop, never pull a model.
    local = probe_local_model()
    ollama_up = local["ollamaUp"]
    model_ready = local["modelReady"]
    model_state = local["state"]

    worker = background_worker.status()
    summary = task_engine.get_status_summary()
    pending = permission_manager.get_pending_permissions()

    if permission_manager.is_emergency_stopped():
        agent_state = "OFFLINE"
    elif pending:
        agent_state = "APPROVAL_REQUIRED"
    elif summary.get("RUNNING") or summary.get("PLANNING"):
        agent_state = "WORKING"
    elif summary.get("NEEDS_ATTENTION"):
        agent_state = "ERROR"
    elif not worker.get("running"):
        agent_state = "SETUP_REQUIRED"
    elif summary.get("QUEUED") or summary.get("RETRYING"):
        agent_state = "WAITING"
    else:
        agent_state = "READY"

    return {
        "agent": {"state": agent_state, "pending_permissions": len(pending)},
        "voice": {
            "state": voice_status.get("readiness", "UNKNOWN"),
            "blockers": voice_status.get("blockers", []),
            "enabled": voice_status.get("enabled", False),
        },
        "wakeWord": {
            "state": wake_state,
            "phrase": wake.get("phrase"),
            "modelStatus": wake_model or "UNKNOWN",
            "error": wake.get("error"),
        },
        "wakePhrase": {
            "state": wake_phrase_status.get("readiness", "UNKNOWN"),
            "turnState": wake_phrase_status.get("state"),
            "phrase": wake_phrase_status.get("phrase"),
            "allowNanoOnly": wake_phrase_status.get("allow_nano_only"),
            "cooldownSeconds": wake_phrase_status.get("cooldown_seconds"),
            "error": wake_phrase_status.get("error"),
        },
        "model": {
            "state": model_state,
            "detail": local.get("detail"),
            "installed": local.get("installed", []),
            "local": {
                "model": brain.ollama_model,
                "online": ollama_up,
                "modelReady": model_ready,
                "enabled": brain.local_enabled,
                "url": local.get("url"),
            },
            "cloud": {"model": brain.groq_model, "configured": bool(API_KEY)},
            "provider": "ollama" if model_ready else ("cloud" if API_KEY else "none"),
        },
        "worker": {"state": "READY" if worker.get("running") else "OFFLINE", **worker},
        "providers": get_provider_health(),
        "emergencyStop": permission_manager.is_emergency_stopped(),
        "autonomyMode": permission_manager.policy_engine.autonomy_mode.value,
        "browser": {"state": "EXPERIMENTAL"},
        "vision": {"state": "NOT_AVAILABLE"},
    }


@eel.expose
def get_provider_health() -> dict:
    """Estado real de cada provider. Nunca reporta 'online' sem verificar."""
    base_url = brain.ollama_url.removesuffix("/api/chat")
    try:
        ollama = "online" if run_coro(ollama_available(base_url), timeout=5) else "offline"
    except Exception:
        ollama = "unknown"

    try:
        import importlib.util

        browser = "online" if importlib.util.find_spec("playwright") is not None else "setup_required"
    except Exception:
        browser = "unknown"


    try:
        desktop = "online" if psutil.cpu_percent(interval=None) is not None else "unknown"
    except Exception:
        desktop = "offline"

    return {
        "ollama": ollama,
        "cloud": "configured" if API_KEY else "not_configured",
        "browser": browser,
        "desktop": desktop,
        "voice": voice_runtime.status().get("readiness", "UNKNOWN"),
    }

@eel.expose
def cancel_agent_task(task_id: str) -> dict:
    """Cancel a task and release everything it was authorised to do."""
    if not task_id:
        return {"ok": False, "error": "missing_task_id"}
    task = background_worker.cancel_task(task_id)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    return {"ok": True, "task": task}


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
def get_wake_phrase_status() -> dict:
    """Retorna o estado do detector local de wake-phrase ('Hey Nano')."""
    return voice.wake_phrase_provider.status()

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

def _emit_wake_detected(transcript: str = ""):
    """Best-effort UI notification. The chime is the guaranteed feedback."""
    try:
        eel.on_wake_detected(transcript)()
    except Exception as exc:
        logger.debug("UI não recebeu o evento de wake: %s", exc)

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


def _on_wake_phrase(transcript: str) -> None:
    """Fired by the STT-based 'Hey Nano' detector.

    Order matters here. The audible chime comes FIRST, before any work: the
    user needs to know within a moment that Nano heard them, otherwise they
    talk into the gap. The chime is synthesised locally (no TTS model, no
    network) so it costs a few milliseconds.

    This callback only wakes Nano. It never resolves a capability and never
    runs a tool: everything it triggers goes through voice_runtime, i.e. the
    normal request -> policy -> permission -> execution pipeline.
    """
    logger.info("Wake phrase detected: %r", transcript)

    # 1. Audible acknowledgment — immediate, local, before anything slow.
    if not audio_feedback.acknowledge_wake():
        logger.warning("Wake detectada mas não foi possível reproduzir o som de confirmação.")

    # 2. Tell the UI (best-effort: the chime already confirmed to the user).
    _emit_wake_detected(transcript)

    voice.wake_phrase_provider.mark_command_listening()
    try:
        # 3. Capture and process the actual command through the normal pipeline.
        result = run_coro(voice_runtime.handle_wake_word())
        voice.wake_phrase_provider.mark_processing()

        if result.get("requires_permission"):
            logger.info("Voice permission request pending: %s", result.get("request_id"))

        response = result.get("response")
        if result.get("ok") and response:
            # 4. Speak the answer. Failures here are reported, never swallowed.
            loop = _get_or_create_loop()
            future = asyncio.run_coroutine_threadsafe(
                voice_runtime.speak_response(str(response)), loop
            )
            spoken = future.result(timeout=45)
            if not spoken:
                logger.warning("Resposta gerada mas o TTS não a reproduziu: %r", str(response)[:80])
                audio_feedback.signal_error()
        elif not result.get("ok"):
            logger.warning("Turno de voz falhou: %s", result.get("error"))
            audio_feedback.signal_error()
    except Exception:
        logger.exception("Erro no fluxo de wake-phrase")
        audio_feedback.signal_error()
    finally:
        voice.wake_phrase_provider.mark_idle()


def _start_wake_phrase() -> None:
    """Start the optional 'Hey Nano' STT-based phrase detector.

    Independent of _start_wake_word: this needs no trained keyword model, so it
    is the wake path that actually runs out of the box.
    """
    if not CONFIG.get("voice", {}).get("wake_phrase_enabled", False):
        return
    try:
        started = voice.start_wake_phrase(_on_wake_phrase)
        status = voice.wake_phrase_provider.status()
        if started:
            logger.info("Wake phrase started: '%s'", status.get("phrase"))
        else:
            logger.warning("Wake phrase not started: %s", status.get("error") or "setup required")
    except Exception:
        logger.exception("Erro ao iniciar wake-phrase")

@eel.expose
def get_audio_devices():
    """Lista dispositivos de áudio via PyAudio.

    Usava 'sounddevice', que não é dependência do Nano e não está instalado —
    o resultado era um ImportError a cada arranque e uma lista de dispositivos
    sempre vazia nas definições. O PyAudio já é usado para capturar áudio, por
    isso é a fonte correcta e os índices batem certo com os da captura.
    """
    try:
        # Goes through AudioInputProvider so it shares the PortAudio lock and
        # the device cache. Opening PyAudio independently here raced the
        # wake-phrase capture thread and crashed the process.
        devices = voice.input_provider.list_devices()
        inputs = [
            {"id": d["index"], "name": d["name"]}
            for d in devices
            if int(d.get("maxInputChannels") or 0) > 0
        ]
        outputs = audio_feedback.output_device_report().get("devices", [])
        return {
            "inputs": inputs,
            "outputs": [{"id": d["index"], "name": d["name"]} for d in outputs],
        }
    except Exception as exc:
        logger.error("Falha ao listar dispositivos de áudio: %s", exc)
        return {"inputs": [], "outputs": [], "error": str(exc)}

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


# Single-instance guard. Held open for the process lifetime; the OS releases it
# automatically if Nano crashes, so a stale lock can never wedge the launcher.
_INSTANCE_LOCK: socket.socket | None = None
_INSTANCE_LOCK_PORT = 47615  # fixed, private-range port used only as a mutex


def acquire_single_instance(port: int = _INSTANCE_LOCK_PORT) -> bool:
    """Return True if this is the only Nano. False if one is already running.

    A bound loopback socket is the mutex: unlike a PID file it cannot go stale,
    because Windows frees the port the moment the owning process dies.
    """
    global _INSTANCE_LOCK
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # No SO_REUSEADDR: we *want* the second bind to fail.
        lock.bind(("127.0.0.1", port))
        lock.listen(1)
    except OSError:
        lock.close()
        return False
    _INSTANCE_LOCK = lock
    return True


def _release_single_instance() -> None:
    global _INSTANCE_LOCK
    if _INSTANCE_LOCK is not None:
        try:
            _INSTANCE_LOCK.close()
        except Exception:
            pass
        _INSTANCE_LOCK = None

def _parse_args():
    parser = argparse.ArgumentParser(description="Nano Assistant")
    parser.add_argument("--mode", default=os.getenv("NANO_MODE", os.getenv("HELIOS_MODE", "electron")))
    parser.add_argument("--port", type=int, default=0)
    return parser.parse_known_args()[0]

def _open_browser_tab(port: int):
    """Abre a UI no navegador. ÚNICO ponto do Nano que abre um navegador.

    Antes existiam DOIS caminhos a abrir o browser: este (via threading.Timer)
    e o próprio eel.start(mode="default"), que também lança um navegador. Daí
    os dois separadores do Chrome. Agora o eel arranca sempre com mode=None
    (só serve HTTP) e esta função é a única responsável por abrir a janela.
    """
    import webbrowser
    url = f"http://localhost:{port}/index.html"
    logger.info("A abrir a UI do Nano em: %s", url)
    try:
        webbrowser.open_new(url)
    except Exception as exc:
        logger.warning("Não foi possível abrir o navegador automaticamente: %s", exc)
        logger.warning("Abre manualmente: %s", url)

def shutdown() -> None:
    """Stop every background service started by main()."""
    try:
        background_worker.stop()
    except Exception:
        logger.exception("Falha ao parar o worker")
    try:
        stop_plugin_services()
    except Exception:
        logger.exception("Falha ao parar serviços de plugins")
    if wake_word is not None:
        try:
            wake_word.stop()
        except Exception:
            logger.exception("Falha ao parar a wake-word")
    try:
        voice.stop()
    except Exception:
        logger.exception("Falha ao parar a voz")

    # Ollama is deliberately NOT stopped. It is a shared background service the
    # user (or another tool) may be relying on, it idles at a few tens of MB,
    # and OLLAMA_KEEP_ALIVE unloads the model by itself. Nano only tears down
    # what it exclusively owns.
    loop = _EVENT_LOOP
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(loop.stop)
    _release_single_instance()


def _report(component: str, state: str, detail: str = "") -> None:
    """One startup line per component, on stdout so the launcher window shows it.

    States are the real measured ones. Nothing prints READY unless the check
    behind it actually passed.
    """
    line = f"  [NANO] {component:.<14} {state}"
    if detail:
        line += f"  ({detail})"
    print(line, flush=True)


def main():
    args = _parse_args()

    # Refuse to become a second Nano: duplicate backends meant duplicate voice
    # engines fighting over the microphone and duplicate browser tabs.
    if not acquire_single_instance():
        _report("Instance", "ALREADY RUNNING", "close the other Nano window first")
        logger.error("O Nano já está a correr. Fecha a instância existente antes de abrir outra.")
        print("NANO_ALREADY_RUNNING=1", flush=True)
        return

    logger.info("Nano Assistant a iniciar...")
    logger.info("Frontend: %s", FRONTEND_DIR)

    _report("Python", "OK", f"{sys.version.split()[0]}")
    _report("Config", "OK" if CONFIG_PATH.exists() else "DEFAULTS", str(CONFIG_PATH.name))

    background_worker.start()
    _report("Backend", "READY" if background_worker.status().get("running") else "ERROR")

    started_services = start_plugin_services()
    _report("Plugins", "OK", f"{len(list_plugins())} loaded")
    if started_services:
        logger.info("Serviços de plugin activos: %s", ", ".join(started_services))

    # Server only — no model is loaded until a real request needs one.
    _start_ollama()
    model_status = probe_local_model()
    if model_status["state"] == "READY":
        _report("Ollama", "READY", model_status["model"])
    elif model_status["state"] == "MODEL_UNAVAILABLE":
        _report("Ollama", "MODEL MISSING", f"{model_status['model']} not installed")
    elif model_status["state"] == "DISABLED":
        _report("Ollama", "DISABLED", "local models off in config")
    else:
        _report("Ollama", "UNAVAILABLE", str(OLLAMA_BOOT.get("detail", ""))[:70])

    _report("Cloud", "OK" if API_KEY else "NOT CONFIGURED", brain.groq_model if API_KEY else "")

    voice_state = voice_runtime.status()
    _report(
        "Voice/STT",
        voice_state.get("readiness", "UNKNOWN"),
        "; ".join(voice_state.get("blockers", []))[:70],
    )

    _start_wake_word()
    _start_wake_phrase()
    wake_status = voice.wake_phrase_provider.status()
    _report(
        "Wake Phrase",
        wake_status.get("readiness", "UNKNOWN"),
        f"\"{wake_status.get('phrase')}\"" if wake_status.get("running") else str(wake_status.get("error") or "")[:70],
    )

    ui_cfg = CONFIG.get("ui", {}) or {}
    port = args.port or int(ui_cfg.get("port", 0) or 0) or _free_port()
    size = (int(ui_cfg.get("width", 1440)), int(ui_cfg.get("height", 900)))
    print(f"NANO_PORT={port}", flush=True)

    # UI: existe UM ÚNICO mecanismo de arranque.
    #   --mode electron -> o Electron já tem a sua janela; o Nano não abre nada.
    #   qualquer outro  -> abrimos o navegador uma só vez, aqui.
    # Em ambos os casos o eel arranca com mode=None, ou seja, apenas serve
    # HTTP/websocket e NUNCA lança um navegador por sua conta.
    electron_mode = str(args.mode).lower() == "electron"
    if not electron_mode:
        threading.Timer(1.0, _open_browser_tab, args=(port,)).start()
        _report("UI", "READY", f"http://localhost:{port}")
    else:
        _report("UI", "READY", "electron window")
        logger.info("Modo electron: a janela é do Electron; o Nano não abre navegador.")

    print(flush=True)
    print("  Nano is running. Close this window to stop it.", flush=True)
    print(flush=True)

    try:
        eel.start("index.html", mode=None, size=size, port=port, block=True)
    except (SystemExit, KeyboardInterrupt):
        logger.info("Nano encerrado pelo utilizador.")
    except Exception as exc:
        # Surface the reason in the launcher window, not only in the log file.
        _report("UI", "FAILED", f"port {port}: {exc}")
        logger.exception("Falha a servir a UI do Nano na porta %s", port)
        raise
    finally:
        shutdown()

if __name__ == "__main__":
    main()
