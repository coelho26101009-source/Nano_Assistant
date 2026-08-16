"""Nano Assistant plugin loader with defensive contract validation."""
from __future__ import annotations

import asyncio
import importlib.util
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("nano.plugin_loader")
_loaded_plugins: dict[str, Any] = {}
_all_tools: list[dict] = []
_all_handlers: dict[str, Any] = {}


def _validate_tool(tool: Any) -> tuple[bool, str]:
    if not isinstance(tool, dict):
        return False, "tool não é um objeto"
    function = tool.get("function")
    if tool.get("type") != "function" or not isinstance(function, dict):
        return False, "tool tem de usar type=function"
    name = function.get("name")
    if not isinstance(name, str) or not name.strip():
        return False, "nome da ferramenta inválido"
    if not isinstance(function.get("description", ""), str):
        return False, "description inválida"
    parameters = function.get("parameters", {"type": "object"})
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        return False, "parameters tem de ser um schema object"
    return True, ""


def _import_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(f"nano.plugins.{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"não foi possível criar loader para {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _register_plugin(name: str, module: Any) -> bool:
    if not callable(getattr(module, "get_tools", None)):
        logger.debug("'%s' ignorado: sem get_tools()", name)
        return False
    tools = module.get_tools()
    if not isinstance(tools, list):
        raise TypeError("get_tools() deve devolver list")
    handlers = getattr(module, "TOOL_HANDLERS", {})
    if not isinstance(handlers, dict):
        raise TypeError("TOOL_HANDLERS deve ser dict")

    valid_tools: list[dict] = []
    for tool in tools:
        ok, reason = _validate_tool(tool)
        if not ok:
            logger.warning("'%s': ferramenta rejeitada: %s", name, reason)
            continue
        tool_name = tool["function"]["name"]
        handler = handlers.get(tool_name)
        if not callable(handler):
            logger.warning("'%s': ferramenta '%s' sem handler callable", name, tool_name)
            continue
        if tool_name in _all_handlers:
            logger.warning("'%s': ferramenta duplicada '%s' rejeitada", name, tool_name)
            continue
        valid_tools.append(tool)
        _all_handlers[tool_name] = handler

    if not valid_tools:
        return False
    _all_tools.extend(valid_tools)
    _loaded_plugins[name] = module
    logger.info("Plugin '%s' carregado: %d ferramentas", name, len(valid_tools))
    return True


def load_all_plugins(plugins_dir: Path | None = None) -> tuple[list[dict], dict]:
    _all_tools.clear()
    _all_handlers.clear()
    _loaded_plugins.clear()
    plugins_dir = plugins_dir or Path(__file__).parent.parent / "plugins"
    if not plugins_dir.exists():
        logger.warning("Pasta de plugins não encontrada: %s", plugins_dir)
        return [], {}
    for plugin_path in sorted(plugins_dir.glob("*.py")):
        if plugin_path.name.startswith("_"):
            continue
        try:
            _register_plugin(plugin_path.stem, _import_module(plugin_path.stem, plugin_path))
        except Exception as exc:
            logger.error("Falha ao carregar plugin '%s': %s", plugin_path.stem, exc, exc_info=True)
    return get_all_tools(), dict(_all_handlers)


async def execute_tool(tool_name: str, arguments: dict) -> dict:
    handler = _all_handlers.get(tool_name)
    if handler is None:
        return {"ok": False, "error": "tool_not_registered", "tool": tool_name}
    try:
        result = handler(arguments)
        if asyncio.iscoroutine(result):
            result = await result
        return result if isinstance(result, dict) else {"ok": True, "result": result}
    except Exception:
        logger.exception("Erro ao executar '%s'", tool_name)
        return {"ok": False, "error": "tool_execution_failed", "tool": tool_name}


def get_all_tools() -> list[dict]:
    return list(_all_tools)


def list_plugins() -> dict[str, list[str]]:
    return {
        name: [t["function"]["name"] for t in (mod.get_tools() if hasattr(mod, "get_tools") else [])]
        for name, mod in _loaded_plugins.items()
    }


def get_plugin_source(plugin_name: str, plugins_dir: Path | None = None) -> dict:
    """Lê o código-fonte de um plugin para exibição na UI."""
    plugins_dir = plugins_dir or Path(__file__).parent.parent / "plugins"
    plugin_path = plugins_dir / f"{plugin_name}.py"
    if not plugin_path.exists():
        return {"ok": False, "error": "Plugin not found"}
    try:
        content = plugin_path.read_text(encoding="utf-8")
        tools = []
        mod = _loaded_plugins.get(plugin_name)
        if mod and hasattr(mod, "get_tools"):
            tools = [t.get("function", {}).get("name", "") for t in (mod.get_tools() or []) if isinstance(t, dict)]
        return {
            "ok": True,
            "name": plugin_name,
            "code": content,
            "tools": tools,
            "filename": f"{plugin_name}.py"
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def reload_plugin(plugin_name: str, plugins_dir: Path | None = None) -> bool:
    plugins_dir = plugins_dir or Path(__file__).parent.parent / "plugins"
    plugin_path = plugins_dir / f"{plugin_name}.py"
    if not plugin_path.exists():
        return False
    old = _loaded_plugins.pop(plugin_name, None)
    if old and hasattr(old, "get_tools"):
        for tool in old.get_tools() or []:
            if isinstance(tool, dict):
                name = (tool.get("function") or {}).get("name")
                if name:
                    _all_handlers.pop(name, None)
                    _all_tools[:] = [t for t in _all_tools if (t.get("function") or {}).get("name") != name]
    try:
        return _register_plugin(plugin_name, _import_module(plugin_name, plugin_path))
    except Exception:
        logger.exception("Falha ao recarregar '%s'", plugin_name)
        return False
