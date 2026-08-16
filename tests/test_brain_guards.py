from unittest.mock import AsyncMock, MagicMock

import pytest

from core.brain import Brain
from core.guardrails import GuardrailsEngine


def test_mutating_tools_require_confirmation():
    guard = GuardrailsEngine()
    assert guard.requires_confirmation("system_files", {"operation": "read"}) is False
    assert guard.requires_confirmation("system_files", {"operation": "move"}) is True
    assert guard.requires_confirmation("iot_command", {"device": "lamp", "action": "on"}) is True
    assert guard.requires_confirmation("set_reminder", {"text": "test", "when": "10 minutos"}) is True


@pytest.mark.asyncio
async def test_tool_confirmation_blocks_execution():
    guard = MagicMock()
    guard.requires_confirmation = MagicMock(return_value=True)
    guard.ask_confirmation = AsyncMock(return_value=False)
    memory = AsyncMock()
    brain = Brain("test-key", guard, memory, {"ollama_enabled": False})

    result = await brain._run_tool(
        type("ToolCall", (), {
            "function": type("Function", (), {
                "name": "system_run_powershell",
                "arguments": '{"command":"Get-Process"}',
            })()
        })()
    )

    assert result["cancelled"] is True
