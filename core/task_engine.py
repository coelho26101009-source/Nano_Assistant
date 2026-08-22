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


# Hard ceilings on what a single task row may persist. A recursive context bug
# once grew metadata exponentially and produced a 1.5 GB task database; these
# caps make that class of bug loud and bounded instead of silent and unbounded.
#
# The size cap alone is not enough. It was applied on create_task only, while
# growth actually happens in update_task, which MERGES the new metadata into
# whatever is already stored -- so every update carried the previous blob
# forward and the one guarded write was the one that could not grow. Both
# writers now go through the same bounded encoder.
MAX_METADATA_BYTES = 64 * 1024
MAX_RESULT_BYTES = 128 * 1024

# Nesting deeper than this is treated as a runaway structure rather than data.
# This is the guard that actually stops the recursive case: a task whose
# metadata embeds a snapshot of the task (which embeds its metadata, ...) grows
# by DEPTH, not by breadth, so a byte cap only notices once the blob is already
# enormous. Pruning by depth is also what makes a genuine reference cycle safe
# to serialise at all -- json.dumps raises ValueError on one.
MAX_JSON_DEPTH = 8

# Individual values up to this size survive a trim intact; larger ones are
# replaced by an explicit marker so the row stays valid JSON and the loss is
# visible rather than silent.
MAX_KEPT_VALUE_BYTES = 4096


def _prune_depth(value: Any, *, depth: int = 0, limit: int = MAX_JSON_DEPTH) -> Any:
    """Rebuild a structure with anything below ``limit`` levels replaced.

    Returning a marker instead of the sub-tree bounds both runaway nesting and
    reference cycles: the recursion is depth-limited, so a self-referential
    object terminates instead of recursing until the interpreter gives up.
    """
    if depth >= limit:
        if isinstance(value, (dict, list, tuple, set)):
            return f"<omitido: estrutura mais profunda que {limit} níveis>"
        return value
    if isinstance(value, dict):
        return {str(k): _prune_depth(v, depth=depth + 1, limit=limit) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_prune_depth(v, depth=depth + 1, limit=limit) for v in value]
    return value


def _dumps_safe(payload: Any) -> str:
    """json.dumps that cannot raise on an unserialisable or cyclic payload."""
    try:
        return json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        # default=str handles unserialisable leaves; the depth prune above has
        # already removed any cycle, so this is the belt to that braces.
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return json.dumps({"_encoding_failed": True}, ensure_ascii=False)


def _encode_bounded(payload: Any, *, max_bytes: int, marker: str) -> str:
    """Serialise a payload, refusing to persist an unbounded blob.

    Valid data that fits is stored byte-for-byte unchanged. Only a payload that
    exceeds the ceiling is trimmed, and the trim is recorded in the row itself
    (``marker``) so a reader can tell truncated data from complete data.
    """
    pruned = _prune_depth(payload)
    encoded = _dumps_safe(pruned)
    if len(encoded.encode("utf-8")) <= max_bytes:
        return encoded

    # Keep the small, useful keys and record why the rest was dropped, rather
    # than truncating into invalid JSON.
    if isinstance(pruned, dict):
        trimmed: dict[str, Any] = {}
        for key, value in pruned.items():
            candidate = _dumps_safe(value)
            if len(candidate.encode("utf-8")) <= MAX_KEPT_VALUE_BYTES:
                trimmed[key] = value
            else:
                trimmed[key] = f"<omitido: {len(candidate)} bytes excedem o limite>"
        trimmed[marker] = True
        result = _dumps_safe(trimmed)
        if len(result.encode("utf-8")) <= max_bytes:
            return result
        # Even the trimmed row is too large (very many keys). Refuse it whole
        # rather than persisting something unbounded.
        return _dumps_safe({marker: True, "_dropped_keys": len(trimmed)})

    return _dumps_safe({marker: True, "_dropped_bytes": len(encoded.encode("utf-8"))})


def _encode_metadata(metadata: dict | None) -> str:
    """Serialise task metadata under the metadata ceiling."""
    return _encode_bounded(metadata or {}, max_bytes=MAX_METADATA_BYTES, marker="_metadata_truncated")


def _encode_result(result: Any) -> str:
    """Serialise a task result under the result ceiling.

    Results carry raw tool output -- a fetched page is up to 12 KB per step --
    so this is the other blob that can grow without a bound of its own.
    """
    return _encode_bounded(result, max_bytes=MAX_RESULT_BYTES, marker="_result_truncated")


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
            "metadata": _encode_metadata(metadata),
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
                    # Both blobs go through the SAME bounded encoders create_task
                    # uses. A bare json.dumps here is what let the metadata cap
                    # be bypassed on the only write path that accumulates.
                    _encode_result(new_result) if new_result is not None else current.get("result"),
                    new_error,
                    _encode_metadata(new_metadata),
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

    def delete_task(self, task_id: str) -> bool:
        """Remove one task row from the queue.

        This is queue housekeeping only. The permission audit log and the event
        stream live elsewhere and are deliberately untouched: they are the
        security record, not the user's task list.
        """
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            try:
                cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def queue_size(self) -> int:
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            row = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
            conn.close()
        return int(row[0]) if row else 0
