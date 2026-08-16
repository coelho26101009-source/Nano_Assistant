"""Agent orchestration layer for Nano: planning, task creation and execution flow."""
from __future__ import annotations

import re
from typing import Any

from core.agent_registry import AgentRegistry
from core.context_engine import ContextEngine
from core.events import EventBus
from core.permission_manager import PermissionManager
from core.task_engine import TaskEngine


class AgentOrchestrator:
    """Transforms a user request into a task plan and persists it in the NanO queue."""

    def __init__(
        self,
        memory: Any,
        task_engine: TaskEngine | None = None,
        event_bus: EventBus | None = None,
        context_engine: ContextEngine | None = None,
        permission_manager: PermissionManager | None = None,
        agent_registry: AgentRegistry | None = None,
    ):
        self.memory = memory
        self.task_engine = task_engine or TaskEngine()
        self.event_bus = event_bus or EventBus()
        self.context_engine = context_engine or ContextEngine(memory, self.task_engine)
        self.permission_manager = permission_manager or PermissionManager()
        self.agent_registry = agent_registry or AgentRegistry()

    def _classify_task(self, request: str) -> str:
        text = request.lower()
        if any(keyword in text for keyword in ("debug", "falha", "erro", "test", "build", "compile")):
            return "engineering"
        if any(keyword in text for keyword in ("pesquisa", "procura", "investiga", "web", "browser", "compare")):
            return "research"
        if any(keyword in text for keyword in ("agenda", "lembrete", "calend", "recorda")):
            return "planning"
        if any(keyword in text for keyword in ("git", "commit", "branch", "pull request", "github")):
            return "project"
        if any(keyword in text for keyword in ("abrir", "iniciar", "launch", "executar", "script")):
            return "desktop"
        return "general"

    def create_plan(self, request: str) -> dict:
        text = (request or "").strip()
        task_type = self._classify_task(text)
        if not text:
            return {"task_type": "general", "steps": []}

        steps = [
            "interpretar o pedido e extrair o objetivo principal",
            "recolher contexto relevante do utilizador e do ambiente",
            "executar as ações necessárias usando ferramentas apropriadas",
            "verificar o resultado e reportar conclusões",
        ]
        if task_type == "engineering":
            steps = [
                "identificar o problema ou falha",
                "ler o código e contexto relevante",
                "reproduzir ou validar a hipótese",
                "aplicar correção com controlo de risco",
                "executar validação e confirmar o resultado",
            ]
        elif task_type == "research":
            steps = [
                "definir fontes e critério de pesquisa",
                "coletar informação relevante",
                "comparar fontes e filtrar inconsistências",
                "sumariar conclusões com evidência",
            ]
        elif task_type == "desktop":
            steps = [
                "verificar o alvo do sistema ou aplicação",
                "selecionar a ação segura mais adequada",
                "executar a operação",
                "validar o resultado e relatar o estado",
            ]
        elif task_type == "project":
            steps = [
                "rever o estado do projeto e do repositório",
                "identificar a tarefa ou mudança necessária",
                "executar a alteração com controlo de risco",
                "validar a mudança e resumir impacto",
            ]

        return {
            "task_type": task_type,
            "summary": text[:200],
            "steps": steps,
            "requires_permission": any(
                keyword in text.lower() for keyword in ("apagar", "eliminar", "formatar", "secret", "credential", "token", "instalar")
            ),
        }

    def handle_request(self, request: str, *, metadata: dict | None = None) -> dict:
        clean_request = (request or "").strip()
        if not clean_request:
            return {"ok": False, "error": "empty_request"}

        context = self.context_engine.build_context(clean_request, extra=metadata or {})
        plan = self.create_plan(clean_request)
        selected_agent = self.agent_registry.select_for_task(plan["task_type"])
        task = self.task_engine.create_task(
            title=self._title_from_request(clean_request),
            description=clean_request,
            task_type=plan["task_type"],
            priority=self._priority_from_request(clean_request),
            metadata={
                "plan": plan,
                "context": context.to_dict(),
                "recommended_agent": selected_agent.name if selected_agent else None,
            },
        )
        self.event_bus.publish("task.created", {"task_id": task["id"], "title": task["title"], "status": task["status"]})
        return {
            "ok": True,
            "task_id": task["id"],
            "status": task["status"],
            "plan": plan,
            "context": context.to_dict(),
            "recommended_agent": selected_agent.name if selected_agent else None,
        }

    def _title_from_request(self, request: str) -> str:
        text = re.sub(r"\s+", " ", request).strip()
        if len(text) <= 40:
            return text
        return text[:37].rstrip() + "..."

    def _priority_from_request(self, request: str) -> int:
        text = request.lower()
        if any(keyword in text for keyword in ("urgente", "urgent", "release", "production", "falha", "erro")):
            return 9
        if any(keyword in text for keyword in ("debug", "corrigir", "investigar", "planeia", "agenda")):
            return 7
        return 5

    def get_status(self) -> dict:
        pending = self.task_engine.list_tasks(limit=10)
        return {
            "queue_size": self.task_engine.queue_size(),
            "recent_tasks": pending,
            "model_provider": "ollama-first",
            "orchestrator": "active",
            "agent_registry": [agent.as_dict() for agent in self.agent_registry._agents.values()],
            "events": {
                "task_created": self.event_bus.get_listener_count("task.created"),
            },
        }
