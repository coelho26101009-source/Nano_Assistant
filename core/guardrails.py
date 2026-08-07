"""HELIOS human-in-the-loop security policy."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from typing import Awaitable, Callable

logger = logging.getLogger("helios.guardrails")

SENSITIVE_TOOLS: dict[str, dict] = {
    "web_interact": {"check": lambda args: _is_form_submit(args), "message": "Vou submeter um formulário na web. Confirmas?"},
    "system_run_powershell": {"check": lambda args: _requires_powershell_confirmation(args), "message": "Este comando PowerShell pode alterar o sistema. Confirmas a execução?"},
    "system_delete_file": {"check": lambda _: True, "message": "Vou apagar um ficheiro permanentemente."},
    "system_kill_process": {"check": lambda _: True, "message": "Vou terminar um processo do sistema."},
    "system_registry_write": {"check": lambda _: True, "message": "Vou escrever no registo do Windows."},
    "system_format_drive": {"check": lambda _: True, "message": "⚠️ ATENÇÃO: Vou formatar uma drive!"},
}
ALWAYS_CONFIRM = {"system_delete_file", "system_format_drive", "system_registry_write", "system_kill_process"}
_SAFE_POWERSHELL = (
    r"^(get-date|whoami|hostname|ver|systeminfo|ipconfig(?:\s+/all)?|"
    r"get-process(?:\s+[^|;&]+)?|get-service(?:\s+[^|;&]+)?|"
    r"get-childitem(?:\s+[^|;&]+)?|dir(?:\s+[^|;&]+)?|get-location|pwd|"
    r"echo(?:\s+[^|;&]+)?)$"
)


def _is_form_submit(args: dict) -> bool:
    sel = str(args.get("selector") or "").lower()
    text = str(args.get("text") or "").lower()
    return any(d in sel or d in text for d in ("submit", "comprar", "pagar", "confirmar", "apagar", "eliminar", "deletar", "checkout"))


def _requires_powershell_confirmation(args: dict) -> bool:
    command = str(args.get("command") or "").strip().lower()
    if not command:
        return True
    if any(token in command for token in (";", "&&", "||", "|", ">", "<", "`", "$", "{", "}", "-encodedcommand", "iex", "invoke-expression")):
        return True
    return re.fullmatch(_SAFE_POWERSHELL, command, flags=re.IGNORECASE) is None


class GuardrailsEngine:
    def __init__(self):
        self._confirm_callback: Callable[[str, dict], Awaitable[bool]] | None = None
        self._pending: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Future[bool]]] = {}
        self._lock = threading.RLock()

    def set_confirm_callback(self, cb: Callable[[str, dict], Awaitable[bool]]) -> None:
        self._confirm_callback = cb

    def requires_confirmation(self, tool_name: str, args: dict) -> bool:
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
        message = rule.get("message", f"Vou executar '{tool_name}'. Confirmas?")
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
        try:
            import eel
            eel.on_confirm_request(request_id, message, meta)
        except Exception:
            with self._lock:
                self._pending.pop(request_id, None)
            logger.exception("Não foi possível abrir confirmação na UI")
            return False
        try:
            return bool(await asyncio.wait_for(future, timeout=60))
        except asyncio.TimeoutError:
            return False
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

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
