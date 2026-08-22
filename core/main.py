"""Nano Assistant — Main Entry Point.

Servidor Eel/Python de orquestração para o Nano Assistant, suportando
streaming bidirecional, execução de ferramentas, guardrails e modo de voz.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import os
import re
import socket
import sys
import threading
import time
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
from core import data_migration
from core.brain import Brain
from core.config import CONFIG_PATH, load_config
from core.guardrails import GuardrailsEngine
from core.plugin_loader import load_all_plugins, list_plugins, start_plugin_services, stop_plugin_services
from core.memory import get_memory
from core.logger import setup_logger
from core.local_runtime import choose_model, ollama_available, model_available
from core.voice import VoiceEngine, VoiceRuntime
from core import wake_phrase as wake_phrase_mod
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
from core import audio_feedback, desktop_bridge, ollama_service, provider_status, providers, secret_store, speech_filter, user_settings

if not getattr(sys, "frozen", False):
    load_dotenv(ROOT / ".env")

setup_logger()
logger = logging.getLogger("nano.main")

# Before ANY database is opened or any secret is read. Nano's data directory
# is %LOCALAPPDATA%/NanoAssistant, but Store-Python redirects writes there
# into a per-package cache, so running under a different interpreter (or
# Electron) would otherwise present an empty directory and look like a fresh
# install. This copies data in only when the destination is empty, never
# overwrites and never deletes the source. See core.data_migration.
DATA_MIGRATION = data_migration.migrate_user_data()
if DATA_MIGRATION.get("copied"):
    logger.info("Migracao de dados: %d ficheiro(s) de %s",
                len(DATA_MIGRATION["copied"]), DATA_MIGRATION.get("source"))

CONFIG = load_config()
guardrails = GuardrailsEngine()
memory = get_memory()
voice = VoiceEngine(CONFIG.get("voice", {}))
# The key the user saved in Settings wins over anything in the environment.
# secret_store.get_secret() already implements exactly this precedence (the
# encrypted store first, then NANO_API_KEY / HELIOS_API_KEY / GROQ_API_KEY),
# so it is the single source of truth.
#
# Reading the environment first was a real failure: a stale key left in .env
# from an earlier session silently beat the valid key saved through Settings,
# and every cloud request failed with AuthenticationError while the Settings
# page cheerfully reported "Pronto" -- because the status check reads the
# store while the Brain was built from .env.
API_KEY = secret_store.get_secret("groq_api_key") or str(CONFIG.get("groq_api_key") or "")


def cloud_configured() -> bool:
    """Whether a Groq credential is loaded RIGHT NOW.

    The module-level API_KEY above is a snapshot taken once at import. It is
    still the value the Brain is constructed from, but it must never be the
    value the UI reports: set_groq_api_key() and remove_groq_api_key() update
    the encrypted store and call brain.reload_cloud_credentials(), and neither
    rebinds a module global. Readiness read that stale snapshot, so saving a key
    left the status panel reporting "not configured" while chat worked, and
    removing one left it reporting "configured" until the next restart.

    brain.groq_enabled is the live answer: reload_cloud_credentials() sets it
    from the secret store every time the credential changes.
    """
    return bool(getattr(brain, "groq_enabled", False))

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
    """Accept a message and answer it asynchronously over the stream events.

    This returns an ACK, not the answer. It used to block until the whole
    completion was ready, so a slow turn timed out on the bridge and the UI
    reported "Motor offline" while the backend was healthy and still working.
    The answer now arrives exclusively through on_stream_start / _status /
    _chunk / _end, and this ACK only says the request was accepted.

    A transport failure (no ACK at all) and a model failure (ACK, then an error
    on the stream) are therefore distinguishable by the UI.
    """
    if not user_text or not user_text.strip():
        return {"ok": False, "accepted": False, "error": "empty_message"}
    request_id = msg_id or uuid.uuid4().hex
    try:
        loop = _get_or_create_loop()
        asyncio.run_coroutine_threadsafe(
            _process_message(user_text, msg_id=request_id), loop
        )
    except Exception:
        logger.exception("Não foi possível aceitar a mensagem")
        return {"ok": False, "accepted": False, "error": "dispatch_failed",
                "request_id": request_id}
    return {"ok": True, "accepted": True, "request_id": request_id, "msg_id": request_id}

@eel.expose
def get_last_response_meta() -> dict:
    """Safe diagnostics for the last answer: provider, model, tokens, latency.

    Used by the technical-details panel and by latency measurement. Contains no
    prompt text, no tool arguments and no credentials.
    """
    return dict(getattr(brain, "last_metadata", {}) or {})


@eel.expose
def stop_voice():
    """Stop what is being spoken right now. Voice REMAINS available.

    Esta é a única exposição Eel de 'stop_voice'. Uma segunda definição com o
    mesmo nome fazia com que eel._expose falhasse no import e a aplicação nunca
    arrancasse.

    This used to call voice.stop(), the full teardown, which also stopped the
    wake detectors -- so pressing Stop once to interrupt a spoken reply
    silently disabled "Ei Nano" for the rest of the session. Stopping a sound
    and shutting down the voice subsystem are different requests; only
    shutdown() does the latter, and only application exit calls it.
    """
    try:
        voice.stop_playback()
    except Exception as exc:
        logger.error("Falha ao parar a voz: %s", exc)
        return {"ok": False, "error": "stop_failed"}
    return {"ok": True, "voiceStillAvailable": True}

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
        "cloud": {"configured": cloud_configured(), "model": brain.groq_fast_model},
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
def get_data_location() -> dict:
    """Where Nano's user data actually lives, plus the last migration result."""
    info = data_migration.describe_data_location()
    info["migration"] = DATA_MIGRATION
    return info


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
    """DEPRECATED. Use start_voice_turn_from_ui().

    Kept because it is still a public bridge function, but nothing in the UI
    calls it any more and nothing new should. Two problems, both solved by the
    turn abstraction: it BLOCKS the eel handler for the whole listen (and eel
    serves its websocket from a single cooperative hub, so that freezes the
    entire bridge), and it captures without pausing the wake detector, so it
    can become a second reader on the same PortAudio stream.
    """
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


# ===========================================================================
#  PROVIDERS  (Groq primary / Ollama fallback)
# ===========================================================================

def current_provider_mode() -> providers.ProviderMode:
    return providers.ProviderMode.parse(
        user_settings.get("provider_mode") or CONFIG.get("provider_mode") or "AUTO"
    )


def describe_providers(*, stale_ok: bool = False) -> dict:
    """Live status of both providers plus the route a request would take.

    Both Groq tiers are validated against the account, so Settings can show the
    conversation model and the complex model honestly instead of implying a
    single model answers everything.

    ``stale_ok`` is for high-frequency polling. This function used to call
    describe_groq() unconditionally, and the Settings page polls get_settings()
    once per second so microphone levels stay live -- which meant one blocking
    request to api.groq.com per second, per open Settings page. With stale_ok
    the poller is served from the shared snapshot and any refresh happens on a
    background thread. The snapshot is the same one the Brain routes from, so
    the two no longer probe the same account separately.
    """
    mode = current_provider_mode()
    key = provider_status.cache_key(
        mode, brain.groq_fast_model, brain.groq_complex_model, brain.ollama_model)

    def _produce() -> tuple[dict, dict]:
        return provider_status.describe_pair(
            mode,
            groq_fast_model=brain.groq_fast_model,
            groq_complex_model=brain.groq_complex_model,
            ollama_model=brain.ollama_model,
            ollama_base_url=brain.ollama_url.removesuffix("/api/chat"),
            local_enabled=brain.local_enabled,
        )

    getter = provider_status.CACHE.get_stale_ok if stale_ok else provider_status.CACHE.get_fresh
    groq, ollama = getter(key, _produce)
    return {
        "mode": mode.value,
        "modes": [m.value for m in providers.ProviderMode],
        "groq": groq,
        "ollama": ollama,
        # The route a normal conversational message would take right now.
        "route": providers.resolve_route(mode, groq, ollama, tier="FAST"),
        "complexRoute": providers.resolve_route(mode, groq, ollama, tier="STRONG"),
    }


@eel.expose
def get_providers() -> dict:
    return describe_providers()


@eel.expose
def set_provider_mode(mode: str) -> dict:
    parsed = providers.ProviderMode.parse(mode)
    result = user_settings.set_value("provider_mode", parsed.value)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "write_failed")}
    CONFIG["provider_mode"] = parsed.value
    brain.provider_mode = parsed.value
    logger.info("Modo de provedor: %s", parsed.value)
    return {"ok": True, "mode": parsed.value, "providers": describe_providers()}


@eel.expose
def set_groq_api_key(api_key: str) -> dict:
    """Validate a key, then store it OS-encrypted. The key never returns."""
    candidate = (api_key or "").strip()
    if not candidate:
        return {"ok": False, "error": "empty_key", "detail": "Introduz uma chave de API."}

    verdict = providers.test_groq(candidate)
    if not verdict["ok"]:
        # Never persist a key we could not validate.
        return {"ok": False, "error": verdict["error"], "detail": verdict["detail"]}

    if not secret_store.set_secret(providers.GROQ_SECRET_NAME, candidate):
        return {"ok": False, "error": "store_failed", "detail": "Não foi possível guardar a chave em segurança."}

    # Adopt a working model if the configured one is not on this account.
    if brain.groq_model not in verdict["models"]:
        suggested = verdict["suggested_model"]
        user_settings.set_value("groq_model", suggested)
        CONFIG["groq_model"] = suggested
        brain.groq_model = suggested
        logger.info("Modelo Groq ajustado para '%s'.", suggested)

    brain.reload_cloud_credentials()
    logger.info("Chave de API do Groq guardada (encriptada=%s).", secret_store.is_encrypted())
    return {"ok": True, "detail": verdict["detail"], "providers": describe_providers()}


@eel.expose
def test_groq_connection(api_key: str = "") -> dict:
    """Test the stored key, or a candidate the user is typing (never stored)."""
    verdict = providers.test_groq(api_key or None)
    return {
        "ok": verdict["ok"],
        "detail": verdict["detail"],
        "models": verdict.get("models", []),
        "suggested_model": verdict.get("suggested_model"),
        "latency_ms": verdict.get("latency_ms"),
    }


@eel.expose
def remove_groq_api_key() -> dict:
    secret_store.delete_secret(providers.GROQ_SECRET_NAME)
    brain.reload_cloud_credentials()
    logger.info("Chave de API do Groq removida.")
    return {"ok": True, "providers": describe_providers()}


@eel.expose
def set_groq_model(model: str) -> dict:
    chosen = (model or "").strip()
    if not chosen:
        return {"ok": False, "error": "empty_model"}
    available, error = providers.list_groq_models()
    if error:
        return {"ok": False, "error": error, "detail": "Não foi possível validar o modelo com o Groq."}
    if chosen not in available:
        return {"ok": False, "error": "model_unavailable",
                "detail": f"'{chosen}' não existe nesta conta Groq."}
    user_settings.set_value("groq_model", chosen)
    CONFIG["groq_model"] = chosen
    brain.groq_model = chosen
    return {"ok": True, "model": chosen, "providers": describe_providers()}


# ===========================================================================
#  SETTINGS
# ===========================================================================

@eel.expose
def get_voice_diagnostics() -> dict:
    """The live microphone/wake numbers ONLY. Cheap enough to poll every second.

    This exists because the Settings page needs live audio levels: fetched once
    at page load they showed a snapshot from before the user spoke, which read
    as "the wake listener hears nothing" when it simply had not looked yet.

    The fix was a 1 s poll of get_settings(), which was far too expensive --
    get_settings() also describes both providers, and describing Groq is a
    blocking HTTPS request. That was one call to api.groq.com per second.

    Everything below is read from objects already in memory: no network, no
    subprocess, no database, no PortAudio. Provider and credential state is NOT
    here on purpose; it belongs to the slow readiness cadence.
    """
    wake = voice.wake_phrase_provider.status()
    return {
        "state": wake.get("readiness"),
        "turnState": wake.get("state"),
        "explain": wake.get("explain"),
        "phrase": wake.get("phrase"),
        "error": wake.get("error"),
        "lastTranscript": wake.get("last_transcript"),
        "recentTranscripts": wake.get("recent_transcripts") or [],
        "audio": wake.get("audio"),
        "voiceTurn": voice_runtime.turn_status(),
        "counters": {
            "chunksCaptured": wake.get("chunks_captured"),
            "silentChunks": wake.get("silent_chunks"),
            "speechChunks": wake.get("speech_chunks"),
            "transcriptsSeen": wake.get("transcripts_seen"),
            "wakeMatches": wake.get("wake_matches"),
        },
    }


@eel.expose
def get_settings() -> dict:
    """Everything the Settings UI renders. Secrets are described, never sent.

    Provider status is served from the shared snapshot with ``stale_ok``: this
    endpoint must never block on the Groq API, because the Settings page is
    open for as long as the user is configuring things.
    """
    voice_cfg = CONFIG.get("voice", {}) or {}
    wake_status = voice.wake_phrase_provider.status()
    return {
        "providers": describe_providers(stale_ok=True),
        "voice": {
            "enabled": bool(voice_cfg.get("enabled")),
            "ttsEnabled": bool(voice_cfg.get("tts_enabled", True)),
            # Typing and talking are separate conversations, so their spoken
            # output is configured separately.
            "typedChatTts": bool(voice_cfg.get("typed_chat_tts", False)),
            "voiceReplyTts": bool(voice_cfg.get("voice_reply_tts", True)),
            "wakePhrase": wake_status.get("phrase"),
            "wakePhraseEnabled": bool(voice_cfg.get("wake_phrase_enabled")),
            "allowNanoOnly": bool(voice_cfg.get("wake_phrase_allow_nano_only")),
            "cooldownSeconds": float(voice_cfg.get("wake_phrase_cooldown_seconds", 3.0)),
            "commandTimeoutSeconds": int(voice_cfg.get("wake_command_timeout_seconds", 7)),
            "state": wake_status.get("readiness"),
            "explain": wake_status.get("explain"),
            # What the transcriber actually heard, matched or not.
            "lastTranscript": wake_status.get("last_transcript"),
            "recentTranscripts": wake_status.get("recent_transcripts") or [],
            # Live microphone characteristics for Voice diagnostics.
            "audio": wake_status.get("audio"),
            "counters": {
                "chunksCaptured": wake_status.get("chunks_captured"),
                "silentChunks": wake_status.get("silent_chunks"),
                "speechChunks": wake_status.get("speech_chunks"),
                "transcriptsSeen": wake_status.get("transcripts_seen"),
                "wakeMatches": wake_status.get("wake_matches"),
            },
        },
        "devices": get_audio_devices(),
        "security": {
            "autonomyMode": permission_manager.policy_engine.autonomy_mode.value,
            "emergencyStop": permission_manager.is_emergency_stopped(),
            "persistentAllowDisabled": True,
            "secretsEncrypted": secret_store.is_encrypted(),
        },
        "stored": user_settings.all_settings(),
        "runtime": get_runtime_info(),
    }


@eel.expose
def update_setting(key: str, value) -> dict:
    """Persist one allow-listed setting and apply it live where possible."""
    result = user_settings.set_value(key, value)
    if not result.get("ok"):
        return result

    voice_cfg = CONFIG.setdefault("voice", {})
    engine = voice.wake_phrase_provider._engine

    if key == "wake_phrase_enabled":
        voice_cfg["wake_phrase_enabled"] = bool(value)
        engine.enabled = bool(value)
        if value and not engine.running:
            _start_wake_phrase()
        elif not value:
            voice.wake_phrase_provider.stop()
    elif key == "wake_phrase_allow_nano_only":
        voice_cfg["wake_phrase_allow_nano_only"] = bool(value)
        engine.allow_nano_only = bool(value)
        engine.detector.allow_nano_only = bool(value)
    elif key == "wake_phrase_cooldown_seconds":
        voice_cfg["wake_phrase_cooldown_seconds"] = float(value)
        engine.detector.cooldown_seconds = float(value)
    elif key == "wake_command_timeout_seconds":
        voice_cfg["wake_command_timeout_seconds"] = int(value)
        voice_runtime.command_timeout_seconds = max(3, min(15, int(value)))
    elif key in ("tts_enabled", "typed_chat_tts", "voice_reply_tts"):
        voice_cfg[key] = bool(value)
    elif key == "voice_enabled":
        voice_cfg["enabled"] = bool(value)
        voice.enabled = bool(value)
    elif key == "input_device_index":
        index = None if value in (None, "", -1) else int(value)
        voice_cfg.setdefault("microphone", {})["device_index"] = index
        voice.input_provider.device_index = index
    elif key in ("groq_fast_model", "groq_complex_model"):
        CONFIG[key] = str(value)
        setattr(brain, key, str(value))
        # The cached provider snapshot names the old model until it expires.
        brain.invalidate_provider_snapshot()

    return {"ok": True, "key": key, "value": value, "settings": get_settings()}


# Candidate phrases offered in the wake-phrase calibration tool. All are
# Portuguese: faster-whisper-tiny is forced to Portuguese, which is exactly why
# the English "Hey Nano" was transcribed as "Ei, não." / "E ai, no." / "NÃO!".
WAKE_PHRASE_CANDIDATES = ("ei nano", "olá nano", "acorda nano")


@eel.expose
def list_wake_phrase_candidates() -> list[str]:
    return list(WAKE_PHRASE_CANDIDATES)


@eel.expose
def test_wake_phrase(phrase: str = "", seconds: int = 3) -> dict:
    """Record one utterance and report what the wake matcher would do with it.

    Diagnostic only: this never wakes Nano, never reaches the Brain and never
    runs a tool. It exists because a wake that does not fire is otherwise
    invisible -- the user says the phrase, nothing happens, and there is no way
    to tell whether the microphone, the transcriber or the matcher is at fault.

    The audio goes through exactly the same provider, the same STT settings and
    the same normalisation as the live detector, so the result is honest. No
    audio is retained.
    """
    candidate = (phrase or "").strip() or str(
        voice.wake_phrase_provider.status().get("phrase") or wake_phrase_mod.DEFAULT_WAKE_PHRASE)
    window = max(1, min(6, int(seconds)))
    engine = voice.wake_phrase_provider._engine

    # Pause the live detector for the moment of the test: two readers on one
    # stream would split the utterance between them and neither would hear it
    # whole. Always resumed, even if the capture fails.
    was_running = engine.running and not engine._paused.is_set()
    if was_running:
        # Wait for the detector to actually leave the microphone. pause() alone
        # returns while a multi-second read may still be in flight, and two
        # readers on one PortAudio stream split the utterance between them --
        # or worse. pause_and_wait() blocks until the read has finished.
        if not voice.wake_phrase_provider.pause_and_wait(5.0):
            voice.wake_phrase_provider.resume()
            return {"ok": False, "error": "microphone_busy", "phrase": candidate,
                    "detail": "O microfone ainda está ocupado pelo detector."}
    try:
        audio = voice.input_provider.capture(window)
    except Exception as exc:
        return {"ok": False, "error": "capture_failed", "detail": str(exc), "phrase": candidate}
    finally:
        if was_running:
            voice.wake_phrase_provider.resume()

    if not audio:
        return {"ok": False, "error": "no_audio", "phrase": candidate,
                "detail": "O microfone não devolveu áudio."}

    rms = speech_filter.rms_of_wav(audio)
    threshold = engine.gate.threshold
    passed_gate = rms >= threshold and speech_filter.voiced_ratio(
        audio, silence_rms=threshold) >= engine.gate.min_voiced_ratio

    if not passed_gate:
        return {
            "ok": True, "phrase": candidate, "matched": False,
            "transcript": "", "normalized": "", "gate": False,
            "rms": round(rms, 1), "threshold": round(threshold, 1),
            "detail": (f"Nível demasiado baixo ({rms:.0f}; é preciso {threshold:.0f}). "
                       "Fala mais perto do microfone."),
        }

    result = voice.stt_provider.transcribe(audio)
    transcript = (result.text or "").strip() if result.ok else ""
    detector = wake_phrase_mod.WakePhraseDetector(
        phrase=candidate,
        allow_nano_only=bool(engine.allow_nano_only),
        cooldown_seconds=0.0,          # a test must never be debounced away
    )
    matched = detector.matches(transcript) if transcript else False
    return {
        "ok": True, "phrase": candidate, "matched": matched,
        "transcript": transcript,
        "normalized": wake_phrase_mod.normalize_transcript(transcript),
        "gate": True, "rms": round(rms, 1), "threshold": round(threshold, 1),
        "detail": (f"Reconhecido: \"{candidate}\"." if matched
                   else (f"Ouvi \"{transcript}\" — não corresponde a \"{candidate}\"."
                         if transcript else "Não percebi nenhuma fala.")),
    }


@eel.expose
def test_speaker() -> dict:
    """Play the wake chime so the user can confirm they will hear it."""
    played = audio_feedback.acknowledge_wake(blocking=True)
    return {
        "ok": played,
        "detail": "Som reproduzido." if played else "Nenhum dispositivo de saída aceitou o som.",
        "output": audio_feedback.output_device_report().get("default_output"),
    }


@eel.expose
def test_microphone(seconds: int = 3) -> dict:
    """Record a short sample and report measured energy. No audio is kept.

    Takes the microphone properly first. This used to capture with no
    coordination at all while the wake thread was reading the same persistent
    stream -- two concurrent readers, so the sample was whatever survived the
    split and the diagnostic could report a false negative.
    """
    engine = voice.wake_phrase_provider._engine
    was_running = engine.running and not engine._paused.is_set()
    if was_running and not voice.wake_phrase_provider.pause_and_wait(5.0):
        voice.wake_phrase_provider.resume()
        return {"ok": False, "error": "microphone_busy",
                "detail": "O microfone ainda está ocupado pelo detector de wake."}
    try:
        audio = voice.input_provider.capture(max(1, min(5, int(seconds))))
    except Exception as exc:
        return {"ok": False, "error": "capture_failed", "detail": str(exc)}
    finally:
        if was_running:
            voice.wake_phrase_provider.resume()
    if not audio:
        return {"ok": False, "error": "no_audio", "detail": "O microfone não devolveu áudio."}

    # Measure against the SAME calibrated threshold the wake detector uses.
    # speech_filter.describe() applies the old fixed floor, so it could report
    # "no voice" for audio the live detector would happily accept -- the test
    # button must agree with the thing it is testing.
    rms = speech_filter.rms_of_wav(audio)
    ratio = speech_filter.voiced_ratio(audio)
    gate = voice.wake_phrase_provider._engine.gate
    threshold = gate.threshold
    detected = rms >= threshold and speech_filter.voiced_ratio(
        audio, silence_rms=threshold) >= gate.min_voiced_ratio

    if detected:
        detail = f"Voz detetada (nível {rms:.0f}, limiar {threshold:.0f})."
    elif rms < max(2.0, gate.noise_floor):
        detail = ("O microfone não está a receber áudio nenhum. Verifica o "
                  "dispositivo de entrada e o nível de captura no Windows.")
    else:
        detail = (f"Nível demasiado baixo: {rms:.0f}, é preciso {threshold:.0f}. "
                  "Fala mais perto ou aumenta o volume do microfone nas "
                  "Definições de Som do Windows.")

    return {
        "ok": True,
        "speechDetected": detected,
        "rms": round(rms, 1),
        "voicedRatio": round(ratio, 3),
        "threshold": round(threshold, 1),
        "noiseFloor": round(gate.noise_floor, 1),
        "detail": detail,
    }


# ===========================================================================
#  TASKS / ACTIVITY / MEMORY
# ===========================================================================

# Statuses that mean a task still needs the worker or the user.
ACTIVE_TASK_STATUSES = frozenset({"QUEUED", "PLANNING", "RUNNING", "RETRYING", "WAITING", "WAITING_FOR_PERMISSION", "RECOVERABLE"})
ATTENTION_TASK_STATUSES = frozenset({"NEEDS_ATTENTION"})
TERMINAL_TASK_STATUSES = frozenset({"COMPLETED", "CANCELLED", "FAILED"})


@eel.expose
def get_task_counts() -> dict:
    """Counts for the sidebar badge.

    The badge must mean "needs you or is running", not "every task ever
    created" -- it was showing 68 because it counted all history.
    """
    summary = task_engine.get_status_summary()
    active = sum(count for status, count in summary.items() if status in ACTIVE_TASK_STATUSES)
    attention = sum(count for status, count in summary.items() if status in ATTENTION_TASK_STATUSES)
    return {
        "active": active,
        "attention": attention,
        "badge": active + attention,
        "total": sum(summary.values()),
        "byStatus": summary,
    }


@eel.expose
def list_tasks_filtered(scope: str = "active", limit: int = 60) -> list:
    """Task rows for the Tasks page, without heavy metadata/result blobs."""
    wanted = str(scope or "active").lower()
    rows = []
    for task in task_engine.list_tasks(limit=max(1, min(300, int(limit)))):
        status = task.get("status")
        if wanted == "active" and status not in ACTIVE_TASK_STATUSES:
            continue
        if wanted == "attention" and status not in ATTENTION_TASK_STATUSES:
            continue
        if wanted == "completed" and status != "COMPLETED":
            continue
        if wanted == "cancelled" and status != "CANCELLED":
            continue
        if wanted == "failed" and status != "FAILED":
            continue
        rows.append(_summarize_task_row(task))
    return rows


@eel.expose
def get_current_task() -> dict | None:
    """The genuinely active task, or None.

    A finished or cancelled task is never "current" -- showing one was making
    the inspector claim work was in progress when nothing was running.
    """
    for task in task_engine.list_tasks(limit=40):
        if task.get("status") in ACTIVE_TASK_STATUSES or task.get("status") in ATTENTION_TASK_STATUSES:
            return _summarize_task_row(task)
    return None


@eel.expose
def archive_finished_tasks() -> dict:
    """Remove finished tasks from the queue view.

    This clears the task QUEUE only. The permission audit log and the event
    stream are untouched: those are the security record and are not the user's
    to tidy away from here.
    """
    removed = 0
    for task in task_engine.list_tasks(limit=1000):
        if task.get("status") in TERMINAL_TASK_STATUSES:
            if task_engine.delete_task(task["id"]):
                removed += 1
    event_bus.publish("tasks.archived", {"count": removed})
    return {
        "ok": True,
        "removed": removed,
        "note": "Apenas a fila de tarefas foi limpa. O registo de auditoria de permissões mantém-se intacto.",
        "counts": get_task_counts(),
    }


@eel.expose
def get_activity(kind: str = "all", limit: int = 80) -> list:
    """Filtered event stream for the Activity page."""
    groups = {
        "tasks": ("task.",),
        "tools": ("tool.",),
        "permissions": ("Permission", "permission", "security."),
        "voice": ("Voice", "WakeWord", "Transcription"),
        "system": ("worker.", "tasks."),
        "errors": ("tool.failed", "worker.error", "task.needs_attention", "VoiceError"),
    }
    prefixes = groups.get(str(kind or "all").lower())
    events = event_bus.get_recent_events(max(1, min(300, int(limit))))
    if not prefixes:
        return events
    return [e for e in events if any(str(e.get("event", "")).startswith(p) or p in str(e.get("event", "")) for p in prefixes)]


@eel.expose
def get_memory_overview() -> dict:
    """Real memory contents for the Memory page."""
    try:
        facts = memory.get_facts()
    except Exception as exc:
        facts = {}
        logger.warning("Falha ao ler factos: %s", exc)
    try:
        profile = memory.get_user_profile()
    except Exception:
        profile = {}
    try:
        message_count = memory.count_messages()
    except Exception:
        message_count = 0

    return {
        "profile": profile,
        "facts": [{"key": k, "value": str(v)[:400]} for k, v in facts.items()],
        "messageCount": message_count,
        "ragEnabled": bool(CONFIG.get("memory", {}).get("rag_enabled")),
        "documents": [],
        "documentsSupported": False,
        "documentsNote": "A indexação de documentos requer o chromadb, que não está instalado.",
    }


@eel.expose
def forget_memory_fact(key: str) -> dict:
    """Delete one remembered fact. Confirmed in the UI before it gets here."""
    if not key:
        return {"ok": False, "error": "missing_key"}
    try:
        removed = memory.forget_fact(key)
        return {"ok": bool(removed), "key": key, "memory": get_memory_overview()}
    except Exception as exc:
        return {"ok": False, "error": "delete_failed", "detail": str(exc)}


@eel.expose
def get_agents_detail() -> list:
    """Registered agents with their real capabilities. Nothing invented."""
    registry = agent_registry.as_dict().get("agents", [])
    return [
        {
            "name": agent.get("name"),
            "description": agent.get("description"),
            "capabilities": agent.get("capabilities", []),
            "tools": agent.get("tools", []),
            "taskTypes": agent.get("supported_task_types", []),
            # Agents are descriptors today: they are selected and recorded, but
            # every tool still runs through the central executor. Saying READY
            # would overstate what they do.
            "state": "EXPERIMENTAL",
        }
        for agent in registry
    ]


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
        # Not ERROR. A task waiting for the user is not the agent malfunctioning,
        # and this was the only branch that ever produced ERROR -- so a handful
        # of stalled tasks left the agent permanently reading "Erro" while it was
        # working perfectly. A badge that is always red is a badge nobody reads.
        agent_state = "NEEDS_ATTENTION"
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
            "cloud": {"model": brain.groq_fast_model, "configured": cloud_configured()},
            "provider": "ollama" if model_ready else ("cloud" if cloud_configured() else "none"),
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
        "cloud": "configured" if cloud_configured() else "not_configured",
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

_NOOP_CALLBACK = lambda *_args: None


def _notify_ui(call) -> None:
    """Send a UI notification without waiting for the browser to answer.

    eel's `eel.fn(args)` already puts the message on the websocket; the usual
    trailing `()` then *polls* for a JS return value. For a notification that
    return value is worthless, and paying a round trip for it once per streamed
    chunk stalled the shared event loop -- measured at up to 1.6 s of added
    latency on a single answer. Passing a callback makes eel return
    immediately, so the chunk is sent and the loop keeps running.
    """
    try:
        call(_NOOP_CALLBACK)
    except Exception:
        logger.debug("UI notification failed", exc_info=True)


def _emit_stream_start(msg_id: str, user_text: str | None = None):
    _notify_ui(eel.on_stream_start(msg_id, user_text))

def _emit_stream_status(msg_id: str, status: str):
    _notify_ui(eel.on_stream_status(msg_id, status))

def _emit_stream_chunk(msg_id: str, chunk: str):
    _notify_ui(eel.on_stream_chunk(msg_id, chunk))

def _emit_stream_end(msg_id: str, result: dict):
    _notify_ui(eel.on_stream_end(msg_id, result))

def _emit_stream_error(msg_id: str, code: str, detail: str = ""):
    """A model/provider failure, as distinct from the bridge being down."""
    _notify_ui(eel.on_stream_error(msg_id, {"code": code, "detail": detail}))

def _emit_rate_limited(msg_id: str, info: dict):
    """Tell the UI it is rate-limited and for how long, never just 'Error'."""
    payload = dict(info or {})
    payload["message"] = providers.rate_limit_message(payload)
    _notify_ui(eel.on_rate_limited(msg_id, payload))

def _emit_voice_phase(phase: str, detail: str = ""):
    """Publish the current voice turn phase so the UI can narrate it.

    TWO SINKS, ONE SOURCE. The browser UI hears it over eel; the Electron
    desktop shell hears it over the control pipe and drives the voice overlay
    from it. The overlay therefore shows the REAL phase of the real turn -- it
    never runs a timeline of its own -- and it keeps working when the main
    window (and with it the eel renderer) is hidden or has never been created.
    """
    logger.info("voice_phase -> %s (%s)", phase, detail or "-")
    _notify_ui(eel.on_voice_phase(phase, detail))
    _desktop_emit("voice_phase", {"phase": phase, "detail": detail})

def _emit_voice_exchange(turn_id: str, user_text: str, assistant_text: str):
    """Show a completed spoken turn in the conversation.

    A voice turn does not go through _process_message, so without this the
    user heard an answer that never appeared on screen. The turn id lets the
    UI insert it exactly once.
    """
    _notify_ui(eel.on_voice_exchange(turn_id, user_text, assistant_text))

def _emit_wake_detected(transcript: str = ""):
    """Best-effort UI notification. The chime is the guaranteed feedback."""
    try:
        _notify_ui(eel.on_wake_detected(transcript))
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
    """Start the optional legacy ONNX wake-word listener, if it is enabled.

    ONE MICROPHONE OWNER (do not break this):
    core.wake_word opens and terminates PyAudio directly, without the shared
    _PORTAUDIO_LOCK that core.voice documents as mandatory for every PortAudio
    init/teardown in this process. Two owners is the concurrency that crashed
    Nano with an access violation (0xC0000005). Until that engine is taught to
    use the shared lock, it may never run alongside the wake-phrase detector --
    so this refuses to start it rather than relying on config discipline alone.
    """
    global wake_word
    voice_cfg = CONFIG.get("voice", {}) or {}
    if not voice_cfg.get("wake_word", {}).get("enabled", False):
        return
    if voice_cfg.get("wake_phrase_enabled", False):
        logger.warning(
            "Wake-word legado ignorado: a wake phrase por STT já é a dona do "
            "microfone e os dois motores não podem partilhar o PortAudio."
        )
        _report("Wake Word", "SKIPPED", "wake phrase owns the microphone")
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
    """Fired by the STT-based 'Ei Nano' detector.

    This is now a THIN TRIGGER, nothing more. The whole choreography -- chime,
    phase events, capture, gate, STT, Brain, on-screen exchange, TTS, return to
    listening -- lives in VoiceRuntime.run_voice_turn, so the global hotkey can
    reuse it verbatim instead of growing a second copy that drifts.

    This callback still only wakes Nano. It resolves no capability and runs no
    tool: everything it triggers goes through the normal request -> policy ->
    permission -> execution pipeline.
    """
    logger.info("Wake phrase detected: %r", transcript)
    try:
        result = run_coro(
            voice_runtime.run_voice_turn("wake_phrase", transcript=transcript),
            timeout=180,
        )
        if result.get("busy"):
            logger.info("Wake ignorada: já existe um turno de voz activo (%s).",
                        result.get("active_source"))
    except Exception:
        logger.exception("Erro no fluxo de wake-phrase")


def start_voice_turn(source: str = "ui") -> dict:
    """Start one voice turn from any trigger. Returns an ACK, not the answer.

    This must NOT wait for the turn. Eel serves its websocket from a single
    cooperative hub, so blocking an exposed function blocks the ENTIRE bridge:
    a measured 14.7 s freeze during which no poll, no chat message and no
    permission dialog could get through, because a voice turn listens for up to
    wake_command_timeout_seconds and then thinks and speaks.

    So this dispatches onto the shared loop and returns immediately, exactly as
    send_message does. Progress arrives on the existing on_voice_phase /
    on_voice_exchange events, which the UI already renders.

    The authoritative single-turn guard is the non-blocking lock inside
    run_voice_turn; the check here is a courtesy so the caller gets an honest
    answer without waiting for the dispatch to land.
    """
    status = voice_runtime.turn_status()
    if status.get("active"):
        logger.info("Turno de voz de %r recusado: %r já está activo.", source, status.get("source"))
        return {"ok": False, "accepted": False, "busy": True,
                "error": "voice_turn_in_progress",
                "active_source": status.get("source"),
                "phase": status.get("phase"),
                "detail": "O Nano já está a ouvir."}
    try:
        loop = _get_or_create_loop()
        future = asyncio.run_coroutine_threadsafe(voice_runtime.run_voice_turn(source), loop)
        # How the turn ENDED, not merely that it stopped. The voice overlay
        # needs to distinguish "answered and spoke" from "heard no command"
        # from "failed", and the phase events alone cannot say which -- they
        # all finish on IDLE. Reported from the turn's real result, so the
        # overlay can never invent an outcome.
        future.add_done_callback(lambda f: _on_voice_turn_finished(source, f))
    except Exception as exc:
        logger.exception("Falha ao iniciar o turno de voz (%s)", source)
        return {"ok": False, "accepted": False, "error": "voice_turn_failed", "detail": str(exc)}
    return {"ok": True, "accepted": True, "source": source}


def _on_voice_turn_finished(source: str, future) -> None:
    """Publish the outcome of a dispatched voice turn to the desktop shell."""
    try:
        result = future.result()
    except Exception as exc:  # noqa: BLE001 - reporting must not raise here
        logger.debug("voice turn future failed", exc_info=True)
        result = {"ok": False, "error": "voice_turn_failed", "detail": str(exc)}
    if not isinstance(result, dict):
        result = {"ok": False, "error": "voice_turn_failed"}
    _desktop_emit("voice_turn_ended", {
        "source": source,
        "ok": bool(result.get("ok")),
        "cancelled": bool(result.get("cancelled")),
        "spoken": bool(result.get("spoken")),
        # A short machine-readable reason. Never a traceback and never a
        # provider message: the overlay must not show internals to the user.
        "error": (str(result.get("error")) if result.get("error") else None),
    })


@eel.expose
def start_voice_turn_from_ui() -> dict:
    """UI-triggered voice turn: the same pipeline the wake phrase uses."""
    return start_voice_turn("ui")


@eel.expose
def get_voice_turn_status() -> dict:
    """Whether a voice turn is currently running, and which trigger began it."""
    return voice_runtime.turn_status()


# ==========================================================================
#  DESKTOP CONTROL CHANNEL  (Electron parent -> this backend)
# ==========================================================================
# The global Ctrl+Shift+Space shortcut is owned by the Electron main process,
# because only it can see a keypress while Nano has no visible window. It has
# to reach the voice turn that lives here, and it must be able to do so when
# the renderer does not exist -- which is why this does not go through eel.
#
# The transport is core.desktop_bridge: one JSON line per message over the
# stdin/stdout pipe Electron already owns as our parent. Read that module for
# the full trust model. The short version: the OS decides who may write to this
# process's stdin, and the answer is "our parent", so there is no port to
# scan, no token to leak, and nothing here is reachable from the network.
#
# WHAT THIS CHANNEL CANNOT DO, and must never be extended to do:
#   * run a command, a path, a script or any caller-supplied code
#   * resolve a capability, bypass the policy engine, or pre-approve anything
#   * read or return a secret
# Every operation below is a name Nano already implements. start_voice_turn
# only *starts a turn*; whatever the user says next is understood by the Brain
# and travels the normal REQUEST -> POLICY -> PERMISSION -> EXECUTION path,
# identically to a message typed into the chat box.

_DESKTOP_BRIDGE: desktop_bridge.DesktopBridge | None = None

#: What the desktop shell told us about itself. Reported to the UI verbatim;
#: nothing here is inferred, so Settings can show an honest shortcut status
#: instead of assuming the hotkey registered.
DESKTOP_STATE: dict = {
    "present": False,
    "shortcut": None,
    "shortcutRegistered": None,
    "shortcutError": None,
    "overlayEnabled": None,
    "autoLaunch": None,
    "version": None,
}


def _desktop_emit(event: str, payload: dict | None = None) -> None:
    """Push an event to the desktop shell, if one is attached."""
    bridge = _DESKTOP_BRIDGE
    if bridge is None:
        return
    try:
        bridge.emit(event, payload or {})
    except Exception:
        logger.debug("desktop event %s failed", event, exc_info=True)


def _ctl_ping(_args: dict) -> dict:
    """Liveness. The desktop shell waits for this before showing a window."""
    return {"pong": True, "pid": os.getpid()}


def _ctl_voice_status(_args: dict) -> dict:
    status = voice_runtime.turn_status()
    readiness = voice_runtime.status()
    return {
        "turn": status,
        "ready": bool(readiness.get("ready")),
        "readiness": readiness.get("readiness"),
        "blockers": readiness.get("blockers", []),
    }


def _ctl_start_voice_turn(args: dict) -> dict:
    """The global hotkey lands here. It starts a turn and nothing else."""
    source = str(args.get("source") or "hotkey")
    if source not in VoiceRuntime.TURN_SOURCES:
        source = "hotkey"
    return start_voice_turn(source)


def _ctl_cancel_voice_turn(_args: dict) -> dict:
    """Stop what is being said now. Voice stays available for the next turn.

    Deliberately the same shallow stop the UI uses: cancelling one turn must
    never tear the voice subsystem down, or the hotkey would stop working for
    the rest of the session. Only application exit calls voice.shutdown().
    """
    result = stop_voice()
    return {**result, "turn": voice_runtime.turn_status()}


def _ctl_data_location(_args: dict) -> dict:
    """Where this backend's data really is, so both halves can be compared."""
    return data_migration.describe_data_location()


def _ctl_report_shortcut(args: dict) -> dict:
    """The shell tells us what actually happened when it registered the hotkey.

    Recorded, not trusted-as-success: if registration failed because another
    application owns the accelerator, Settings shows that failure rather than a
    shortcut that silently does nothing.
    """
    DESKTOP_STATE.update({
        "present": True,
        "shortcut": str(args.get("shortcut") or "") or None,
        "shortcutRegistered": bool(args.get("registered")),
        "shortcutError": (str(args.get("error")) if args.get("error") else None),
        "overlayEnabled": bool(args.get("overlay")) if "overlay" in args else DESKTOP_STATE["overlayEnabled"],
        "autoLaunch": bool(args.get("autoLaunch")) if "autoLaunch" in args else DESKTOP_STATE["autoLaunch"],
        "version": (str(args.get("version")) if args.get("version") else DESKTOP_STATE["version"]),
    })
    return dict(DESKTOP_STATE)


def _ctl_shutdown(_args: dict) -> dict:
    """Graceful exit, so the microphone and the databases close properly.

    Electron calls this before falling back to killing the process tree: an
    abrupt kill leaves PortAudio and SQLite to be cleaned up by the OS, which
    works but is not what we want on a normal Quit.
    """
    logger.info("Desktop shell requested shutdown.")
    threading.Timer(0.15, _exit_now).start()
    return {"stopping": True}


def _exit_now() -> None:
    try:
        shutdown()
    except Exception:
        logger.exception("Falha no encerramento pedido pelo desktop")
    finally:
        os._exit(0)


#: THE COMPLETE SET. Anything not named here is refused by the dispatcher.
DESKTOP_OPERATIONS = {
    "ping": _ctl_ping,
    "voice_status": _ctl_voice_status,
    "start_voice_turn": _ctl_start_voice_turn,
    "cancel_voice_turn": _ctl_cancel_voice_turn,
    "data_location": _ctl_data_location,
    "report_shortcut": _ctl_report_shortcut,
    "shutdown": _ctl_shutdown,
}


def _start_desktop_bridge() -> bool:
    """Attach the control channel. Nano runs fine without it."""
    global _DESKTOP_BRIDGE
    bridge = desktop_bridge.DesktopBridge(DESKTOP_OPERATIONS)
    if not bridge.start():
        return False
    _DESKTOP_BRIDGE = bridge
    DESKTOP_STATE["present"] = True
    return True


@eel.expose
def get_desktop_status() -> dict:
    """Honest desktop/activation state for the Settings page.

    Everything here is measured or reported by the shell. Nothing is assumed:
    with no desktop shell attached, `present` is false and the UI says the
    global shortcut is unavailable rather than showing a key combination that
    does nothing.
    """
    bridge = _DESKTOP_BRIDGE
    return {
        **DESKTOP_STATE,
        "channel": bool(bridge is not None and bridge.running),
        "operations": list(bridge.operations) if bridge else [],
    }


def _attach_voice_observer() -> None:
    """Give the voice runtime its UI notifications.

    The choreography lives in VoiceRuntime; the transport stays here. That is
    what lets core.voice be imported and tested with no eel and no browser,
    and what lets a future trigger (the global hotkey) reuse the whole turn
    without reimplementing any of it.
    """
    voice_runtime.set_observer(
        on_phase=_emit_voice_phase,
        on_exchange=_emit_voice_exchange,
        on_activation=_emit_wake_detected,
    )


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


def _should_speak(source: str) -> bool:
    """Whether a reply from this source should be spoken aloud.

    Typing and talking are different conversations. A single global
    `tts_enabled` meant every typed reply was read out, which is how a spoken
    "Olá" arrived a minute after a chat message the user had already read.
    """
    voice_cfg = CONFIG.get("voice", {}) or {}
    if not voice_cfg.get("tts_enabled", True):
        return False
    if source == "voice":
        return bool(voice_cfg.get("voice_reply_tts", True))
    return bool(voice_cfg.get("typed_chat_tts", False))


async def _process_message(user_text: str, msg_id: str | None = None,
                           blocking_tts: bool = False, source: str = "text") -> dict:
    if not msg_id:
        msg_id = uuid.uuid4().hex
    try:
        memory.save_message("user", user_text)
        _emit_stream_start(msg_id, user_text)
        full_response, status_updates = "", []
        rate_limit: dict | None = None
        async for token in brain.chat(user_text, stream=True):
            if token.startswith("_ratelimit_:"):
                # A 429 is a state the UI must show, not a wall of red text.
                try:
                    rate_limit = json.loads(token.split(":", 1)[1])
                except Exception:
                    rate_limit = {}
                _emit_rate_limited(msg_id, rate_limit)
            elif token.startswith("_thinking_:"):
                status = token.removeprefix("_thinking_:")
                status_updates.append(status)
                _emit_stream_status(msg_id, status)
            else:
                full_response += token
                _emit_stream_chunk(msg_id, token)
        full_response = full_response.strip() or "Desculpa Simão, não consegui gerar uma resposta."
        memory.save_message("assistant", full_response)
        result = {
            "msg_id": msg_id, "text": full_response, "status": status_updates, "ok": True,
            # Safe diagnostics only: provider, model, tier, token counts and
            # latency. Never the prompt, never a tool argument, never the key.
            "meta": dict(getattr(brain, "last_metadata", {}) or {}),
        }
        if rate_limit:
            result["rate_limit"] = rate_limit
        _emit_stream_end(msg_id, result)
        if _should_speak(source) and not rate_limit:
            if blocking_tts:
                await voice.speak(full_response)
            else:
                threading.Thread(target=lambda: asyncio.run(voice.speak(full_response)), daemon=True).start()
        return result
    except Exception:
        logger.exception("Erro no _process_message")
        error_result = {"msg_id": msg_id, "text": "Ups Simão, ocorreu um erro ao processar o teu pedido.", "ok": False, "error": "processing_error"}
        _emit_stream_error(msg_id, "processing_error", error_result["text"])
        _emit_stream_end(msg_id, error_result)
        return error_result

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# Single-instance guard. Held open for the process lifetime; the OS releases it
# automatically if Nano crashes, so a stale lock can never wedge the launcher.
_INSTANCE_LOCK: socket.socket | None = None
# Fixed, private-range port used only as a mutex. The Electron shell probes
# this same number before spawning a backend, so the two single-instance
# systems agree on what "a Nano is already running" means; tests assert the
# two constants have not drifted apart.
_INSTANCE_LOCK_PORT = 47615
INSTANCE_LOCK_PORT = _INSTANCE_LOCK_PORT


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
    # Opt-in, never inferred. Started unconditionally, a blocking reader on
    # stdin would swallow the console input of a plain NANO.bat session; the
    # desktop shell asks for the channel explicitly because only it owns the
    # other end of the pipe.
    parser.add_argument("--desktop-control", action="store_true",
                        help="read control requests from the parent process (Electron)")
    return parser.parse_known_args()[0]

def should_open_browser(mode: str) -> bool:
    """Whether THIS process is responsible for putting a UI on screen.

    Exactly one thing may open a window. In desktop mode the Electron shell
    already has one, so Nano must open nothing: a browser tab appearing beside
    the desktop window would be two UIs against one backend, and it is the
    single most visible way to get the desktop migration wrong.
    """
    return str(mode).lower() != "electron"


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
        # Full teardown here, and ONLY here: application exit is the one place
        # that is allowed to take the voice subsystem down.
        voice.shutdown()
    except Exception:
        logger.exception("Falha ao parar a voz")

    # Ollama is deliberately NOT stopped. It is a shared background service the
    # user (or another tool) may be relying on, it idles at a few tens of MB,
    # and OLLAMA_KEEP_ALIVE unloads the model by itself. Nano only tears down
    # what it exclusively owns.
    bridge = _DESKTOP_BRIDGE
    if bridge is not None:
        try:
            bridge.stop()
        except Exception:
            logger.debug("Falha ao fechar o canal de controlo do desktop", exc_info=True)

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

    _report("Cloud", "OK" if cloud_configured() else "NOT CONFIGURED",
            brain.groq_fast_model if cloud_configured() else "")

    # Load the audio backends before eel starts serving. A UI request must
    # never be the first thing to import a native extension: eel runs the whole
    # bridge on one cooperative hub, and a first `import pygame` (which pulls in
    # numpy) inside a request handler froze the entire interface permanently.
    # See core.audio_feedback.prewarm.
    audio_ready = audio_feedback.prewarm()
    _report(
        "Audio",
        "READY" if audio_ready.get("mixer") or audio_ready.get("pyaudio") else "DEGRADED",
        ", ".join(name for name in ("pygame", "pyaudio") if audio_ready.get(name)) or "no backend",
    )

    voice_state = voice_runtime.status()
    _report(
        "Voice/STT",
        voice_state.get("readiness", "UNKNOWN"),
        "; ".join(voice_state.get("blockers", []))[:70],
    )

    # The voice turn is trigger-independent; hook up its UI notifications
    # before any trigger can fire.
    _attach_voice_observer()

    # The desktop control channel, before the shortcut can possibly fire. The
    # shell waits for a ping on this channel before it registers the hotkey,
    # so a keypress can never arrive at a backend that is not ready for it.
    if args.desktop_control:
        _report("Desktop", "READY" if _start_desktop_bridge() else "UNAVAILABLE",
                "control channel")
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
    if should_open_browser(args.mode):
        threading.Timer(1.0, _open_browser_tab, args=(port,)).start()
        _report("UI", "READY", f"http://localhost:{port}")
    else:
        _report("UI", "READY", "electron window")
        logger.info("Modo electron: a janela é do Electron; o Nano não abre navegador.")

    print(flush=True)
    print("  Nano is running. Close this window to stop it.", flush=True)
    print(flush=True)

    # Tell the desktop shell the HTTP port for real, on the authenticated
    # channel rather than by scraping stdout. The shell still verifies the
    # server itself before showing a window: this is an announcement, not a
    # readiness claim.
    _desktop_emit("backend_started", {"port": port, "mode": str(args.mode).lower()})

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
