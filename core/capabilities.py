"""What Nano CANNOT do, declared once, in a form both layers can read.

The bug this module exists for: asked "Executa PowerShell e corre Get-Process",
Nano answered "Precisamos de confirmar... Pretende prosseguir?". That sentence
is false in the most expensive way available -- it tells the person that the
only thing standing between them and a shell is their own Yes. Confirmation is
how Nano asks permission for something it CAN do; it can never conjure a
capability that does not exist.

The model had no way to know better. Its system prompt described the approval
pathway and nothing described the ABSENCE of a capability, so "this is a system
action, therefore it needs confirmation" was the only shape available to it.
The `docs/architecture/PC_CONTROL.md` section "Explicitly unsupported in V2"
had the right answer, in prose, where no code could read it.

So the declaration moves here, and three layers read the same table:

  * ``grounding_block`` gives the model the words, and only on turns where the
    request actually asks for something unavailable -- the prompt budget is
    8000 tokens per minute and this must cost nothing on "abre o Spotify";
  * ``for_tool`` refuses a matching tool call BEFORE any confirmation is
    offered, in ``Brain._run_tool`` and again in ``ToolExecutor._authorize``;
  * ``UNSUPPORTED_CAPABILITY_IDS`` is blocked outright in ``PolicyEngine``, so
    the capability cannot be approved, granted or allow-listed either.

ADDING AN ENTRY IS A SECURITY DECISION, NOT A COPY EDIT. An entry here says
"no tool implements this and none may". It must be matched by the absence of a
handler; a declaration that contradicts the registry is worse than no
declaration, because it teaches Nano to deny something it will then do.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


def _normalize(text: str) -> str:
    """Lower-case and strip accents, so "consola" and "consolá" both match.

    Mirrors core.model_selection._normalize deliberately: the two modules read
    the same user sentence and must agree about what it says.
    """
    decomposed = unicodedata.normalize("NFD", str(text or "").lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


@dataclass(frozen=True)
class UnsupportedCapability:
    """One thing Nano does not do, with the words to say so and what to offer.

    ``patterns`` are matched against the ACCENT-STRIPPED, LOWER-CASED user
    message. Each pattern encodes its own co-occurrence -- a bare verb like
    "executa" or a bare noun like "terminal" is not enough, because "executa a
    calculadora" and "fecha o terminal" are ordinary requests the narrow tools
    already handle. Matching only on the pair keeps this from firing on work
    that succeeds today.
    """

    id: str
    #: Short noun phrase, PT-PT, used inside the generated grounding block.
    title: str
    #: PolicyEngine capability ids that must be blocked outright.
    capability_ids: frozenset[str]
    #: Tool names a model might emit for this. Refused before confirmation.
    tool_names: frozenset[str]
    #: Regexes over the normalized user message.
    patterns: tuple[re.Pattern[str], ...]
    #: What Nano tells the person. One paragraph, PT-PT, no hedging.
    explanation: str
    #: Real, registered tools that may genuinely serve the same intent.
    alternatives: tuple[str, ...] = field(default_factory=tuple)


# Execution verbs and shell nouns are kept as fragments so the co-occurrence
# patterns below stay readable and stay in sync with each other.
_RUN_VERB = (
    r"(?:executa|executar|executas|corre|correr|corres|roda|rodar|abre|abrir|"
    r"lanca|lancar|usa|usar|invoca|invocar|run|runs|execute|launch|open|start)"
)
_SHELL_NOUN = (
    r"(?:powershell|power shell|pwsh|cmd\.exe|cmd|command prompt|"
    r"prompt de comandos|linha de comandos|linhas de comando|terminal|"
    r"terminais|consola|console|bash|zsh|wsl|windows terminal|"
    r"interpretador de comandos)"
)
_COMMAND_NOUN = (
    r"(?:comando|comandos|command|commands|script|scripts|cmdlet|cmdlets|"
    r"one-liner|batch|\.ps1|\.bat|\.cmd|\.vbs|\.sh)"
)

SHELL_EXECUTION = UnsupportedCapability(
    id="shell.execution",
    title="execução de comandos, shell, PowerShell, CMD, terminal ou scripts",
    capability_ids=frozenset({"shell.execute"}),
    # Every name a model has been observed to reach for, plus the names the
    # withdrawn tools used to carry. tests/test_pc_control_v2.py already
    # asserts none of these is ADVERTISED; this set makes sure none of them is
    # EXECUTABLE either, which is the half that was missing.
    tool_names=frozenset({
        "shell.execute", "shell_execute", "shell.run", "system_run_powershell",
        "powershell_execute", "run_powershell", "cmd_execute", "cmd_run",
        "terminal_run", "terminal_execute", "run_command", "command_execute",
        "execute_command", "system_exec", "system_execute", "os_system",
        "subprocess_run", "process_exec", "python_exec", "exec_script",
        "script_run", "run_script", "pc_shell_execute", "pc_run_anything",
        "pc_run_command", "pc_terminal", "bash_execute", "voice.command",
    }),
    patterns=(
        # "executa PowerShell", "abre o cmd", "corre um comando no terminal"
        re.compile(r"\b" + _RUN_VERB + r"\b[^.?!;]{0,40}\b" + _SHELL_NOUN + r"\b"),
        # "no PowerShell, corre este comando", "um comando de terminal"
        re.compile(r"\b" + _SHELL_NOUN + r"\b[^.?!;]{0,40}\b" + _COMMAND_NOUN + r"\b"),
        # "executa este comando", "corre o script", "corre um .bat"
        re.compile(r"\b" + _RUN_VERB + r"\b[^.?!;]{0,40}\b" + _COMMAND_NOUN + r"\b"),
        # Naming a cmdlet or a shell idiom is itself the request.
        re.compile(
            r"\b(?:get-process|get-service|get-childitem|get-itemproperty|"
            r"invoke-expression|invoke-webrequest|start-process|stop-process|"
            r"iex\b|net user|netsh|taskkill|reg add|reg delete|"
            r"os\.system|subprocess\.)"
        ),
    ),
    explanation=(
        "O Nano não executa comandos arbitrários — não tem PowerShell, CMD, "
        "terminal, bash, scripts nem qualquer executor genérico. Isto não é uma "
        "permissão por conceder: a capacidade não existe e nenhuma confirmação "
        "a cria. O Nano só age através de ferramentas estreitas, cada uma com "
        "argumentos tipados que nunca formam uma linha de comandos."
    ),
    alternatives=(
        "pc_app_list_running — que aplicações estão abertas agora",
        "pc_system_info — CPU, memória e estado da máquina",
        "pc_network_status — estado da rede",
        "pc_storage_info — espaço em disco",
        "pc_app_launch — abrir uma aplicação pelo nome",
        "pc_settings_open — abrir uma página das Definições do Windows",
        "pc_file_search — procurar ficheiros",
    ),
)

#: The canonical table. Order is the order they appear in a grounding block.
UNSUPPORTED: tuple[UnsupportedCapability, ...] = (SHELL_EXECUTION,)

#: Flattened lookups, built once at import.
UNSUPPORTED_CAPABILITY_IDS: frozenset[str] = frozenset(
    capability for entry in UNSUPPORTED for capability in entry.capability_ids
)
UNSUPPORTED_TOOL_NAMES: frozenset[str] = frozenset(
    name for entry in UNSUPPORTED for name in entry.tool_names
)

_BY_NAME: dict[str, UnsupportedCapability] = {}
for _entry in UNSUPPORTED:
    for _name in (*_entry.tool_names, *_entry.capability_ids):
        _BY_NAME[_name.lower()] = _entry
del _entry, _name


def for_tool(name: str | None) -> UnsupportedCapability | None:
    """The entry a tool name or capability id belongs to, if any.

    This runs before every tool call the model makes, so it is a dict lookup
    and nothing else.
    """
    return _BY_NAME.get(str(name or "").strip().lower())


def detect(text: str | None) -> tuple[UnsupportedCapability, ...]:
    """Which unavailable capabilities this message is asking for."""
    normalized = _normalize(text)
    if not normalized:
        return ()
    return tuple(
        entry for entry in UNSUPPORTED
        if any(pattern.search(normalized) for pattern in entry.patterns)
    )


def describe(entry: UnsupportedCapability) -> str:
    """The explanation plus its alternatives, as one paragraph for a person."""
    if not entry.alternatives:
        return entry.explanation
    return f"{entry.explanation} O que existe e pode servir: {'; '.join(entry.alternatives)}."


def grounding_block(text: str | None) -> str:
    """System-prompt fragment for this turn. Empty when nothing matches.

    Empty is the common case, and that is the point: a turn that does not ask
    for anything unavailable pays no tokens for this.
    """
    matched = detect(text)
    if not matched:
        return ""
    lines = ["\nCapacidades que o Nano NÃO tem (relevantes para este pedido):"]
    for entry in matched:
        lines.append(f"- {entry.title}.")
        lines.append(f"  {entry.explanation}")
        if entry.alternatives:
            lines.append("  Alternativas reais que podes sugerir se servirem a intenção:")
            lines.extend(f"    * {alternative}" for alternative in entry.alternatives)
    lines.append(
        "Diz isto de forma clara e direta. NÃO peças confirmação, NÃO ofereças "
        "autorização e NÃO perguntes se se pretende prosseguir para nada desta "
        "lista: não há nada para autorizar. Não inventes uma ferramenta para o "
        "fazer."
    )
    return "\n".join(lines) + "\n"


def refusal(entry: UnsupportedCapability, *, tool: str | None = None) -> dict:
    """The result a refused call returns, in the shape the chat loop expects.

    Deliberately NOT ``cancelled``: nobody cancelled anything. Claiming the
    user declined a capability that was never offered would be the same class
    of untruth as offering to confirm it.
    """
    return {
        "ok": False,
        "success": False,
        "status": "unsupported_capability",
        "unsupported_capability": entry.id,
        "error": describe(entry),
        "message": describe(entry),
        "metadata": {"tool": tool, "capability": entry.id, "unsupported": True},
    }


__all__ = [
    "SHELL_EXECUTION",
    "UNSUPPORTED",
    "UNSUPPORTED_CAPABILITY_IDS",
    "UNSUPPORTED_TOOL_NAMES",
    "UnsupportedCapability",
    "describe",
    "detect",
    "for_tool",
    "grounding_block",
    "refusal",
]
