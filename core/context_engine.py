"""Context builder for Nano task planning and memory relevance."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.memory import MemoryEngine


# Fields worth carrying as context. Deliberately excludes 'metadata' and
# 'result': those are unbounded and are what caused the recursive blow-up.
_TASK_SUMMARY_FIELDS = ("id", "title", "status", "task_type", "progress", "priority")


def _summarize_task(task: dict) -> dict:
    """Flatten a task to a small, fixed set of scalar fields."""
    summary = {key: task.get(key) for key in _TASK_SUMMARY_FIELDS}
    title = summary.get("title")
    if isinstance(title, str):
        summary["title"] = title[:120]
    return summary


@dataclass
class ContextSnapshot:
    user_profile: dict = field(default_factory=dict)
    relevant_memories: list[dict] = field(default_factory=list)
    active_tasks: list[dict] = field(default_factory=list)
    system_state: dict = field(default_factory=dict)
    request: str = ""
    generated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "user_profile": self.user_profile,
            "relevant_memories": self.relevant_memories,
            "active_tasks": self.active_tasks,
            "system_state": self.system_state,
            "request": self.request,
            "generated_at": self.generated_at,
        }


class ContextEngine:
    """Builds a compact but relevant context window for each user request."""

    def __init__(self, memory: MemoryEngine, task_engine: Any | None = None):
        self.memory = memory
        self.task_engine = task_engine

    def build_context(self, request: str, *, extra: dict | None = None) -> ContextSnapshot:
        profile = self.memory.get_user_profile()
        relevant_memories = self.memory.search_memory((request or "").strip() or "user profile", limit=5)
        # Only a compact summary of each task, never the whole task dict.
        #
        # This context snapshot is itself persisted into the metadata of the
        # task being created. Embedding full task dicts therefore embedded the
        # metadata of the previous 5 tasks, which already embedded theirs, and
        # so on: metadata doubled with every task (810 -> 2 KB -> 4 KB -> 40 KB
        # -> ...), producing a 1.5 GB task database and multi-GB RSS, because
        # the Command Center re-reads and JSON-parses those rows every 3s.
        active_tasks = (
            [_summarize_task(task) for task in self.task_engine.list_tasks(limit=5)]
            if self.task_engine
            else []
        )
        system_state = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "platform": "local-desktop",
            "local_first": True,
        }
        snapshot = ContextSnapshot(
            user_profile=profile,
            relevant_memories=relevant_memories,
            active_tasks=active_tasks,
            system_state={**system_state, **(extra or {})},
            request=request or "",
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        return snapshot

    def build_prompt_context(self, request: str, *, extra: dict | None = None) -> str:
        snapshot = self.build_context(request, extra=extra)
        profile = snapshot.user_profile or {}
        facts = [f"- {key}: {value}" for key, value in profile.items()]
        memories = [f"- {item.get('content', '')}" for item in snapshot.relevant_memories[:5]]
        tasks = [f"- {item.get('title', '')} [{item.get('status', '')}]" for item in snapshot.active_tasks[:5]]
        lines = ["Contexto relevante do Nano:"]
        if facts:
            lines.append("Perfil do utilizador:")
            lines.extend(facts)
        if memories:
            lines.append("Memórias relevantes:")
            lines.extend(memories)
        if tasks:
            lines.append("Tarefas ativas:")
            lines.extend(tasks)
        return "\n".join(lines)
