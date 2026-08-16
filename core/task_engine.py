"""Persistent task queue and execution state for the Nano agent."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.app_paths import DATA_DIR


class TaskEngine:
    """SQLite-backed task queue supporting status, retries, dependencies and recovery."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DATA_DIR / "nano_tasks.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    task_type TEXT NOT NULL DEFAULT 'instant',
                    priority INTEGER NOT NULL DEFAULT 5,
                    status TEXT NOT NULL DEFAULT 'QUEUED',
                    progress INTEGER NOT NULL DEFAULT 0,
                    depends_on TEXT DEFAULT NULL,
                    metadata TEXT DEFAULT '{}',
                    result TEXT DEFAULT NULL,
                    error TEXT DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT DEFAULT NULL,
                    finished_at TEXT DEFAULT NULL,
                    retries INTEGER NOT NULL DEFAULT 0,
                    timeout_seconds INTEGER DEFAULT 300,
                    last_event TEXT DEFAULT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority DESC)")
            conn.commit()
            conn.close()

    def create_task(
        self,
        title: str,
        description: str = "",
        task_type: str = "instant",
        priority: int = 5,
        metadata: dict | None = None,
        depends_on: str | None = None,
        timeout_seconds: int = 300,
    ) -> dict:
        task_id = uuid.uuid4().hex
        now = self._now()
        data = {
            "id": task_id,
            "title": title.strip() or "Nova tarefa",
            "description": description or "",
            "task_type": task_type,
            "priority": max(0, min(10, int(priority))),
            "status": "QUEUED",
            "progress": 0,
            "depends_on": depends_on,
            "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "retries": 0,
            "timeout_seconds": timeout_seconds,
            "last_event": "created",
        }
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.execute(
                """
                INSERT INTO tasks (
                    id, title, description, task_type, priority, status, progress, depends_on, metadata,
                    result, error, created_at, updated_at, started_at, finished_at, retries,
                    timeout_seconds, last_event
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["id"], data["title"], data["description"], data["task_type"], data["priority"],
                    data["status"], data["progress"], data["depends_on"], data["metadata"], data["result"],
                    data["error"], data["created_at"], data["updated_at"], data["started_at"],
                    data["finished_at"], data["retries"], data["timeout_seconds"], data["last_event"],
                ),
            )
            conn.commit()
            conn.close()
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict | None:
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            conn.close()
        if not row:
            return None
        return self._row_to_dict(row)

    def _row_to_dict(self, row: sqlite3.Row | tuple) -> dict:
        if isinstance(row, sqlite3.Row):
            values = dict(row)
        else:
            keys = [
                "id", "title", "description", "task_type", "priority", "status", "progress",
                "depends_on", "metadata", "result", "error", "created_at", "updated_at",
                "started_at", "finished_at", "retries", "timeout_seconds", "last_event",
            ]
            values = dict(zip(keys, row))
        metadata = values.get("metadata") or "{}"
        try:
            parsed_metadata = json.loads(metadata)
        except (TypeError, json.JSONDecodeError):
            parsed_metadata = {}

        raw_result = values.get("result")
        parsed_result = raw_result
        if isinstance(raw_result, str):
            try:
                parsed_result = json.loads(raw_result)
            except (TypeError, json.JSONDecodeError):
                parsed_result = raw_result

        return {
            "id": values.get("id"),
            "title": values.get("title"),
            "description": values.get("description"),
            "task_type": values.get("task_type"),
            "priority": int(values.get("priority") or 5),
            "status": values.get("status"),
            "progress": int(values.get("progress") or 0),
            "depends_on": values.get("depends_on"),
            "metadata": parsed_metadata,
            "result": parsed_result,
            "error": values.get("error"),
            "created_at": values.get("created_at"),
            "updated_at": values.get("updated_at"),
            "started_at": values.get("started_at"),
            "finished_at": values.get("finished_at"),
            "retries": int(values.get("retries") or 0),
            "timeout_seconds": int(values.get("timeout_seconds") or 300),
            "last_event": values.get("last_event"),
        }

    def list_tasks(self, status: str | None = None, limit: int | None = None) -> list[dict]:
        query = "SELECT * FROM tasks"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY priority DESC, created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(1, int(limit)))
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            rows = conn.execute(query, params).fetchall()
            conn.close()
        return [self._row_to_dict(row) for row in rows]

    def get_status_summary(self) -> dict:
        statuses = {
            "QUEUED": 0,
            "PLANNING": 0,
            "RUNNING": 0,
            "WAITING_PERMISSION": 0,
            "RETRYING": 0,
            "RECOVERABLE": 0,
            "NEEDS_ATTENTION": 0,
            "COMPLETED": 0,
            "FAILED": 0,
            "CANCELLED": 0,
        }
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            rows = conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall()
            conn.close()
        for status, count in rows:
            if status in statuses:
                statuses[status] = int(count)
        return statuses

    def mark_recoverable(self, task_id: str, reason: str = "recoverable") -> dict | None:
        return self.update_task(task_id, status="RECOVERABLE", progress=0, last_event=reason)

    def mark_needs_attention(self, task_id: str, error: str) -> dict | None:
        return self.update_task(task_id, status="NEEDS_ATTENTION", progress=100, error=error, last_event="needs_attention")

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        result: Any | None = None,
        error: str | None = None,
        last_event: str | None = None,
        metadata: dict | None = None,
    ) -> dict | None:
        current = self.get_task(task_id)
        if not current:
            return None
        now = self._now()
        new_status = status or current["status"]
        new_progress = progress if progress is not None else current["progress"]
        new_result = result if result is not None else current.get("result")
        new_error = error if error is not None else current.get("error")
        new_last_event = last_event or current.get("last_event") or "updated"
        new_metadata = current["metadata"]
        if metadata is not None:
            new_metadata = dict(current["metadata"], **metadata)
        if new_status in {"RUNNING", "WAITING", "WAITING_FOR_PERMISSION", "RETRYING", "PLANNING", "RECOVERABLE", "NEEDS_ATTENTION"} and not current.get("started_at"):
            started_at = now
        else:
            started_at = current.get("started_at")
        if new_status in {"COMPLETED", "FAILED", "RECOVERABLE", "NEEDS_ATTENTION", "CANCELLED"} and not current.get("finished_at"):
            finished_at = now
        else:
            finished_at = current.get("finished_at")
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, progress = ?, result = ?, error = ?, metadata = ?, started_at = COALESCE(started_at, ?),
                    finished_at = COALESCE(finished_at, ?), updated_at = ?, last_event = ?
                WHERE id = ?
                """,
                (
                    new_status,
                    new_progress,
                    json.dumps(new_result, ensure_ascii=False) if new_result is not None else current.get("result"),
                    new_error,
                    json.dumps(new_metadata, ensure_ascii=False),
                    started_at,
                    finished_at,
                    now,
                    new_last_event,
                    task_id,
                ),
            )
            conn.commit()
            conn.close()
        return self.get_task(task_id)

    def recover_pending_tasks(self) -> list[dict]:
        pending = []
        for task in self.list_tasks():
            if task["status"] not in {"COMPLETED", "FAILED", "RECOVERABLE", "NEEDS_ATTENTION", "CANCELLED"}:
                pending.append(task)
        return pending

    def cancel_task(self, task_id: str) -> dict | None:
        return self.update_task(task_id, status="CANCELLED", progress=100, last_event="cancelled")

    def mark_running(self, task_id: str, progress: int = 0) -> dict | None:
        return self.update_task(task_id, status="RUNNING", progress=progress, last_event="started")

    def mark_failed(self, task_id: str, error: str) -> dict | None:
        return self.update_task(task_id, status="FAILED", progress=100, error=error, last_event="failed")

    def mark_complete(self, task_id: str, result: Any | None = None) -> dict | None:
        return self.update_task(task_id, status="COMPLETED", progress=100, result=result, last_event="completed")

    def retry_task(self, task_id: str) -> dict | None:
        task = self.get_task(task_id)
        if not task:
            return None
        retries = int(task.get("retries") or 0) + 1
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.execute(
                "UPDATE tasks SET status = ?, retries = ?, progress = 0, error = NULL, updated_at = ?, last_event = ? WHERE id = ?",
                ("RETRYING", retries, self._now(), "retrying", task_id),
            )
            conn.commit()
            conn.close()
        return self.get_task(task_id)

    def queue_size(self) -> int:
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            row = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
            conn.close()
        return int(row[0]) if row else 0
