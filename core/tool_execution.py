"""Central tool registry and execution authority for Nano agents.

Every tool the model can call is registered here, and every execution — from the
chat loop or from the background worker — goes through the same path:

    capability resolution -> argument validation -> scope classification
    -> PolicyEngine -> PermissionManager -> execution -> verification -> audit

Plugin handlers are never invoked directly. ``core.plugin_loader`` refuses to run
a handler unless the caller presents this executor as its execution authority,
so bypassing the pipeline fails closed rather than silently succeeding.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from core import capabilities, plugin_loader
from core.browser_agent import validate_public_http_url
from core.execution_scope import (
    PathValidationError,
    ResolvedTarget,
    Scope,
    resolve_target,
    workspace_root,
)
from core.permission_manager import PermissionManager
from core.policy_engine import RiskLevel
from core.trust import TrustLevel, classify_external, is_untrusted_capability


class ToolExecutionError(RuntimeError):
    """Raised when a tool cannot be executed safely or fails validation."""


class RetryPolicy(str):
    SAFE_TO_RETRY = "SAFE_TO_RETRY"
    CONDITIONALLY_RETRYABLE = "CONDITIONALLY_RETRYABLE"
    NOT_SAFE_TO_RETRY = "NOT_SAFE_TO_RETRY"


# Argument keys that always carry a filesystem target and must be resolved and
# classified centrally before the handler ever sees them.
_PATH_ARGUMENT_KEYS = ("path", "destination", "dest", "src", "source", "cwd", "target_path")
# Argument keys that always carry a network target.
_URL_ARGUMENT_KEYS = ("url", "webhook_url", "endpoint")

# Test runners the project may execute. Anything else is refused: the model has
# no legitimate reason to choose the command line for a test run.
_ALLOWED_TEST_RUNNERS: dict[str, list[str]] = {
    "pytest": ["-m", "pytest", "-q"],
    "unittest": ["-m", "unittest", "discover", "-q"],
}

# Synchronous tool handlers run here rather than on asyncio's shared default
# executor, for two reasons.
#
# 1. Isolation. A tool that times out leaves its worker running -- Python cannot
#    interrupt a thread -- and that orphan occupies a slot until the handler
#    returns. On the shared default executor those orphans would also delay
#    every other asyncio.to_thread caller in the process, and would block
#    interpreter shutdown while the loop waits to join them.
# 2. A stated ceiling. Tool concurrency is now an explicit number instead of
#    asyncio's implicit min(32, cpu_count + 4).
#
# Threads are created on demand and the pool is never resized, so an idle Nano
# holds no tool threads at all.
_TOOL_THREADS = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="nano-tool"
)


#: A permission target is shown to a human and written to the audit log, so it
#: is bounded like every other crossing value.
MAX_TARGET_CHARS = 300


def _digest(text: str) -> str:
    """A short, stable fingerprint of content that must never be logged itself.

    Typed text and clipboard writes bind their grant to WHAT is being written,
    without the audit log or the permission target ever holding the content.
    """
    import hashlib

    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:8]


def _window_target(args: dict) -> str:
    window_id = args.get("window_id")
    if window_id not in (None, ""):
        return f"window:{window_id}"
    query = str(args.get("query") or "").strip()
    return f"window:{query}" if query else "window:*"


def _pc_control_target(tool_name: str, args: dict) -> str | None:
    """A stable, human-readable target string for one PC-control call.

    THIS IS WHAT MAKES A GRANT MEAN SOMETHING. PermissionManager keys a grant on
    (capability, target); without a target every PC grant would key on "*", and
    one ALLOW_ONCE for "close the Calculator" would authorise closing Discord.

    Two rules the table below follows:

    * A call with more than one target names ALL of them. `pc_file_move` binds
      to source AND destination, so approving "move A into B" cannot be reused
      to move A into C -- a different destination is a different grant.
    * Content is never a target. Typed text and clipboard writes bind to a
      DIGEST of the content, which is precise enough to distinguish two calls
      and carries nothing that should not be in a log.

    Returns None for non-PC tools, and for the PC tools whose real target is
    already an argument the permission layer inspects (`path`, `url`) -- so
    those keep binding to the fully-resolved value the executor produced.
    """
    if not str(tool_name).startswith("pc_"):
        return None

    def clamp(value: str) -> str:
        text = str(value)
        return text if len(text) <= MAX_TARGET_CHARS else text[:MAX_TARGET_CHARS - 1] + "…"

    # ---------------------------------------------------------------- apps
    if tool_name in {"pc_app_launch", "pc_app_switch"}:
        value = str(args.get("app_id") or args.get("name") or "").strip()
        return clamp(f"app:{value}" if value else "app:*")
    if tool_name == "pc_app_list_running":
        return "apps:running"

    # -------------------------------------------------------------- windows
    if tool_name in {"pc_window_batch_state", "pc_window_batch_close"}:
        app = str(args.get("app") or "").strip()
        state = str(args.get("state") or "").strip()
        suffix = f":{state}" if state else ""
        return clamp(f"windows:{app or '*'}{suffix}")
    if tool_name == "pc_window_list":
        return "windows:all"
    if tool_name.startswith("pc_window_"):
        return clamp(_window_target(args))

    # ------------------------------------------------------- volume / media
    if tool_name.startswith("pc_volume_"):
        return f"volume:{tool_name.rsplit('_', 1)[-1]}"
    if tool_name == "pc_media_control":
        return clamp(f"media:{str(args.get('action') or '*').strip()}")

    # -------------------------------------------------------------- display
    if tool_name == "pc_display_info":
        return "display:all"
    if tool_name.startswith("pc_display_"):
        monitor = args.get("monitor")
        return f"display:{monitor if monitor not in (None, '') else 'default'}"

    # ------------------------------------------------------------ clipboard
    if tool_name == "pc_clipboard_write":
        return f"clipboard:write:#{_digest(args.get('text') or '')}"
    if tool_name.startswith("pc_clipboard_"):
        return f"clipboard:{tool_name.rsplit('_', 1)[-1]}"

    # ---------------------------------------------------------------- input
    if tool_name == "pc_input_type_text":
        return clamp(f"input:type:{_window_target(args)}:#{_digest(args.get('text') or '')}")
    if tool_name == "pc_input_press_key":
        key = str(args.get("key") or "*").strip().lower()
        return clamp(f"input:key:{key}:{_window_target(args)}")
    if tool_name == "pc_input_hotkey":
        hotkey = str(args.get("hotkey") or "*").strip().lower()
        aimed = (_window_target(args) if args.get("window_id") or args.get("query")
                 else "desktop")
        return clamp(f"input:hotkey:{hotkey}:{aimed}")
    if tool_name == "pc_pointer_scroll":
        return clamp(f"pointer:scroll:{_window_target(args)}")

    # ---------------------------------------------------------------- files
    if tool_name in {"pc_folder_open", "pc_file_open"}:
        # A real `path` is already preferred by _resolve_target. A known-folder
        # NAME arrives as `folder` (see the handler for why) and still has to
        # bind the grant to something specific.
        if not str(args.get("path") or "").strip():
            folder = str(args.get("folder") or "").strip()
            return clamp(f"folder:{folder}") if folder else None
        return None
    if tool_name in {"pc_folder_create", "pc_file_create_text"}:
        parent = str(args.get("folder") or args.get("path") or "").strip()
        name = str(args.get("name") or "").strip()
        return clamp(f"create:{parent or '?'}/{name or '*'}")
    if tool_name in {"pc_file_copy", "pc_file_move"}:
        return clamp(f"file:{str(args.get('source') or '*')} -> "
                     f"{str(args.get('destination') or '*')}")
    if tool_name == "pc_file_rename":
        return clamp(f"file:{str(args.get('source') or '*')} -> "
                     f"{str(args.get('new_name') or '*')}")
    if tool_name in {"pc_file_recycle", "pc_folder_recycle"}:
        return clamp(f"recycle:{str(args.get('path') or '*')}")
    if tool_name in {"pc_app_search", "pc_file_search"}:
        query = str(args.get("query") or "").strip()
        return clamp(f"query:{query}") if query else None

    # ----------------------------------------------------- web and settings
    if tool_name == "pc_web_open_url":
        # `url` is already a target key the permission layer inspects, and it
        # holds the value the central URL validation approved.
        return None
    if tool_name == "pc_web_search":
        engine = str(args.get("engine") or "default").strip()
        return clamp(f"search:{engine}:{str(args.get('query') or '*').strip()}")
    if tool_name == "pc_settings_open":
        return clamp(f"settings:{str(args.get('section') or '*').strip()}")

    # --------------------------------------------------- system and session
    if tool_name == "pc_system_info":
        return "system:info"
    if tool_name == "pc_network_status":
        return "system:network"
    if tool_name == "pc_storage_info":
        return "system:storage"
    if tool_name in {"pc_session_lock", "pc_session_logoff"}:
        return f"session:{tool_name.rsplit('_', 1)[-1]}"
    if tool_name.startswith("pc_power_"):
        return f"power:{tool_name.rsplit('_', 1)[-1]}"

    # --------------------------------------------------------------- screen
    if tool_name == "pc_screenshot_capture":
        mode = str(args.get("mode") or "desktop").strip().lower()
        if mode == "window":
            return clamp(f"screen:{_window_target(args)}")
        return f"screen:{mode}"

    return tool_name


class ToolExecutor:
    """Registry and runner for real Nano tools with permission enforcement."""

    def __init__(self, permission_manager: PermissionManager | None = None, event_bus: Any | None = None):
        self.permission_manager = permission_manager or PermissionManager()
        self.event_bus = event_bus
        self.registry: dict[str, dict] = {}
        self._register_default_tools()
        # Claim the right to run plugin handlers. plugin_loader.execute_tool
        # rejects any caller that is not a bound authority.
        plugin_loader.bind_execution_authority(self)

    # ------------------------------------------------------------------ utils

    def _publish(self, event_name: str, payload: dict | None = None) -> None:
        if self.event_bus is not None:
            try:
                self.event_bus.publish(event_name, payload or {})
            except Exception:
                pass

    def get_retry_policy(self, name: str) -> str:
        tool = self.registry.get(name)
        if not tool:
            return RetryPolicy.NOT_SAFE_TO_RETRY
        return str(tool.get("retry_policy", RetryPolicy.SAFE_TO_RETRY)).upper()

    def _validate_path(
        self,
        value: str | None,
        *,
        allow_absolute: bool = False,
        must_exist: bool = False,
        workspace_root: str | Path | None = None,
    ) -> Path:
        """Backwards-compatible path validation returning a resolved Path.

        Kept for existing callers; new code should use ``resolve_tool_target``
        which also reports the scope the path landed in.
        """
        target = self.resolve_tool_target(value, base=workspace_root, must_exist=must_exist)
        if not allow_absolute and target.scope == Scope.SYSTEM:
            raise ToolExecutionError("absolute_path_blocked")
        return target.path

    def resolve_tool_target(
        self,
        value: str | None,
        *,
        base: str | Path | None = None,
        must_exist: bool = False,
    ) -> ResolvedTarget:
        try:
            return resolve_target(value, base=base, must_exist=must_exist)
        except PathValidationError as exc:
            raise ToolExecutionError(exc.code if not exc.detail else f"{exc.code}") from exc

    # -------------------------------------------------------------- registry

    def _register_default_tools(self) -> None:
        self.register_tool(
            "filesystem.create_directory",
            "Criar diretório no sistema local.",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=self._create_directory,
            risk="medium",
            timeout=15,
            retry_policy=RetryPolicy.CONDITIONALLY_RETRYABLE,
            capabilities=["filesystem.write"],
        )
        self.register_tool(
            "filesystem.write_file",
            "Escrever conteúdo num ficheiro local.",
            {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
            handler=self._write_file,
            risk="medium",
            timeout=20,
            retry_policy=RetryPolicy.CONDITIONALLY_RETRYABLE,
            capabilities=["filesystem.write"],
        )
        self.register_tool(
            "filesystem.read_file",
            "Ler o conteúdo de um ficheiro local.",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=self._read_file,
            risk="low",
            timeout=15,
            retry_policy=RetryPolicy.SAFE_TO_RETRY,
            capabilities=["filesystem.read"],
        )
        self.register_tool(
            "filesystem.list_directory",
            "Listar o conteúdo de um diretório local.",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=self._list_directory,
            risk="low",
            timeout=15,
            retry_policy=RetryPolicy.SAFE_TO_RETRY,
            capabilities=["filesystem.read"],
        )
        self.register_tool(
            "filesystem.delete_path",
            "Apagar um ficheiro ou diretório local.",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            handler=self._delete_path,
            risk="critical",
            timeout=15,
            retry_policy=RetryPolicy.NOT_SAFE_TO_RETRY,
            capabilities=["filesystem.delete"],
            requires_confirmation=True,
        )
        # THERE IS NO shell.execute TOOL, AND THERE MUST NOT BE ONE.
        #
        # Until the V2 checkpoint audit there was: it ran
        # `subprocess.run(["cmd", "/c", command])` on a model-supplied string,
        # gated by nothing but an approval dialog. It was never ADVERTISED to
        # the model -- but Brain._run_tool dispatches whatever name the model
        # emits, so invisibility was not de-authorisation, and a single
        # confirmed call would have run arbitrary PowerShell. That is exactly
        # the primitive plugins/god_mode.py was emptied out to remove, and its
        # docstring's claim that no PowerShell call site remained in the repo
        # was false while this registration stood.
        #
        # The capability is now declared unavailable in core/capabilities.py,
        # blocked in PolicyEngine, and refused in _authorize below.
        self.register_tool(
            "project.run_tests",
            "Executa a suíte de testes do projeto atual com um runner suportado.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "runner": {"type": "string", "enum": sorted(_ALLOWED_TEST_RUNNERS)},
                    "test_path": {"type": "string"},
                },
                "required": ["path"],
            },
            handler=self._run_project_tests,
            risk="medium",
            timeout=120,
            retry_policy=RetryPolicy.SAFE_TO_RETRY,
            capabilities=["project.test"],
        )
        self.register_tool(
            "browser.search_web",
            "Pesquisa pública na web e retorna resultados de busca.",
            {"type": "object", "properties": {"query": {"type": "string"}, "engine": {"type": "string"}}, "required": ["query"]},
            handler=self._search_web,
            risk="low",
            timeout=25,
            retry_policy=RetryPolicy.SAFE_TO_RETRY,
            capabilities=["browser.read"],
        )
        self.register_tool(
            "browser.fetch_url",
            "Carrega um URL pública e extrai o texto principal.",
            {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            handler=self._fetch_url,
            risk="low",
            timeout=25,
            retry_policy=RetryPolicy.SAFE_TO_RETRY,
            capabilities=["browser.read"],
        )

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict,
        *,
        handler: Callable[[dict], Any],
        risk: str = "low",
        timeout: int = 30,
        requires_confirmation: bool = False,
        provider: str = "core",
        capabilities: list[str] | None = None,
        retry_policy: str = RetryPolicy.SAFE_TO_RETRY,
    ) -> None:
        self.registry[name] = {
            "name": name,
            "description": description,
            "input_schema": input_schema,
            "handler": handler,
            "risk": risk,
            "timeout": timeout,
            "requires_confirmation": requires_confirmation,
            "provider": provider,
            "capabilities": capabilities or [],
            "retry_policy": retry_policy,
        }

    def register_plugin_tools(self, tools: list[dict] | None = None) -> int:
        """Absorb every loaded plugin tool into this registry.

        Plugin tools become ordinary registry entries whose handler is dispatched
        through ``plugin_loader`` with this executor as the execution authority.
        After this call there is exactly one execution path for every tool the
        model can see.
        """
        registered = 0
        for tool in tools if tools is not None else plugin_loader.get_all_tools():
            function = (tool or {}).get("function") or {}
            name = function.get("name")
            if not name or name in self.registry:
                continue
            capability = self.permission_manager.resolve_tool_capability(name, {})
            risk = self.permission_manager.classify_action(capability, {})
            self.register_tool(
                name,
                str(function.get("description") or name),
                function.get("parameters") or {"type": "object"},
                handler=self._make_plugin_handler(name),
                risk=risk.value,
                timeout=60,
                requires_confirmation=self.permission_manager.is_approval_gated(capability),
                provider="plugin",
                capabilities=[capability],
                retry_policy=RetryPolicy.CONDITIONALLY_RETRYABLE,
            )
            registered += 1
        return registered

    def _make_plugin_handler(self, tool_name: str) -> Callable[[dict], Any]:
        def _handler(args: dict) -> Any:
            return plugin_loader.execute_tool(tool_name, args, authority=self)

        _handler.__name__ = f"plugin::{tool_name}"
        return _handler

    # -------------------------------------------------- argument validation

    def _validate_arguments(self, name: str, tool: dict, args: dict) -> tuple[dict, dict]:
        """Validate and rewrite tool arguments centrally.

        Path arguments are resolved to absolute, symlink-resolved paths and
        written back into the argument dict, so handlers — including plugin
        handlers that were never written defensively — only ever receive a path
        that this authority already approved. Returns the prepared arguments and
        the execution context handed to the policy engine.
        """
        prepared = dict(args)
        # `_pc_target` is the authoritative permission target, written below and
        # read first by PermissionManager._resolve_target. Any value the MODEL
        # supplied under that name is discarded here, before anything reads it,
        # so a crafted argument cannot rebind a grant to a harmless-looking
        # string while the call does something else.
        prepared.pop("_pc_target", None)
        context: dict[str, Any] = {"tool": name}
        scopes: list[Scope] = []
        protected = False

        for key in _PATH_ARGUMENT_KEYS:
            value = prepared.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            target = self.resolve_tool_target(value)
            prepared[key] = str(target.path)
            scopes.append(target.scope)
            protected = protected or target.protected
            context.setdefault("targets", []).append(target.as_dict())

        for key in _URL_ARGUMENT_KEYS:
            value = prepared.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            ok, error = validate_public_http_url(value)
            if not ok:
                raise ToolExecutionError(error or "invalid_url")
            context.setdefault("urls", []).append(value)

        # TARGET BINDING FOR PC CONTROL.
        #
        # PermissionManager._resolve_target inspects a fixed list of argument
        # names. A PC tool's real target lives in `name`, `app_id`, `window_id`,
        # `query`, `source`+`destination` or a section enum, so without this the
        # grant key would be (capability, "*") -- and an ALLOW_ONCE for "close
        # the Calculator window" would silently authorise closing Discord.
        #
        # Computed AFTER path resolution above, so a file target binds to the
        # fully-resolved absolute path rather than to whatever the model typed.
        # Written to `_pc_target`, which _resolve_target reads FIRST: a
        # two-target operation like file.move has to out-rank the single `path`
        # key, or approving "move A into B" would also authorise moving A into
        # C. `target` is set alongside it for the confirmation UI and for
        # callers that predate the underscore key.
        pc_target = _pc_control_target(name, prepared)
        if pc_target is not None:
            prepared["_pc_target"] = pc_target
            prepared["target"] = pc_target
            context["pc_target"] = pc_target

        if scopes:
            # The least trusted scope touched by the call decides the scope of
            # the whole call. Writing inside the workspace but reading from the
            # system is a system-scoped operation.
            order = [Scope.CURRENT_WORKSPACE, Scope.CURRENT_PROJECT, Scope.EXPLICIT_TARGET, Scope.SYSTEM]
            context["scope"] = max(scopes, key=order.index).value
        context["protected_target"] = protected
        return prepared, context

    # --------------------------------------------------------- authorization

    def _authorize(self, name: str, args: dict, task_id: str | None) -> dict:
        """Run the full policy pipeline for one call.

        Returns a dict with ``ok`` plus, when refused, a ready-made tool result.
        When it returns ``ok=True`` and ``needs_confirmation=True`` the caller
        must obtain confirmation through the sync or async path before running.
        """
        # A capability Nano does not have is refused before anything else --
        # before the registry, the policy engine and every confirmation path.
        # "unknown_tool" would be true but useless here: it reads as "you named
        # it wrong", when the honest answer is that no such capability exists
        # and no approval can produce one. Whoever called gets a sentence that
        # says so, and never a dialog.
        unsupported = capabilities.for_tool(name)
        if unsupported is not None:
            self.permission_manager.log_decision(
                unsupported.id, "deny", risk=RiskLevel.CRITICAL, task_id=task_id,
                reason=f"Capability is not implemented and never will be: {unsupported.id}.",
                event_name="PermissionDenied",
            )
            return {"ok": False, "result": self._tool_result(
                False, "unsupported_capability",
                error=capabilities.describe(unsupported),
                metadata={"tool": name, "task_id": task_id,
                          "capability": unsupported.id, "unsupported": True},
            )}

        tool = self.registry.get(name)
        if not tool:
            return {"ok": False, "result": self._tool_result(False, "unknown_tool", error=f"Tool desconhecida: {name}", metadata={"tool": name})}

        capability = (tool.get("capabilities") or [name])[0]

        if self.permission_manager.is_emergency_stopped():
            self.permission_manager.log_decision(capability, "deny", risk=tool.get("risk"), task_id=task_id, reason="Emergency stop engaged; execution blocked.", event_name="PermissionDenied")
            return {"ok": False, "result": self._tool_result(False, "permission_denied", error="Emergency stop active: execution blocked by the Nano Policy Engine.", metadata={"tool": name, "task_id": task_id})}

        try:
            prepared, context = self._validate_arguments(name, tool, args)
        except ToolExecutionError as exc:
            self.permission_manager.log_decision(capability, "deny", risk=tool.get("risk"), task_id=task_id, reason=f"Argument validation failed: {exc}", event_name="PermissionDenied")
            return {"ok": False, "result": self._tool_result(False, "invalid_input", error=str(exc), metadata={"tool": name, "task_id": task_id})}

        # Re-resolve the capability now that arguments are normalised, so that
        # system_files(operation="delete") is gated as a delete, not a write.
        capability = self.permission_manager.resolve_tool_capability(name, prepared)

        evaluation = self.permission_manager.policy_engine.evaluate(
            capability,
            target=self.permission_manager._resolve_target(prepared),
            scope=context.get("scope"),
            arguments=prepared,
            context=context,
            task_id=task_id,
        )
        if evaluation.decision.value == "BLOCKED":
            self.permission_manager.log_decision(capability, "deny", risk=evaluation.risk, target=evaluation.target, task_id=task_id, reason=evaluation.reason, event_name="PermissionDenied")
            return {"ok": False, "result": self._tool_result(False, "permission_denied", error=f"Bloqueado pela política de segurança: {evaluation.reason}", metadata={"tool": name, "task_id": task_id, "capability": capability, "scope": evaluation.scope})}

        stored = self.permission_manager.get_policy(capability) or {}
        if str(stored.get("decision", "")).lower() in {"deny", "blocked"}:
            self.permission_manager.log_decision(capability, "deny", risk=evaluation.risk, target=evaluation.target, task_id=task_id, reason="Policy denies this capability.", event_name="PermissionDenied")
            return {"ok": False, "result": self._tool_result(False, "permission_denied", error="Permissão negada pela política do sistema.", metadata={"tool": name, "capability": capability})}

        decision = self.permission_manager.evaluate(capability, prepared, task_id=task_id, scope=context.get("scope"), context=context)
        stored_decision = self.permission_manager.get_decision_for_action(
            capability,
            {**prepared, "_task_id": task_id} if task_id else prepared,
            scope=context.get("scope"),
            context=context,
        )
        # An "ask" decision is honoured here. Both executors used to compute it
        # and then fall through, which is what let project.run_tests reach a
        # shell with no prompt.
        needs_confirmation = bool(
            decision.requires_confirmation
            or evaluation.requires_confirmation
            or tool.get("requires_confirmation")
            or stored_decision == "ask"
        )
        return {
            "ok": True,
            "tool": tool,
            "capability": capability,
            "args": prepared,
            "context": context,
            "evaluation": evaluation,
            "decision": decision,
            "needs_confirmation": needs_confirmation,
        }

    def _denied_result(self, name: str, capability: str, risk: Any, task_id: str | None) -> dict:
        self.permission_manager.log_decision(capability, "deny", risk=risk, task_id=task_id, reason="User declined risk confirmation", event_name="PermissionDenied")
        return self._tool_result(False, "permission_denied", error="Autorização recusada pelo utilizador.", metadata={"tool": name, "capability": capability, "task_id": task_id})

    # -------------------------------------------------------------- execution

    def execute_tool(self, name: str, args: dict | None = None, *, task_id: str | None = None) -> dict:
        """Synchronous execution. Must not be called from the shared event loop."""
        args = dict(args or {})
        auth = self._authorize(name, args, task_id)
        if not auth["ok"]:
            return auth["result"]

        if auth["needs_confirmation"] and not self.permission_manager.ask_for_confirmation(auth["capability"], auth["args"], task_id=task_id, context=auth["context"]):
            return self._denied_result(name, auth["capability"], auth["decision"].risk, task_id)

        return self._run_and_verify(name, auth, task_id, self._run_handler_sync)

    async def execute_tool_async(self, name: str, args: dict | None = None, *, task_id: str | None = None) -> dict:
        """Async execution used by the chat loop. Never blocks the event loop.

        Authorization (_authorize) and the handler itself both run off-thread,
        so the loop stays responsive for the whole call and the per-tool timeout
        below is genuinely enforceable.

        Cancellation note: a timeout cancels the *await*, not the worker thread
        -- Python cannot interrupt a running thread. The call returns
        tool_timeout immediately and the orphaned worker finishes into a result
        nobody reads. That is the correct trade for a desktop assistant: the
        alternative is the loop hanging until the tool decides to return.
        Handlers keep their own internal timeouts (subprocess.run(timeout=...),
        httpx timeouts) so the thread is bounded in practice too.
        """
        args = dict(args or {})
        auth = await asyncio.to_thread(self._authorize, name, args, task_id)
        if not auth["ok"]:
            return auth["result"]

        if auth["needs_confirmation"]:
            approved = await self.permission_manager.ask_for_confirmation_async(auth["capability"], auth["args"], task_id=task_id, context=auth["context"])
            if not approved:
                return self._denied_result(name, auth["capability"], auth["decision"].risk, task_id)

        tool = auth["tool"]
        start = time.monotonic()
        try:
            output = await asyncio.wait_for(
                self._run_handler_async(tool["handler"], auth["args"]),
                timeout=float(tool.get("timeout") or 30),
            )
        except asyncio.TimeoutError:
            return self._failure(name, auth, task_id, start, "tool_timeout")
        except Exception as exc:
            return self._failure(name, auth, task_id, start, str(exc))
        return self._complete(name, auth, task_id, start, output)

    def _run_handler_sync(self, handler: Callable[[dict], Any], args: dict, timeout: float) -> Any:
        result = handler(args)
        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(result)
            raise ToolExecutionError("async_handler_requires_execute_tool_async")
        return result

    async def _run_handler_async(self, handler: Callable[[dict], Any], args: dict) -> Any:
        """Run one handler without ever occupying the calling event loop.

        Almost every handler here is SYNCHRONOUS -- the four built-ins that use
        subprocess or httpx, and all 36 plugin handlers. Calling one directly
        from this coroutine ran its whole body before the coroutine ever
        yielded, so the loop was blocked for the full duration of the tool: up
        to the 180 s ceiling of shell.execute. Two things followed from that.
        Streamed chunks, eel callbacks and confirmation dialogs all stalled;
        and the asyncio.wait_for() wrapped around this call could never fire,
        because a timeout callback cannot be scheduled on a loop that is not
        running. The declared per-tool timeout was decorative.

        Off-loading to a worker thread fixes both at once: the loop keeps
        turning, so the UI stays live and the timeout is real.
        """
        if inspect.iscoroutinefunction(handler):
            return await handler(args)

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(_TOOL_THREADS, handler, args)
        if inspect.isawaitable(result):
            # A sync function that returns a coroutine (the plugin dispatch
            # wrapper does this for async plugin handlers). Await it here, on
            # the loop, where it belongs.
            return await result
        return result

    def _run_and_verify(self, name: str, auth: dict, task_id: str | None, runner) -> dict:
        """Run a handler off-thread with a real timeout.

        Submits to the shared ``_TOOL_THREADS`` pool rather than a per-call
        ``ThreadPoolExecutor`` used as a context manager: that pool's
        ``__exit__`` calls ``shutdown(wait=True)``, which blocks the caller --
        here, the background worker's own thread -- until the timed-out
        handler actually returns, defeating the timeout exactly the way the
        async path's docstring describes and fixes. A handler with no internal
        bound (a hung network call, a deadlock) would otherwise wedge the
        worker forever instead of failing at ``timeout`` as declared.
        """
        tool = auth["tool"]
        start = time.monotonic()
        timeout = float(tool.get("timeout") or 30)
        future = _TOOL_THREADS.submit(runner, tool["handler"], auth["args"], timeout)
        try:
            output = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return self._failure(name, auth, task_id, start, "tool_timeout")
        except Exception as exc:
            return self._failure(name, auth, task_id, start, str(exc))
        return self._complete(name, auth, task_id, start, output)

    def _trust_for(self, capability: str, context: dict) -> str:
        """Classify where a tool's output came from.

        Anything fetched from the network, or read from outside the workspace,
        is external content: data the model may read but never obey.
        """
        if is_untrusted_capability(capability) or context.get("urls"):
            return TrustLevel.UNTRUSTED_EXTERNAL.value
        if capability.startswith("filesystem") and context.get("scope") not in {None, "current_workspace"}:
            return TrustLevel.UNTRUSTED_EXTERNAL.value
        return TrustLevel.USER.value

    @staticmethod
    def _output_text(output) -> str:
        if isinstance(output, dict):
            parts = [str(output.get(key) or "") for key in ("content", "text", "stdout", "snippet", "results")]
            return "\n".join(part for part in parts if part)
        return str(output or "")

    def _complete(self, name: str, auth: dict, task_id: str | None, start: float, output: Any) -> dict:
        duration_ms = int((time.monotonic() - start) * 1000)
        verified, verification = self._verify_execution(name, auth["args"], output)
        metadata = {
            "tool": name,
            "task_id": task_id,
            "capability": auth["capability"],
            "scope": auth["context"].get("scope"),
            "duration_ms": duration_ms,
            "retry_policy": self.get_retry_policy(name),
            "permission_checked": True,
            "verified": verified,
            "verification": verification,
        }
        if not verified:
            # A handler that reports success but leaves no verifiable effect is
            # reported as a failure. Failure never becomes success.
            payload = self._tool_result(False, "verification_failed", output=output, error=f"Verificação falhou: {verification}", metadata=metadata)
            self.permission_manager.log_decision(auth["capability"], "verification_failed", risk=auth["decision"].risk, target=str(auth["args"].get("path") or ""), task_id=task_id, reason=verification, event_name="ToolVerificationFailed")
            self._publish("tool.failed", {"tool": name, "task_id": task_id, "error": "verification_failed", "duration_ms": duration_ms})
            return payload

        metadata["trust"] = self._trust_for(auth["capability"], auth["context"])
        if metadata["trust"] == TrustLevel.UNTRUSTED_EXTERNAL.value:
            inspection = classify_external(self._output_text(output), source=name)
            metadata["untrusted_source"] = name
            metadata["injection_findings"] = [f.as_dict() for f in inspection.findings]
            if inspection.suspicious:
                self.permission_manager.log_decision(
                    auth["capability"], "untrusted_content_flagged",
                    risk=auth["decision"].risk, target=str(auth["args"].get("url") or ""), task_id=task_id,
                    reason=f"External content attempted to claim authority: {sorted({f.category for f in inspection.findings})}",
                    event_name="UntrustedContentFlagged",
                )
                self._publish("security.untrusted_content", {
                    "tool": name, "task_id": task_id,
                    "categories": sorted({f.category for f in inspection.findings}),
                })

        wrapped = self._tool_result(True, "completed", output=output, metadata=metadata)
        self.permission_manager.log_decision(auth["capability"], "executed", risk=auth["decision"].risk, target=str(auth["args"].get("path") or auth["args"].get("url") or ""), task_id=task_id, reason="Tool executed and verified.", event_name="ToolExecuted")
        self._publish("tool.executed", {"tool": name, "task_id": task_id, "ok": True, "duration_ms": duration_ms, "retry_policy": self.get_retry_policy(name)})
        return wrapped

    def _failure(self, name: str, auth: dict, task_id: str | None, start: float, error: str) -> dict:
        duration_ms = int((time.monotonic() - start) * 1000)
        payload = self._tool_result(False, "failed", error=error, metadata={
            "tool": name,
            "task_id": task_id,
            "capability": auth["capability"],
            "duration_ms": duration_ms,
            "retry_policy": self.get_retry_policy(name),
            "permission_checked": True,
            "verified": False,
        })
        self.permission_manager.log_decision(auth["capability"], "failed", risk=auth["decision"].risk, task_id=task_id, reason=error, event_name="ToolFailed")
        self._publish("tool.failed", {"tool": name, "task_id": task_id, "error": error, "duration_ms": duration_ms, "retry_policy": self.get_retry_policy(name)})
        return payload

    def _verify_execution(self, name: str, args: dict, output: Any) -> tuple[bool, str]:
        """Post-execution verification against observable state, not self-report."""
        path = args.get("path")
        if name in {"filesystem.write_file", "filesystem.create_directory"} and isinstance(path, str):
            return (Path(path).exists(), "path_exists" if Path(path).exists() else "path_missing_after_write")
        if name == "filesystem.delete_path" and isinstance(path, str):
            gone = not Path(path).exists()
            return (gone, "path_removed" if gone else "path_still_present_after_delete")
        if isinstance(output, dict):
            if output.get("error"):
                return (False, str(output.get("error"))[:200])
            if output.get("success") is False:
                return (False, "handler_reported_failure")
            # Plugin handlers report with `ok`, not `success`. Without this a
            # handler returning {"ok": False, "status": "not_found"} and no
            # "error" key was wrapped as success:true -- so "não encontrei o
            # Spotify" would reach the model looking like a completed action.
            if output.get("ok") is False:
                return (False, str(output.get("status") or "handler_reported_failure")[:200])
        return (True, "no_verification_required")

    def _tool_result(self, success: bool, status: str, *, output: Any = None, error: str | None = None, metadata: dict | None = None) -> dict:
        result = {
            "success": bool(success),
            "status": status,
            "output": output,
            "error": error,
            "metadata": metadata or {},
            "duration_ms": metadata.get("duration_ms") if isinstance(metadata, dict) else None,
        }
        if output is None and status == "completed":
            result["output"] = {"ok": True}
        return result

    # ---------------------------------------------------------- core handlers

    def _create_directory(self, args: dict) -> dict:
        p = self.resolve_tool_target(args.get("path")).path
        p.mkdir(parents=True, exist_ok=True)
        return {"path": str(p), "created": True}

    def _write_file(self, args: dict) -> dict:
        content = args.get("content", "")
        p = self.resolve_tool_target(args.get("path")).path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content), encoding="utf-8")
        return {"path": str(p), "written": True, "bytes": len(str(content).encode("utf-8"))}

    def _read_file(self, args: dict) -> dict:
        p = self.resolve_tool_target(args.get("path"), must_exist=True).path
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"path": str(p), "content": content[:12000], "bytes": len(content.encode("utf-8"))}

    def _list_directory(self, args: dict) -> dict:
        p = self.resolve_tool_target(args.get("path") or ".", must_exist=True).path
        items = [{"name": child.name, "type": "directory" if child.is_dir() else "file"} for child in sorted(p.iterdir())]
        return {"path": str(p), "items": items, "count": len(items)}

    def _delete_path(self, args: dict) -> dict:
        p = self.resolve_tool_target(args.get("path"), must_exist=True).path
        if p.is_dir():
            for child in sorted(p.iterdir(), reverse=True):
                if child.is_dir():
                    self._delete_path({"path": str(child)})
                else:
                    child.unlink()
            p.rmdir()
        else:
            p.unlink()
        return {"path": str(p), "deleted": True}

    # _classify_shell_command and _execute_shell were deleted with the
    # shell.execute registration above. Nothing in this class builds a command
    # line any more; _run_project_tests below is the only remaining subprocess
    # call, and it chooses its argv from a closed allow-list with shell=False.

    def _run_project_tests(self, args: dict) -> dict:
        """Run a supported test runner inside the project. No shell, ever.

        The model may pick which supported runner to use and which test path to
        target, but never the command line: there is no argument that reaches a
        shell from here.
        """
        target = self.resolve_tool_target(args.get("path") or ".", must_exist=True)
        if target.scope != Scope.CURRENT_WORKSPACE:
            raise ToolExecutionError("project_tests_outside_workspace_blocked")
        if not target.path.is_dir():
            raise ToolExecutionError("project_path_not_a_directory")

        runner = str(args.get("runner") or "pytest").strip().lower()
        if runner not in _ALLOWED_TEST_RUNNERS:
            raise ToolExecutionError(f"unsupported_test_runner:{runner}")

        argv = [_python_executable(), *_ALLOWED_TEST_RUNNERS[runner]]
        test_path = args.get("test_path")
        if isinstance(test_path, str) and test_path.strip():
            scoped = self.resolve_tool_target(test_path, base=target.path, must_exist=True)
            if scoped.scope != Scope.CURRENT_WORKSPACE:
                raise ToolExecutionError("test_path_outside_workspace_blocked")
            argv.append(str(scoped.path))

        result = subprocess.run(argv, cwd=str(target.path), shell=False, capture_output=True, text=True, timeout=110)
        return {
            "path": str(target.path),
            "runner": runner,
            "argv": argv,
            "returncode": result.returncode,
            "stdout": result.stdout[:12000],
            "stderr": result.stderr[:12000],
            "success": result.returncode == 0,
        }

    def _search_web(self, args: dict) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolExecutionError("query obrigatório")
        engine = str(args.get("engine") or "duckduckgo").lower()
        encoded = query.replace(" ", "+")
        urls = {
            "duckduckgo": f"https://html.duckduckgo.com/html/?q={encoded}",
            "bing": f"https://www.bing.com/search?q={encoded}",
            "google": f"https://www.google.com/search?q={encoded}",
        }
        url = urls.get(engine, urls["duckduckgo"])
        ok, error = validate_public_http_url(url)
        if not ok:
            raise ToolExecutionError(error)
        response = httpx.get(url, timeout=20)
        response.raise_for_status()
        text = response.text[:8000]
        return {"engine": engine, "query": query, "url": url, "content": text, "success": True}

    def _fetch_url(self, args: dict) -> dict:
        url = str(args.get("url") or "").strip()
        if not url:
            raise ToolExecutionError("url obrigatório")
        ok, error = validate_public_http_url(url)
        if not ok:
            raise ToolExecutionError(error)
        response = httpx.get(url, timeout=20)
        response.raise_for_status()
        return {"url": url, "status_code": response.status_code, "text": response.text[:12000], "success": True}

    def list_tools(self) -> list[dict]:
        return [
            {
                "name": meta["name"],
                "description": meta["description"],
                "risk": meta["risk"],
                "timeout": meta["timeout"],
                "provider": meta["provider"],
                "retry_policy": meta.get("retry_policy", RetryPolicy.SAFE_TO_RETRY),
                "capabilities": meta.get("capabilities", []),
            }
            for meta in self.registry.values()
        ]


def _python_executable() -> str:
    import sys

    return sys.executable or "python"
