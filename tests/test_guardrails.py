from core.guardrails import GuardrailsEngine


def test_read_only_powershell_is_allowed():
    g = GuardrailsEngine()
    assert not g.requires_confirmation("system_run_powershell", {"command": "Get-Date"})
    assert not g.requires_confirmation("system_run_powershell", {"command": "Get-Process"})


def test_unknown_powershell_requires_confirmation():
    g = GuardrailsEngine()
    assert g.requires_confirmation("system_run_powershell", {"command": "Get-ItemProperty HKCU:\\Software"})
    assert g.requires_confirmation("system_run_powershell", {"command": "Write-Output hi | Out-File x.txt"})


def test_destructive_tools_always_confirm():
    g = GuardrailsEngine()
    for tool in ("system_delete_file", "system_kill_process", "system_registry_write", "system_format_drive"):
        assert g.requires_confirmation(tool, {})
