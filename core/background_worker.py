"""Real background executor for Nano tasks."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.task_engine import TaskEngine
from core.events import EventBus
from core.tool_execution import ToolExecutor
from core.context_engine import ContextEngine
from core.memory import MemoryEngine
from core.permission_manager import PermissionManager


# Hard ceiling on automatic retries. Exceeding it parks the task for a human
# rather than looping: an unbounded retry hammered external services and
# re-ran approved actions without ever asking again.
MAX_AUTO_RETRIES = 2

# Per-policy retry budget. Actions that are not safe to repeat blindly get none.
RETRY_BUDGET = {
    "SAFE_TO_RETRY": MAX_AUTO_RETRIES,
    "CONDITIONALLY_RETRYABLE": 1,
    "NOT_SAFE_TO_RETRY": 0,
}

TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "NEEDS_ATTENTION"})


class BackgroundTaskWorker:
    """Long-running worker that executes queued task requests and verifies outputs."""

    def __init__(
        self,
        task_engine: TaskEngine,
        event_bus: EventBus,
        context_engine: ContextEngine,
        memory: MemoryEngine,
        tool_executor: ToolExecutor | None = None,
        permission_manager: PermissionManager | None = None,
        poll_interval: float = 1.0,
    ):
        self.task_engine = task_engine
        self.event_bus = event_bus
        self.context_engine = context_engine
        self.memory = memory
        self.tool_executor = tool_executor or ToolExecutor(permission_manager=permission_manager or PermissionManager(), event_bus=event_bus)
        self.poll_interval = float(poll_interval)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

    def start(self) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"running": True, "message": "Worker already active."}
            self._recover_abandoned_tasks()
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name="NanoTaskWorker", daemon=True)
            self._thread.start()
            self.event_bus.publish("worker.started", {"running": True})
            return {"running": True, "message": "Worker started."}

    def stop(self) -> dict:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.event_bus.publish("worker.stopped", {"running": False})
        return {"running": False, "message": "Worker stopped."}

    def status(self) -> dict:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "poll_interval": self.poll_interval,
            "queue_size": self.task_engine.queue_size(),
        }

    def _recover_abandoned_tasks(self) -> None:
        for task in self.task_engine.list_tasks():
            status = task.get("status")
            started = task.get("started_at")
            if status not in {"RUNNING", "PLANNING", "WAITING", "WAITING_FOR_PERMISSION"}:
                continue
            if not started:
                self.task_engine.mark_recoverable(task["id"], reason="recovered_from_missing_start")
                continue
            try:
                started_dt = datetime.fromisoformat(started)
                if (datetime.now(timezone.utc) - started_dt).total_seconds() > max(30, int(task.get("timeout_seconds") or 300)):
                    self.task_engine.mark_recoverable(task["id"], reason="recovered_after_timeout")
            except ValueError:
                self.task_engine.mark_recoverable(task["id"], reason="recovered_after_invalid_start")

    def _next_ready_task(self) -> dict | None:
        for task in self.task_engine.list_tasks():
            if task.get("status") not in {"QUEUED", "RETRYING", "RECOVERABLE"}:
                continue
            if int(task.get("retries") or 0) > MAX_AUTO_RETRIES:
                # Past the ceiling the task is parked rather than re-queued, so
                # a permanently failing task cannot spin the worker forever.
                self.task_engine.mark_needs_attention(task["id"], f"Excedido o limite de {MAX_AUTO_RETRIES} retries automáticos.")
                self.event_bus.publish("task.needs_attention", {"task_id": task["id"], "error": "retry_ceiling_exceeded", "retries": task.get("retries")})
                self._release_grants(task["id"])
                continue
            return task
        return None

    def _resolve_execution_plan(self, task: dict) -> list[dict]:
        description = task.get("description") or ""
        title = task.get("title") or ""
        combined_text = f"{description}\n{title}".strip()
        lower_desc = combined_text.lower()

        folder_name = self._extract_target_path(combined_text)
        file_target = self._extract_file_targets(combined_text)
        if ("create" in lower_desc or "cria" in lower_desc) and ("folder" in lower_desc or "directory" in lower_desc or "pasta" in lower_desc or "diretório" in lower_desc or "directorio" in lower_desc):
            steps: list[dict] = []
            if folder_name:
                resolved_folder = self._resolve_task_path(folder_name)
                steps.append({"tool": "filesystem.create_directory", "args": {"path": resolved_folder}, "verification": {"type": "exists", "path": resolved_folder}})
            for target in file_target:
                if target.endswith(".txt"):
                    path = f"{folder_name}/{target}" if folder_name else target
                    resolved_path = self._resolve_task_path(path)
                    steps.append({"tool": "filesystem.write_file", "args": {"path": resolved_path, "content": "Olá Nano!\n"}, "verification": {"type": "exists", "path": resolved_path}})
            if steps:
                return steps
        if "ficheiro" in lower_desc or "file" in lower_desc or "hello.txt" in lower_desc:
            for target in file_target:
                if target.endswith(".txt"):
                    path = f"{folder_name}/{target}" if folder_name else target
                    resolved_path = self._resolve_task_path(path)
                    return [{"tool": "filesystem.write_file", "args": {"path": resolved_path, "content": "Olá Nano!\n"}, "verification": {"type": "exists", "path": resolved_path}}]
        if "test" in lower_desc or "pytest" in lower_desc:
            return [{"tool": "project.run_tests", "args": {"path": ".", "command": "python -m pytest -q"}, "verification": {"type": "exit_code", "expected": 0}}]
        if "search" in lower_desc or "pesquisa" in lower_desc or "procura" in lower_desc:
            query = self._extract_search_query(combined_text)
            return [{"tool": "browser.search_web", "args": {"query": query or "nano agent", "engine": "duckduckgo"}, "verification": {"type": "text_present", "needle": query or "nano"}}]
        if "delete" in lower_desc or "apagar" in lower_desc or "remove" in lower_desc:
            path = self._extract_target_path(combined_text)
            if path:
                resolved_path = self._resolve_task_path(path)
                return [{"tool": "filesystem.delete_path", "args": {"path": resolved_path}, "verification": {"type": "not_exists", "path": resolved_path}}]
        # No plan matched. This used to fall back to `shell.execute` running
        # `pwd` -- a shell call standing in for "I could not work out what to
        # do", on a capability Nano does not have at all. An unplannable task
        # is now reported as unplannable: _run_task marks it failed with
        # "Nenhuma ação executável foi identificada", which is the truth.
        return []

    def _extract_target_path(self, text: str) -> str | None:
        lowered = text.lower()
        for keyword in ("pasta", "folder", "directory", "diretório", "directorio"):
            idx = lowered.find(keyword)
            if idx != -1:
                tokens = text[idx + len(keyword):].replace("'", ' ').replace('"', ' ').split()
                for token in tokens:
                    token = token.strip(" .,:;()[]{}")
                    if token and not token.lower() in {"e", "a", "o", "para", "com", "dentro", "da"}:
                        return token
        for token in text.replace("'", ' ').replace('"', ' ').split():
            token = token.strip(" .,:;()[]{}")
            if token.startswith(".") or token.startswith("/") or (":" in token and len(token) > 2):
                return token
        if "test-nano" in text.lower():
            return "test-nano"
        if "hello.txt" in text.lower():
            return "test-nano"
        return None

    def _extract_file_targets(self, text: str) -> list[str]:
        matches = []
        for token in text.replace("'", ' ').replace('"', ' ').split():
            if token.endswith(".txt") or token.endswith(".md") or token.endswith(".py"):
                matches.append(token)
        if not matches:
            return ["hello.txt"]
        return matches

    def _extract_search_query(self, text: str) -> str:
        cleaned = text.replace("pesquisa", "").replace("search", "").replace("procura", "").strip()
        if not cleaned:
            return "Nano agent"
        return cleaned.strip(" .")

    def _workspace_root(self) -> Path:
        db_path = getattr(self.task_engine, "db_path", None)
        if db_path is not None:
            return Path(db_path).parent
        return Path.cwd()

    def _resolve_task_path(self, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        if not candidate:
            return candidate
        path = Path(candidate)
        if path.is_absolute():
            return str(path)
        return str((self._workspace_root() / path))

    def _normalize_path(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return normalized
        return normalized.replace("/", "\\").lower()

    def _verify_execution(self, step: dict, result: dict) -> bool:
        verification = step.get("verification")
        if not verification:
            return bool(result.get("success"))
        kind = verification.get("type")
        if kind == "exit_code":
            expected = verification.get("expected", 0)
            if isinstance(result.get("output"), dict):
                return bool(result["output"].get("returncode") == expected)
            return bool(result.get("success"))
        if kind == "exists":
            path = verification.get("path")
            if not path:
                return False
            output = result.get("output") or {}
            actual_path = output.get("path")
            if isinstance(actual_path, str):
                if self._normalize_path(actual_path) == self._normalize_path(path):
                    return True
            if output.get("created") is True:
                return True
            return bool(result.get("success") and path and Path(path).exists())
        if kind == "not_exists":
            path = verification.get("path")
            if not path:
                return False
            output = result.get("output") or {}
            if output.get("deleted") is True:
                return True
            return bool(result.get("success") and path and not Path(path).exists())
        if kind == "text_present":
            needle = str(verification.get("needle") or "")
            content = str((result.get("output") or {}).get("content") or "")
            return needle.lower() in content.lower()
        return bool(result.get("success"))

    def process_task(self, task: dict) -> dict:
        task_id = task["id"]

        # Cancellation is checked before the first status write. Setting
        # PLANNING first would overwrite CANCELLED and resurrect the task.
        if self.is_cancelled(task_id):
            self.event_bus.publish("task.cancelled", {"task_id": task_id, "stage": "before_planning"})
            self._release_grants(task_id)
            return self.task_engine.get_task(task_id)

        self.task_engine.update_task(task_id, status="PLANNING", progress=10, last_event="planning")
        self.event_bus.publish("task.planning", {"task_id": task_id, "title": task["title"]})

        plan = self._resolve_execution_plan(task)
        if not plan:
            self.task_engine.mark_failed(task_id, "Nenhuma ação executável foi identificada para a tarefa.")
            return self.task_engine.get_task(task_id)

        results: list[dict] = []
        for index, step in enumerate(plan, start=1):
            step_name = step.get("tool")

            # Cancellation is checked before every step against the persisted
            # state, so a cancelled task actually stops instead of having its
            # status overwritten by the next update.
            if self.is_cancelled(task_id):
                self.event_bus.publish("task.cancelled", {"task_id": task_id, "tool": step_name})
                self._release_grants(task_id)
                return self.task_engine.get_task(task_id)

            self.task_engine.update_task(task_id, status="RUNNING", progress=min(90, int(index / max(len(plan), 1) * 100)), last_event=f"executing:{step_name}")
            self.event_bus.publish("task.step", {"task_id": task_id, "tool": step_name, "index": index, "total": len(plan)})

            # Every attempt, including retries, goes back through the executor,
            # so a retry can never reuse or outlive a permission decision.
            result = self.tool_executor.execute_tool(step_name, step.get("args") or {}, task_id=task_id)
            results.append({"step": step_name, "result": result})

            if result.get("status") == "permission_denied":
                self.task_engine.mark_needs_attention(task_id, f"Permissão recusada para {step_name}.")
                self.event_bus.publish("task.needs_attention", {"task_id": task_id, "tool": step_name, "error": "permission_denied"})
                self._release_grants(task_id)
                return self.task_engine.get_task(task_id)

            if not self._verify_execution(step, result):
                return self._handle_verification_failure(task_id, task, step_name)

        completed = {"task_id": task_id, "steps": results, "status": "completed"}
        self.task_engine.mark_complete(task_id, completed)
        self.event_bus.publish("task.completed", {"task_id": task_id, "title": task.get("title"), "result": completed})
        self._release_grants(task_id)
        return self.task_engine.get_task(task_id)

    def is_cancelled(self, task_id: str) -> bool:
        """Read the persisted status so cancellation survives in-flight state."""
        current = self.task_engine.get_task(task_id)
        return bool(current and current.get("status") == "CANCELLED")

    def cancel_task(self, task_id: str) -> dict | None:
        """Cancel a task and release anything it was authorised to do."""
        cancelled = self.task_engine.cancel_task(task_id)
        self._release_grants(task_id)
        self.event_bus.publish("task.cancelled", {"task_id": task_id, "source": "user"})
        return cancelled

    def _release_grants(self, task_id: str) -> None:
        """Drop task-scoped permissions the moment the task stops running."""
        manager = getattr(self.tool_executor, "permission_manager", None)
        release = getattr(manager, "release_task_grants", None)
        if callable(release):
            try:
                release(task_id)
            except Exception:
                self.event_bus.publish("worker.error", {"error": "grant_release_failed", "task_id": task_id})

    def _handle_verification_failure(self, task_id: str, task: dict, step_name: str) -> dict | None:
        """Apply this tool's retry budget, then park the task once it is spent."""
        retry_policy = self.tool_executor.get_retry_policy(step_name)
        budget = RETRY_BUDGET.get(retry_policy, 0)
        attempts = int((self.task_engine.get_task(task_id) or task).get("retries") or 0)

        if attempts >= budget:
            reason = (
                f"Verificação falhou para {step_name}; ação não segura para retry automático."
                if budget == 0
                else f"Retry esgotado para {step_name} ({attempts}/{budget})."
            )
            self.task_engine.mark_needs_attention(task_id, reason)
            self.event_bus.publish("task.needs_attention", {"task_id": task_id, "tool": step_name, "error": reason, "retries": attempts})
            self._release_grants(task_id)
            return self.task_engine.get_task(task_id)

        self.task_engine.retry_task(task_id)
        self.event_bus.publish("task.retrying", {
            "task_id": task_id, "tool": step_name, "reason": "verification_failed",
            "attempt": attempts + 1, "budget": budget,
        })
        return self.task_engine.get_task(task_id)

    def _loop(self) -> None:
        self.event_bus.publish("worker.loop.start", {"running": True})
        while not self._stop_event.is_set():
            task = None
            try:
                task = self._next_ready_task()
                if task is None:
                    self._stop_event.wait(self.poll_interval)
                    continue
                self.task_engine.update_task(task["id"], status="RUNNING", progress=5, last_event="claimed")
                self.event_bus.publish("task.started", {"task_id": task["id"], "title": task.get("title"), "status": "RUNNING"})
                self.process_task(task)
            except Exception as exc:  # pragma: no cover - safety net for worker loop
                # A crash used to leave the task RUNNING forever. RUNNING is not
                # a ready status, so nothing ever picked the task up again.
                if task is not None:
                    try:
                        self.task_engine.mark_needs_attention(task["id"], f"Worker exception: {exc}")
                        self._release_grants(task["id"])
                    except Exception:
                        pass
                self.event_bus.publish("worker.error", {"error": str(exc), "task_id": (task or {}).get("id")})
                time.sleep(self.poll_interval)

    def claim_task(self, task_id: str) -> dict | None:
        task = self.task_engine.get_task(task_id)
        if not task:
            return None
        if task.get("status") not in {"QUEUED", "RETRYING"}:
            return task
        self.task_engine.update_task(task_id, status="RUNNING", progress=5, last_event="claimed")
        return self.task_engine.get_task(task_id)

    def execute_now(self, task_id: str) -> dict | None:
        task = self.task_engine.get_task(task_id)
        if not task:
            return None
        self.claim_task(task_id)
        return self.process_task(self.task_engine.get_task(task_id))
