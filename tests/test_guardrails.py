from core.guardrails import GuardrailsEngine


def test_powershell_is_never_confirmable_because_it_does_not_exist():
    """Both halves of the old PowerShell rule were wrong, in opposite ways.

    This file used to assert that "Get-Process" needed NO confirmation (an
    allow-list of supposedly harmless cmdlets) and that "Get-ItemProperty"
    needed one. Neither is right for a capability Nano does not have: there is
    nothing to wave through and nothing to ask about. The guardrail layer now
    declines to confirm any of it, whatever the command says.
    """
    g = GuardrailsEngine()
    for command in ("Get-Date", "Get-Process", "Get-ItemProperty HKCU:\\Software",
                    "Write-Output hi | Out-File x.txt", "Remove-Item -Recurse C:\\"):
        assert g.requires_confirmation("system_run_powershell", {"command": command}) is False
        assert g.requires_confirmation("shell.execute", {"command": command}) is False


def test_the_guardrail_layer_no_longer_classifies_command_lines():
    """The allow-list regex is gone, not merely unused.

    Comments are stripped before matching so that this explanation, which
    names the very symbols it forbids, cannot satisfy the check it describes.
    """
    import ast
    import inspect

    from core import guardrails

    source = inspect.getsource(guardrails)
    stripped = ast.unparse(ast.parse(source))
    for token in ("_SAFE_POWERSHELL", "_requires_powershell_confirmation", "invoke-expression"):
        assert token not in stripped, f"{token} is still live code in guardrails"


def test_destructive_tools_always_confirm():
    g = GuardrailsEngine()
    for tool in ("system_delete_file", "system_kill_process", "system_registry_write", "system_format_drive"):
        assert g.requires_confirmation(tool, {})
