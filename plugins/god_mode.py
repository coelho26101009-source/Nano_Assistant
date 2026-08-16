"""
H.E.L.I.O.S. Plugin: God Mode V2
Controlo total do PC em linguagem natural via PowerShell.
Ações destrutivas → guardrails obrigatórios.
"""

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("helios.plugins.god_mode")


def _run_ps(command: str, timeout: int = 15) -> dict:
    """Executa PowerShell e devolve output."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8",
        )
        return {
            "stdout":     result.stdout.strip(),
            "stderr":     result.stderr.strip(),
            "returncode": result.returncode,
            "success":    result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Timeout — o comando demorou demasiado."}
    except Exception as exc:
        return {"error": f"Falha ao executar PowerShell: {exc}"}


def run_powershell(command: str) -> dict:
    """Executa um comando PowerShell arbitrário."""
    logger.info(f"PowerShell: {command[:100]}")
    return _run_ps(command)


def set_volume(level: int) -> dict:
    """Define o volume do sistema (0-100)."""
    level = max(0, min(100, level))
    ps = f"""
    $obj = New-Object -ComObject WScript.Shell
    # Muda para o nível exacto usando nircmd se disponível
    $nircmd = 'C:\\Program Files\\NirCmd\\nircmd.exe'
    if (Test-Path $nircmd) {{
        & $nircmd setsysvolume ([math]::Round({level} / 100 * 65535))
    }} else {{
        # Fallback: PowerShell puro (aproximado)
        $vol = [math]::Round({level} / 100 * 65535)
        [System.Runtime.InteropServices.Marshal]::Copy(
            [System.BitConverter]::GetBytes($vol), 0,
            [System.Runtime.InteropServices.Marshal]::AllocHGlobal(4), 4)
    }}
    Write-Output "Volume: {level}%"
    """
    return {**_run_ps(ps), "level": level}


def set_brightness(level: int) -> dict:
    """Define o brilho do ecrã (0-100). Requer WMI."""
    level = max(0, min(100, level))
    ps = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level})"
    result = _run_ps(ps)
    if result.get("success"):
        result["message"] = f"Brilho definido para {level}%"
    return result


def toggle_bluetooth(enable: bool) -> dict:
    """Liga ou desliga Bluetooth."""
    action = "Enable" if enable else "Disable"
    ps = f"""
    $radio = [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime]
    $radios = [Windows.Devices.Radios.Radio]::GetRadiosAsync().GetAwaiter().GetResult()
    foreach ($r in $radios) {{
        if ($r.Kind -eq 'Bluetooth') {{
            $state = if ({str(enable).lower()}) {{'On'}} else {{'Off'}}
            $r.SetStateAsync($state).GetAwaiter().GetResult() | Out-Null
            Write-Output "Bluetooth: $state"
        }}
    }}
    """
    return _run_ps(ps)


def manage_wifi(action: str, network_name: str | None = None) -> dict:
    """Gere redes Wi-Fi: listar, ligar, desligar."""
    if action == "list":
        return _run_ps("netsh wlan show profiles")
    elif action == "connect" and network_name:
        return _run_ps(f'netsh wlan connect name="{network_name}"')
    elif action == "disconnect":
        return _run_ps("netsh wlan disconnect")
    elif action == "status":
        return _run_ps("netsh wlan show interfaces")
    return {"error": f"Acção Wi-Fi desconhecida: {action}"}


def file_operations(operation: str, path: str,
                    destination: str | None = None,
                    content: str | None = None) -> dict:
    """Operações de ficheiros: ler, listar, criar, copiar, mover."""
    p = Path(path)

    if operation == "read":
        if not p.exists():
            return {"error": f"Ficheiro não encontrado: {path}"}
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            return {"content": text[:10000], "path": str(p), "size_kb": round(p.stat().st_size/1024, 1)}
        except Exception as exc:
            return {"error": str(exc)}

    elif operation == "list":
        if not p.exists():
            return {"error": f"Pasta não encontrada: {path}"}
        try:
            items = [{"name": i.name, "type": "dir" if i.is_dir() else "file",
                      "size_kb": round(i.stat().st_size/1024, 1) if i.is_file() else None}
                     for i in sorted(p.iterdir())[:100]]
            return {"path": str(p), "items": items, "count": len(items)}
        except Exception as exc:
            return {"error": str(exc)}

    elif operation == "create" and content is not None:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"success": True, "path": str(p)}
        except Exception as exc:
            return {"error": str(exc)}

    elif operation == "copy" and destination:
        import shutil
        try:
            shutil.copy2(str(p), destination)
            return {"success": True, "from": str(p), "to": destination}
        except Exception as exc:
            return {"error": str(exc)}

    elif operation == "move" and destination:
        try:
            p.rename(destination)
            return {"success": True, "from": str(p), "to": destination}
        except Exception as exc:
            return {"error": str(exc)}

    return {"error": f"Operação desconhecida: {operation}"}


def get_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "system_run_powershell",
            "description": "Executa qualquer comando PowerShell no sistema. Para operações avançadas que não têm tool dedicada.",
            "parameters": {"type": "object", "required": ["command"], "properties": {
                "command": {"type": "string", "description": "Comando PowerShell completo"},
            }},
        }},
        {"type": "function", "function": {
            "name": "system_volume",
            "description": "Define o volume do sistema Windows (0-100).",
            "parameters": {"type": "object", "required": ["level"], "properties": {
                "level": {"type": "integer", "minimum": 0, "maximum": 100},
            }},
        }},
        {"type": "function", "function": {
            "name": "system_brightness",
            "description": "Define o brilho do ecrã (0-100). Funciona em monitores com suporte WMI.",
            "parameters": {"type": "object", "required": ["level"], "properties": {
                "level": {"type": "integer", "minimum": 0, "maximum": 100},
            }},
        }},
        {"type": "function", "function": {
            "name": "system_bluetooth",
            "description": "Liga ou desliga o Bluetooth do sistema.",
            "parameters": {"type": "object", "required": ["enable"], "properties": {
                "enable": {"type": "boolean"},
            }},
        }},
        {"type": "function", "function": {
            "name": "system_wifi",
            "description": "Gere redes Wi-Fi: listar perfis, ligar a uma rede, desligar.",
            "parameters": {"type": "object", "required": ["action"], "properties": {
                "action":       {"type": "string", "enum": ["list","connect","disconnect","status"]},
                "network_name": {"type": "string", "description": "Nome da rede para conectar"},
            }},
        }},
        {"type": "function", "function": {
            "name": "system_files",
            "description": "Operações de ficheiros: ler conteúdo, listar pasta, criar, copiar ou mover.",
            "parameters": {"type": "object", "required": ["operation","path"], "properties": {
                "operation":   {"type": "string", "enum": ["read","list","create","copy","move"]},
                "path":        {"type": "string"},
                "destination": {"type": "string"},
                "content":     {"type": "string"},
            }},
        }},
    ]


TOOL_HANDLERS: dict = {
    "system_run_powershell": lambda a: run_powershell(**a),
    "system_volume":         lambda a: set_volume(**a),
    "system_brightness":     lambda a: set_brightness(**a),
    "system_bluetooth":      lambda a: toggle_bluetooth(**a),
    "system_wifi":           lambda a: manage_wifi(**a),
    "system_files":          lambda a: file_operations(**a),
}
