"""Central tool registry and execution layer for Nano agents."""
from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from core.permission_manager import PermissionManager


class ToolExecutionError(RuntimeError):
    """Raised when a tool cannot be executed safely or fails validation."""


class RetryPolicy(str):
    SAFE_TO_RETRY = "SAFE_TO_RETRY"
    CONDITIONALLY_RETRYABLE = "CONDITIONALLY_RETRYABLE"
    NOT_SAFE_TO_RETRY = "NOT_SAFE_TO_RETRY"


class ToolExecutor:
    """Registry and runner for real Nano tools with permission enforcement."""

    def __init__(self, permission_manager: PermissionManager | None = None, event_bus: Any | None = None):
        self.permission_manager = permission_manager or PermissionManager()
        self.event_bus = event_bus
        self.registry: dict[str, dict] = {}
        self._register_default_tools()

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

    def _validate_path(self, value: str | None, *, allow_absolute: bool = False, must_exist: bool = False, workspace_root: str | Path | None = None) -> Path:
        if value is None:
            raise ToolExecutionError("path_required")
        raw = str(value).strip()
        if not raw:
            raise ToolExecutionError("path_required")
        if ".." in raw.split("/") or ".." in raw.split("\\"):
            raise ToolExecutionError("path_traversal_blocked")
        candidate = Path(raw)
        if candidate.is_absolute():
            safe_roots = [Path.cwd(), Path.home(), Path(tempfile.gettempdir())]
            if allow_absolute:
                resolved = candidate
            else:
                resolved = candidate
                allowed = False
                for root in safe_roots:
                    try:
                        candidate.relative_to(root)
                        allowed = True
                        break
                    except ValueError:
                        continue
                if not allowed:
                    raise ToolExecutionError("absolute_path_blocked")
        else:
            root = Path(workspace_root) if workspace_root is not None else Path.cwd()
            resolved = (root / candidate).resolve()
        if must_exist and not resolved.exists():
            raise ToolExecutionError(f"path_not_found:{resolved}")
        return resolved

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
            "Executa a suíte de testes do projeto atual.",
            {"type": "object", "properties": {"path": {"type": "string"}, "command": {"type": "string"}}, "required": ["path"]},
            handler=self._run_project_tests,
            risk="medium",
            timeout=60,
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

    def _run_handler(self, handler: Callable[[dict], Any], args: dict) -> Any:
        result = handler(args)
        if inspect.isawaitable(result):
            try:
                return asyncio.run(result)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(result)
                finally:
                    loop.close()
        return result

    def execute_tool(self, name: str, args: dict | None = None, *, task_id: str | None = None) -> dict:
        args = args or {}
        tool = self.registry.get(name)
        if not tool:
            return self._tool_result(False, "unknown_tool", error=f"Tool desconhecida: {name}", metadata={"tool": name})

        tool_capability = (tool.get("capabilities") or [name])[0]
        if self.permission_manager.is_emergency_stopped():
            self.permission_manager.log_decision(tool_capability, "deny", risk=tool.get("risk"), task_id=task_id, reason="Emergency stop engaged; execution blocked.", event_name="PermissionDenied")
            return self._tool_result(False, "permission_denied", error="Emergency stop active: execution blocked by the Nano Policy Engine.", metadata={"tool": name, "task_id": task_id})

        decision_value = self.permission_manager.get_decision_for_action(tool_capability, args)
        if decision_value == "deny":
            self.permission_manager.log_decision(tool_capability, "deny", risk=tool.get("risk"), task_id=task_id, reason="Permission policy denied execution", event_name="PermissionDenied")
            return self._tool_result(False, "permission_denied", error="Permissão negada pela política do sistema.", metadata={"tool": name})
        decision = self.permission_manager.evaluate(tool_capability, args)
        if decision.requires_confirmation and not self.permission_manager.ask_for_confirmation(tool_capability, args):
            self.permission_manager.log_decision(tool_capability, "deny", risk=decision.risk, task_id=task_id, reason="User declined risk confirmation", event_name="PermissionDenied")
            return self._tool_result(False, "permission_denied", error="Autorização recusada pelo utilizador.", metadata={"tool": name, "risk": decision.risk.value})

        if "path" in args and isinstance(args["path"], str):
            try:
                self._validate_path(args["path"], allow_absolute=False)
            except ToolExecutionError as exc:
                return self._tool_result(False, "invalid_input", error=str(exc), metadata={"tool": name, "task_id": task_id})

        start = time.monotonic()
        try:
            output = self._run_handler(tool["handler"], args)
            duration_ms = int((time.monotonic() - start) * 1000)
            wrapped = self._tool_result(True, "completed", output=output, metadata={"tool": name, "task_id": task_id, "duration_ms": duration_ms, "retry_policy": self.get_retry_policy(name), "permission_checked": True})
            self._publish("tool.executed", {"tool": name, "task_id": task_id, "ok": True, "duration_ms": duration_ms, "retry_policy": self.get_retry_policy(name)})
            return wrapped
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            payload = self._tool_result(False, "failed", error=str(exc), metadata={"tool": name, "task_id": task_id, "duration_ms": duration_ms, "retry_policy": self.get_retry_policy(name), "permission_checked": True})
            self._publish("tool.failed", {"tool": name, "task_id": task_id, "error": str(exc), "duration_ms": duration_ms, "retry_policy": self.get_retry_policy(name)})
            return payload

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

    def _create_directory(self, args: dict) -> dict:
        path = str(args.get("path") or "").strip()
        if not path:
            raise ToolExecutionError("path obrigatório")
        p = self._validate_path(path)
        p.mkdir(parents=True, exist_ok=True)
        return {"path": str(p), "created": True}

    def _write_file(self, args: dict) -> dict:
        path = str(args.get("path") or "").strip()
        content = args.get("content", "")
        if not path:
            raise ToolExecutionError("path obrigatório")
        p = self._validate_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content), encoding="utf-8")
        return {"path": str(p), "written": True, "bytes": len(str(content).encode("utf-8"))}

    def _read_file(self, args: dict) -> dict:
        path = str(args.get("path") or "").strip()
        if not path:
            raise ToolExecutionError("path obrigatório")
        p = self._validate_path(path, must_exist=True)
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"path": str(p), "content": content[:12000], "bytes": len(content.encode("utf-8"))}

    def _list_directory(self, args: dict) -> dict:
        path = str(args.get("path") or ".").strip() or "."
        p = self._validate_path(path, must_exist=True)
        items = [{"name": child.name, "type": "directory" if child.is_dir() else "file"} for child in sorted(p.iterdir())]
        return {"path": str(p), "items": items, "count": len(items)}

    def _delete_path(self, args: dict) -> dict:
        path = str(args.get("path") or "").strip()
        if not path:
            raise ToolExecutionError("path obrigatório")
        p = self._validate_path(path, must_exist=True)
        if not p.exists():
            raise ToolExecutionError(f"Caminho não existe: {path}")
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
            cwd = str(self._validate_path(cwd, allow_absolute=True))
        timeout = max(1, min(int(args.get("timeout") or 30), 180))
        stdout_limit = min(int(args.get("stdout_limit") or 12000), 12000)
        stderr_limit = min(int(args.get("stderr_limit") or 12000), 12000)
        command_risk = self._classify_shell_command(command)
        if command_risk in {"high", "critical"}:
            decision = self.permission_manager.get_decision_for_action("shell.execute", {"command": command, "cwd": cwd})
            if decision == "deny":
                raise ToolExecutionError("shell.execute blocked by permission policy")
            if self.permission_manager.confirmation_callback is None and command_risk == "critical":
                raise ToolExecutionError("critical command requires explicit permission confirmation")
        if subprocess.mswindows:
            completed = subprocess.run(["cmd", "/c", command], cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
        else:
            completed = subprocess.run(["bash", "-lc", command], cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
        return {
            "command": command,
            "risk": command_risk,
            "returncode": completed.returncode,
            "stdout": completed.stdout[:stdout_limit],
            "stderr": completed.stderr[:stderr_limit],
            "success": completed.returncode == 0,
        }

    def _run_project_tests(self, args: dict) -> dict:
        path = str(args.get("path") or ".").strip() or "."
        safe_path = self._validate_path(path)
        cmd = str(args.get("command") or "python -m pytest -q").strip()
        result = subprocess.run(cmd, cwd=str(safe_path), shell=True, capture_output=True, text=True, timeout=60)
        return {
            "path": str(safe_path),
            "command": cmd,
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
        response = httpx.get(url, timeout=20)
        response.raise_for_status()
        text = response.text[:8000]
        return {"engine": engine, "query": query, "url": url, "content": text, "success": True}

    def _fetch_url(self, args: dict) -> dict:
        url = str(args.get("url") or "").strip()
        if not url:
            raise ToolExecutionError("url obrigatório")
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
