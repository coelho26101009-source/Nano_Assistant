"""Nano Assistant human-in-the-loop security policy."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Awaitable, Callable

from core import capabilities

logger = logging.getLogger("nano.guardrails")

SENSITIVE_TOOLS: dict[str, dict] = {
    "web_interact": {"check": lambda args: str(args.get("action", "")).lower() in {"click", "type"}, "message": "Vou interagir com uma página web e alterar o estado dela. Confirmas?"},
    # system_run_powershell was here, with a rule that waved read-only cmdlets
    # through unconfirmed and asked "Confirmas a execução?" for the rest. Both
    # halves were wrong once the tool was withdrawn: there is nothing to wave
    # through and nothing to confirm. Shell execution is now declared
    # unavailable in core/capabilities.py and refused before this layer runs.
    "system_files": {"check": lambda args: str(args.get("operation", "")).lower() not in {"read", "list"}, "message": "Esta operação vai alterar ficheiros no computador. Confirmas?"},
    "system_wifi": {"check": lambda args: str(args.get("action", "")).lower() in {"connect", "disconnect"}, "message": "Vou alterar a ligação Wi-Fi do computador. Confirmas?"},
    "system_bluetooth": {"check": lambda _args: True, "message": "Vou alterar o estado do Bluetooth do computador. Confirmas?"},
    "system_volume": {"check": lambda _args: True, "message": "Vou alterar o volume do computador. Confirmas?"},
    "system_brightness": {"check": lambda _args: True, "message": "Vou alterar o brilho do computador. Confirmas?"},
    "iot_command": {"check": lambda _args: True, "message": "Vou enviar uma ação para um dispositivo externo. Confirmas?"},
    "phone_notify": {"check": lambda _args: True, "message": "Vou enviar uma notificação para um serviço externo. Confirmas?"},
    "calendar_add_event": {"check": lambda _args: True, "message": "Vou criar um evento no teu calendário. Confirmas?"},
    "calendar_delete_event": {"check": lambda _args: True, "message": "Vou apagar um evento do teu calendário. Confirmas?"},
    "calendar_import_ics": {"check": lambda _args: True, "message": "Vou importar eventos para o teu calendário. Confirmas?"},
    # set_reminder used to be here. A reminder only shows a notification on
    # this PC at the time asked, and is cancelled in one click, so asking
    # "Confirmas?" for "lembra-me daqui a 10 minutos" was a click too many.
    # It still asks when the turn already read outside content (see
    # UNATTENDED_AFTER_TRUSTED_ONLY and Brain._run_tool), and its message
    # stays below for that case.
    "cancel_reminder": {"check": lambda _args: True, "message": "Vou cancelar um lembrete existente. Confirmas?"},
    # The plan of a multi-step task, shown once (plugins/task_plan.py). The
    # message is built from the plan itself.
    "task_plan": {"check": lambda _args: True, "message": None},
    "rag_index_pdf": {"check": lambda _args: True, "message": "Vou ler e indexar um documento local na memória do Nano Assistant. Confirmas?"},
}

#: Tools that run without a card only while the turn has read nothing from
#: outside (pages, screen, documents). Brain asks for them otherwise.
UNATTENDED_AFTER_TRUSTED_ONLY: dict[str, str] = {
    "set_reminder": "Vou criar um lembrete que ficará ativo em segundo plano. Confirmas?",
}

ALWAYS_CONFIRM = {
    "system_delete_file",
    "system_format_drive",
    "system_registry_write",
    "system_kill_process",
}

# _SAFE_POWERSHELL and _requires_powershell_confirmation were deleted with the
# rule above. Between them they encoded an allow-list of "harmless" cmdlets --
# Get-Process among them, the exact request that exposed this -- for a tool
# that no longer exists. Nothing in this module classifies a command line now.


class GuardrailsEngine:
    def __init__(self):
        self._confirm_callback: Callable[[str, dict], Awaitable[bool]] | None = None
        self._pending: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Future[bool]]] = {}
        self._lock = threading.RLock()
        # Other places an approval request is shown besides the PC window
        # (the paired phone, core.mobile_link). Each is called as
        # listener("request", request_id, message, meta) -> bool (shown?) and
        # listener("resolved", request_id, None, None) once it is answered or
        # times out, so the other screens can take their card down.
        self._listeners: list[Callable[[str, str, Any, Any], bool]] = []

    def add_confirm_listener(self, listener: Callable[[str, str, Any, Any], bool]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_confirm_listener(self, listener: Callable[[str, str, Any, Any], bool]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _tell_listeners(self, phase: str, request_id: str, message: Any = None, meta: Any = None) -> bool:
        shown = False
        for listener in list(self._listeners):
            try:
                shown = bool(listener(phase, request_id, message, meta)) or shown
            except Exception:
                logger.exception("Falha num ecrã de confirmação adicional")
        return shown

    def set_confirm_callback(self, cb: Callable[[str, dict], Awaitable[bool]]) -> None:
        self._confirm_callback = cb

    def requires_confirmation(self, tool_name: str, args: dict) -> bool:
        # An unavailable capability is never confirmable. Brain refuses these
        # before this layer is reached, so this is the second lock on the same
        # door: any future caller that consults guardrails first is told there
        # is nothing to ask about, rather than being handed a dialog whose Yes
        # cannot deliver anything.
        if capabilities.for_tool(tool_name) is not None:
            return False
        if tool_name in ALWAYS_CONFIRM:
            return True
        rule = SENSITIVE_TOOLS.get(tool_name)
        try:
            return bool(rule and rule.get("check") and rule["check"](args))
        except Exception:
            logger.exception("Falha a avaliar guardrail '%s' — a bloquear", tool_name)
            return True

    async def ask_confirmation(self, tool_name: str, args: dict) -> bool:
        rule = SENSITIVE_TOOLS.get(tool_name, {})
        message = rule.get("message") or UNATTENDED_AFTER_TRUSTED_ONLY.get(tool_name) or f"Vou executar '{tool_name}'. Confirmas?"
        if tool_name == "task_plan":
            from plugins import task_plan as task_plan_plugin  # noqa: PLC0415 - plugin, loaded lazily

            plan = task_plan_plugin.validate(args)
            if not plan["ok"]:
                return False
            message = task_plan_plugin.describe(plan)
        if self._confirm_callback is None:
            logger.error("Guardrail sem callback — operação bloqueada")
            return False
        try:
            return await asyncio.wait_for(self._confirm_callback(message, {"tool": tool_name, "args": args}), timeout=60)
        except asyncio.TimeoutError:
            logger.warning("Timeout no guardrail '%s'", tool_name)
            return False
        except Exception:
            logger.exception("Erro no guardrail '%s'", tool_name)
            return False

    async def request_from_ui(self, message: str, meta: dict) -> bool:
        """Create a one-shot confirmation request and wait for the UI."""
        import uuid
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        with self._lock:
            self._pending[request_id] = (loop, future)
        shown_on_pc = False
        try:
            import eel
            eel.on_confirm_request(request_id, message, meta)
            shown_on_pc = True
        except Exception:
            logger.exception("Não foi possível abrir confirmação na UI")
        shown_elsewhere = self._tell_listeners("request", request_id, message, meta)
        if not (shown_on_pc or shown_elsewhere):
            with self._lock:
                self._pending.pop(request_id, None)
            return False
        try:
            return bool(await asyncio.wait_for(future, timeout=60))
        except asyncio.TimeoutError:
            return False
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
            self._tell_listeners("resolved", request_id)

    def resolve_confirmation(self, request_id: str, confirmed: bool) -> bool:
        with self._lock:
            pending = self._pending.get(request_id)
        if not pending:
            return False
        loop, future = pending
        if future.done():
            return False
        loop.call_soon_threadsafe(future.set_result, bool(confirmed))
        return True
