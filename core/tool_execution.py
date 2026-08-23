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
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from core import plugin_loader
from core.browser_agent import validate_public_http_url
from core.execution_scope import (
    PathValidationError,
    ResolvedTarget,
    Scope,
    resolve_target,
    workspace_root,
)
from core.permission_manager import PermissionManager
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


def _pc_control_target(tool_name: str, args: dict) -> str | None:
    """A stable, human-readable target string for one PC-control call.

    Returns None for non-PC tools, so nothing else in the registry is touched.
    """
    if not str(tool_name).startswith("pc_"):
        return None
    if tool_name == "pc_app_launch":
        value = str(args.get("app_id") or args.get("name") or "").strip()
        return f"app:{value}" if value else "app:*"
    if tool_name.startswith("pc_window_"):
        window_id = args.get("window_id")
        if window_id not in (None, ""):
            return f"window:{window_id}"
        query = str(args.get("query") or "").strip()
        return f"window:{query}" if query else "window:*"
    if tool_name in {"pc_folder_open", "pc_file_open"}:
        # A real `path` is already preferred by _resolve_target. A known-folder
        # NAME arrives as `folder` (see the handler for why) and still has to
        # bind the grant to something specific.
        if not str(args.get("path") or "").strip():
            folder = str(args.get("folder") or "").strip()
            return f"folder:{folder}" if folder else None
        return None
    if tool_name in {"pc_app_search", "pc_file_search"}:
        query = str(args.get("query") or "").strip()
        return f"query:{query}" if query else None
    if tool_name.startswith("pc_volume_"):
        return f"volume:{tool_name.rsplit('_', 1)[-1]}"
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
        self.register_tool(
            "shell.execute",
            "Executa um comando de shell de forma controlada.",
            {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"]},
            handler=self._execute_shell,
            risk="high",
            timeout=30,
            retry_policy=RetryPolicy.CONDITIONALLY_RETRYABLE,
            capabilities=["shell.execute"],
            requires_confirmation=True,
        )
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

        pc_target = _pc_control_target(name, prepared)
        if pc_target is not None:
            # TARGET BINDING FOR PC CONTROL.
            #
            # PermissionManager._resolve_target only inspects
            # (path, target, url, command, cwd). A PC tool's real target lives
            # in `name`, `app_id`, `window_id` or `query`, so without this the
            # grant key would be (capability, "*") -- and an ALLOW_ONCE for
            # "close the Calculator window" would silently authorise closing
            # Discord. Normalising into `target` here makes the existing grant
            # machinery bind to the actual thing, with no change to the
            # permission layer and no effect on any other tool.
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
        tool = auth["tool"]
        start = time.monotonic()
        timeout = float(tool.get("timeout") or 30)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(runner, tool["handler"], auth["args"], timeout)
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

    def _classify_shell_command(self, command: str) -> str:
        lower = (command or "").lower()
        if any(token in lower for token in ("rm -rf", "rmdir /s", "del /s", "format c:", "net user", "reg delete", "shutdown", "taskkill /f", "bcdedit", "certutil -decode", "system32", "/s /q", "--delete")):
            return "critical"
        if any(token in lower for token in ("curl ", "wget ", "powershell -enc", "cmd /c", "whoami", "sc delete", "net start", "mklink", "copy ", "move ", "rename ", "del ", "attrib +h", "chown", "chmod 777")):
            return "high"
        return "medium"

    def _execute_shell(self, args: dict) -> dict:
        command = str(args.get("command") or "").strip()
        if not command:
            raise ToolExecutionError("command obrigatório")
        cwd = args.get("cwd")
        if cwd is not None:
            cwd = str(self.resolve_tool_target(cwd, must_exist=True).path)
        timeout = max(1, min(int(args.get("timeout") or 30), 180))
        stdout_limit = min(int(args.get("stdout_limit") or 12000), 12000)
        stderr_limit = min(int(args.get("stderr_limit") or 12000), 12000)
        command_risk = self._classify_shell_command(command)

        # subprocess.mswindows was a Python 2 attribute and does not exist in
        # Python 3; every shell execution used to raise AttributeError here.
        if os.name == "nt":
            argv = ["cmd", "/c", command]
        else:
            argv = ["bash", "-lc", command]
        completed = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
        return {
            "command": command,
            "risk": command_risk,
            "returncode": completed.returncode,
            "stdout": completed.stdout[:stdout_limit],
            "stderr": completed.stderr[:stderr_limit],
            "success": completed.returncode == 0,
        }

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
