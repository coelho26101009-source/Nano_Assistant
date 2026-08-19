import asyncio

import pytest

from core import plugin_loader


def test_invalid_tool_is_rejected(tmp_path):
    plugin = tmp_path / "bad.py"
    plugin.write_text(
        "def get_tools():\n    return [{'type': 'not-function', 'function': {'name': 'x'}}]\n"
        "TOOL_HANDLERS = {'x': lambda args: {'ok': True}}\n",
        encoding="utf-8",
    )
    plugin_loader.load_all_plugins(tmp_path)
    assert plugin_loader.get_all_tools() == []


def test_valid_plugin_is_registered(tmp_path):
    plugin = tmp_path / "good.py"
    plugin.write_text(
        "def get_tools():\n"
        "    return [{'type': 'function', 'function': {'name': 'hello', 'description': 'hello', 'parameters': {'type': 'object'}}}]\n"
        "TOOL_HANDLERS = {'hello': lambda args: {'ok': True}}\n",
        encoding="utf-8",
    )
    plugin_loader.load_all_plugins(tmp_path)
    assert 'hello' in [x['function']['name'] for x in plugin_loader.get_all_tools()]
    # Direct execution is refused: only a bound execution authority may run a
    # plugin handler, so no code path can skip policy and permission checks.
    with pytest.raises(plugin_loader.UnauthorizedExecution):
        plugin_loader.execute_tool('hello', {})

    class _Authority:
        pass

    authority = _Authority()
    plugin_loader.bind_execution_authority(authority)
    assert plugin_loader.execute_tool('hello', {}, authority=authority) == {'ok': True}
