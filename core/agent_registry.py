"""Central registry for specialized Nano agents."""
from __future__ import annotations

from typing import Any


class SpecializedAgent:
    """Base contract for specialized Nano agents."""

    def __init__(
        self,
        name: str,
        *,
        capabilities: list[str] | None = None,
        tools: list[str] | None = None,
        supported_task_types: list[str] | None = None,
        status: str = "online",
        description: str = "",
    ):
        self.name = name
        self.capabilities = capabilities or []
        self.tools = tools or []
        self.supported_task_types = supported_task_types or []
        self.status = status
        self.description = description

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "capabilities": self.capabilities,
            "tools": self.tools,
            "supported_task_types": self.supported_task_types,
            "status": self.status,
            "description": self.description,
        }


class DesktopAgent(SpecializedAgent):
    def __init__(self):
        super().__init__(
            "DesktopAgent",
            # shell.execute is absent from both lists on purpose: Nano has no
            # shell, so advertising one here described an agent that could not
            # exist. See core/capabilities.py.
            capabilities=["filesystem.read", "filesystem.write", "filesystem.delete", "process.list", "process.start", "process.stop", "system.cpu", "system.memory", "system.gpu", "desktop.screenshot"],
            tools=["filesystem.read_file", "filesystem.write_file", "filesystem.delete_path", "process.list", "process.start", "desktop.screenshot"],
            supported_task_types=["desktop", "general", "project"],
            description="Handles local filesystem and OS tasks with permission-controlled execution. No shell.",
        )


class BrowserAgent(SpecializedAgent):
    def __init__(self):
        super().__init__(
            "BrowserAgent",
            capabilities=["browser.read", "browser.interact", "browser.submit", "browser.search", "browser.screenshot"],
            tools=["browser.search_web", "browser.fetch_url"],
            supported_task_types=["research", "desktop"],
            description="Opens, reads and navigates web pages within the browser safety policy.",
        )


class ResearchAgent(SpecializedAgent):
    def __init__(self):
        super().__init__(
            "ResearchAgent",
            capabilities=["research.search", "research.compare", "research.cite"],
            tools=["browser.search_web", "browser.fetch_url"],
            supported_task_types=["research"],
            description="Searches multiple sources, compares evidence, and synthesizes a report with citations.",
        )


class CodingAgent(SpecializedAgent):
    def __init__(self):
        super().__init__(
            "CodingAgent",
            capabilities=["git.read", "git.write", "project.inspect", "project.test", "project.fix"],
            tools=["project.run_tests", "filesystem.read_file", "filesystem.write_file"],
            supported_task_types=["engineering", "project"],
            description="Inspects repositories, edits code, runs tests, and validates repairs.",
        )


class AgentRegistry:
    def __init__(self):
        self._agents: dict[str, SpecializedAgent] = {
            "DesktopAgent": DesktopAgent(),
            "BrowserAgent": BrowserAgent(),
            "ResearchAgent": ResearchAgent(),
            "CodingAgent": CodingAgent(),
        }

    def register(self, agent: SpecializedAgent) -> None:
        self._agents[agent.name] = agent

    def get_agent(self, name: str) -> SpecializedAgent | None:
        return self._agents.get(name)

    def select_for_task(self, task_type: str) -> SpecializedAgent | None:
        task_type = (task_type or "").lower()
        preference = {
            "research": "ResearchAgent",
            "engineering": "CodingAgent",
            "project": "CodingAgent",
            "desktop": "DesktopAgent",
            "general": "DesktopAgent",
        }
        preferred = preference.get(task_type)
        if preferred and preferred in self._agents:
            return self._agents[preferred]
        for agent in self._agents.values():
            if task_type in {item.lower() for item in agent.supported_task_types}:
                return agent
        return self._agents.get("DesktopAgent")

    def as_dict(self) -> dict[str, Any]:
        return {"agents": [agent.as_dict() for agent in self._agents.values()], "selected": [agent.as_dict() for agent in self._agents.values()]}
