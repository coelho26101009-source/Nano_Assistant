from unittest.mock import AsyncMock

import pytest

from core.brain import Brain


@pytest.mark.asyncio
async def test_tool_confirmation_blocks_execution():
    guard = AsyncMock()
    guard.requires_confirmation.return_value = True
    guard.ask_confirmation.return_value = False
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
