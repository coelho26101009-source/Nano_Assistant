"""Nano must say "I cannot", never "confirm and I will", for what it lacks.

The human retest of PC Control V2 asked, in Portuguese, "Executa PowerShell e
corre Get-Process." Nano answered with a confirmation prompt -- "Precisamos de
confirmar... Pretende prosseguir?" -- which told the person their Yes was the
only thing missing. It was not. Two separate things were wrong:

* the model had no grounding that said shell execution does not exist, so the
  approval pathway was the only shape it had for a systemic request; and
* a `shell.execute` tool really was registered in ToolExecutor, running
  `cmd /c <model string>` behind an approval dialog, reachable by name from
  Brain._run_tool even though it was never advertised.

These tests hold both halves down, and the last section holds down the thing
that must NOT change: real confirmation-gated tools still ask.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from core import capabilities
from core.brain import Brain
from core.guardrails import GuardrailsEngine
from core.memory import MemoryEngine
from core.permission_manager import PermissionManager
from core.plugin_loader import get_all_tools, load_all_plugins
from core.policy_engine import AuthorityDecision, PolicyEngine
from core.tool_execution import ToolExecutor

# The phrasing from the human retest, plus the neighbouring ways to ask for it.
SHELL_REQUESTS = [
    "Executa PowerShell e corre Get-Process.",
    "executa powershell e corre get-process",
    "abre o cmd e corre dir",
    "corre um comando no terminal",
    "podes correr um comando no cmd?",
    "corre este script .ps1",
    "abre a linha de comandos",
    "usa o bash para listar os ficheiros",
    "faz taskkill no chrome",
    "run a command in the terminal",
]

# Requests the human validated as working. None of them may be caught by the
# unavailable-capability detector: a false positive here turns a working
# feature into a refusal, which is a worse regression than the bug being fixed.
VALIDATED_V2_REQUESTS = [
    "abre o spotify",
    "abre a calculadora",
    "mete a calculadora a direita",
    "centra a janela do bloco de notas",
    "encosta o chrome a esquerda",
    "qual e o volume",
    "aumenta o volume",
    "poe o volume a 30",
    "abre as definicoes de som",
    "abre o google.com",
    "tira uma screenshot",
    "captura o ecra",
    "fecha o discord",
    "fecha esta janela",
    "fecha o terminal",
    "que aplicacoes estao abertas",
    "quanta memoria estou a usar",
    "procura o ficheiro relatorio.pdf",
    "escreve ola no bloco de notas",
    "bloqueia a sessao",
    "cria uma pasta chamada testes",
    "minimiza tudo",
]


def _tool_call(name: str, arguments: str = "{}"):
    return type("ToolCall", (), {
        "function": type("Function", (), {"name": name, "arguments": arguments})()
    })()


def _pc_executor(*, approve: bool):
    """An executor with the PC Control tools actually registered.

    register_plugin_tools() copies from the plugin registry, so the plugins
    have to be loaded first -- without that every pc_* name is unknown_tool and
    a confirmation test passes for the wrong reason.
    """
    load_all_plugins()
    manager = PermissionManager(confirmation_callback=lambda *_a, **_k: approve)
    executor = ToolExecutor(permission_manager=manager)
    executor.register_plugin_tools()
    return executor


def _brain(guardrails=None, *, approve_everything=True):
    manager = PermissionManager(
        confirmation_callback=(lambda *_a, **_k: True) if approve_everything else (lambda *_a, **_k: False)
    )
    return Brain("", guardrails or GuardrailsEngine(), MemoryEngine(),
                 {"ollama_enabled": False}, permission_manager=manager)


# ------------------------------------------------------- the declaration itself

def test_detection_covers_the_reported_phrasing():
    for request in SHELL_REQUESTS:
        assert capabilities.detect(request), f"not detected as unavailable: {request!r}"


def test_detection_does_not_fire_on_validated_v2_requests():
    for request in VALIDATED_V2_REQUESTS:
        assert not capabilities.detect(request), f"false positive on a working flow: {request!r}"


def test_the_declaration_never_contradicts_the_tool_registry():
    """An entry here asserts "no tool implements this". Prove it.

    A declaration that contradicts the registry is worse than none at all: it
    teaches Nano to deny something it will then go on and do.
    """
    load_all_plugins()
    advertised = set()
    for tool in get_all_tools():
        function = tool.get("function") or {}
        advertised.add(str(function.get("name") or tool.get("name") or ""))

    registered = set(ToolExecutor(permission_manager=PermissionManager()).registry)

    for name in capabilities.UNSUPPORTED_TOOL_NAMES:
        assert name not in advertised, f"{name} is declared unavailable but advertised to the model"
        assert name not in registered, f"{name} is declared unavailable but registered as executable"


def test_every_unsupported_capability_is_blocked_by_the_policy_engine():
    engine = PolicyEngine()
    for capability in capabilities.UNSUPPORTED_CAPABILITY_IDS:
        evaluation = engine.evaluate(capability, target="anything")
        assert evaluation.decision is AuthorityDecision.BLOCKED, capability


def test_a_blocked_capability_cannot_be_unblocked_by_revoking_its_rule():
    engine = PolicyEngine()
    for capability in capabilities.UNSUPPORTED_CAPABILITY_IDS:
        assert engine.remove_rule(capability) is False
        assert engine.evaluate(capability, target="anything").decision is AuthorityDecision.BLOCKED


# --------------------------------------------------------- the model's grounding

@pytest.mark.asyncio
async def test_system_prompt_states_the_capability_is_absent_and_forbids_confirming():
    brain = _brain()
    prompt = await brain._build_system_prompt(
        "Executa PowerShell e corre Get-Process.", with_tools=True
    )

    assert "não executa comandos arbitrários" in prompt
    assert "a capacidade não existe" in prompt
    assert "NÃO peças confirmação" in prompt
    # And it offers somewhere real to go instead of just refusing.
    assert "pc_app_list_running" in prompt


@pytest.mark.asyncio
async def test_the_grounding_costs_nothing_on_an_ordinary_request():
    """Prompt tokens are the scarce resource; this block must not be constant.

    The Groq tier allows 8000 tokens per minute, and the PC tool schemas
    already dominate that budget.
    """
    brain = _brain()
    prompt = await brain._build_system_prompt("abre o spotify", with_tools=True)
    assert "não executa comandos arbitrários" not in prompt
    assert capabilities.grounding_block("abre o spotify") == ""


# ------------------------------------------------------ no confirmation, no run

@pytest.mark.asyncio
async def test_a_shell_tool_call_is_refused_without_any_confirmation():
    """The whole point: no dialog, no execution, and an honest status.

    The permission manager approves everything here, so if a confirmation were
    reachable the call would go through.
    """
    brain = _brain(approve_everything=True)
    for name in ("shell.execute", "system_run_powershell", "run_command", "pc_shell_execute"):
        result = await brain._run_tool(_tool_call(name, '{"command":"Get-Process"}'))
        assert result.get("ok") is False, name
        assert result.get("status") == "unsupported_capability", name
        assert result.get("cancelled") is None, f"{name} claimed the user cancelled"
        assert "não executa comandos arbitrários" in result.get("message", ""), name


@pytest.mark.asyncio
async def test_the_refusal_reaching_the_model_never_suggests_confirming():
    """What the model reads back must not contain an approval affordance.

    If the tool result hints at confirmation, the model will offer one in
    prose, and the user is back to "Pretende prosseguir?" with no tool behind
    it.
    """
    brain = _brain()
    result = await brain._run_tool(_tool_call("shell.execute", '{"command":"Get-Process"}'))
    serialised = Brain._tool_result_for_model("shell.execute", result).lower()

    assert "unsupported_capability" in serialised
    for affordance in ("confirmas", "pretende prosseguir", "autoriza", "aprovar", "permitir"):
        assert affordance not in serialised, f"the refusal offers an approval: {affordance!r}"


def test_the_executor_refuses_before_it_would_ask(monkeypatch):
    """Ordering, asserted directly rather than inferred from the outcome."""
    asked: list[tuple] = []
    manager = PermissionManager(confirmation_callback=lambda *a, **k: (asked.append(a), True)[1])
    executor = ToolExecutor(permission_manager=manager)

    result = executor.execute_tool("shell.execute", {"command": "whoami"})

    assert result["status"] == "unsupported_capability"
    assert asked == [], f"a confirmation was requested for an absent capability: {asked}"


def test_no_shell_command_line_is_built_anywhere_in_the_executor():
    """The generic-executor invariant, read from the AST rather than the text.

    Comments and docstrings are stripped first, so the explanations in
    tool_execution.py that quote `["cmd", "/c", command]` while describing the
    removal cannot satisfy the check that forbids it.
    """
    from core import tool_execution

    stripped = ast.unparse(ast.parse(inspect.getsource(tool_execution)))
    for forbidden in ('"cmd"', "'cmd'", '"bash"', "'bash'", "shell=True",
                      "os.system", "os.popen", "_execute_shell"):
        assert forbidden not in stripped, f"{forbidden} is live code in tool_execution"


# ------------------------------------- what must NOT change: real confirmations

def test_screenshot_still_asks_for_confirmation():
    """A capability that DOES exist keeps its approval flow, unchanged.

    Both of these were confirmed by hand in the V2 retest, prompt and all.
    """
    executor = _pc_executor(approve=False)

    auth = executor._authorize("pc_screenshot_capture", {}, None)
    assert auth["ok"] is True, "screenshot is no longer a registered tool"
    assert auth["capability"] == "pc.screen.capture"
    assert auth["needs_confirmation"] is True, "screenshot stopped asking for confirmation"

    denied = executor.execute_tool("pc_screenshot_capture", {})
    assert denied["success"] is False
    assert denied["status"] == "permission_denied", "declining the prompt no longer blocks the capture"


def test_window_close_still_asks_for_confirmation():
    executor = _pc_executor(approve=False)

    auth = executor._authorize("pc_window_close", {"window_id": 12345}, None)
    assert auth["ok"] is True, "window close is no longer a registered tool"
    assert auth["capability"] == "pc.window.close"
    assert auth["needs_confirmation"] is True, "window close stopped asking for confirmation"

    denied = executor.execute_tool("pc_window_close", {"window_id": 12345})
    assert denied["success"] is False
    assert denied["status"] == "permission_denied"


def test_the_confirmation_shortcut_is_scoped_to_absent_capabilities_only():
    """Guardrails still confirm everything they used to, minus the dead rule."""
    guardrails = GuardrailsEngine()
    assert guardrails.requires_confirmation("system_files", {"operation": "move"}) is True
    assert guardrails.requires_confirmation("iot_command", {"device": "lamp"}) is True
    assert guardrails.requires_confirmation("system_delete_file", {}) is True
    assert guardrails.requires_confirmation("shell.execute", {"command": "dir"}) is False
