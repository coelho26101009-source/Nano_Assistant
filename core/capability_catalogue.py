"""What Nano can do, grouped the way a person would ask for it.

The Ferramentas page used to show two things: the AI providers, and a list of
loaded plugin files with their raw tool names. Neither answers the question the
page exists to answer -- "what can this thing actually do for me?". A plugin is
an implementation detail, and `pc_window_batch_state` is not a capability, it is
a function signature.

This module reads the LIVE executor registry and regroups it into categories a
user recognises, carrying only what a person can act on: a human sentence, and
whether the action runs straight away, asks first, or does not exist.

EVERYTHING HERE IS DERIVED, NOTHING IS DECLARED TWICE. The description is the
tool's own registered description; the confirmation status is the registry's
`requires_confirmation`, which `register_plugin_tools` computed from
`PermissionManager.is_approval_gated`; the unsupported entries come from
`core.capabilities`. If the permission model changes, this page changes with it,
because there is no second copy of the answer to drift.

It is a CATALOGUE, not a control surface. Nothing here can be toggled, and no
schema is exposed: a tool's `input_schema` never leaves the backend, because a
JSON schema in a consumer UI is noise at best and a nudge to hand-craft a call
at worst.
"""
from __future__ import annotations

from dataclasses import dataclass

from core import capabilities


@dataclass(frozen=True)
class Category:
    """One group in the catalogue.

    ``prefixes`` and ``exact`` are matched against the registered tool name.
    Order matters: the first category that claims a tool owns it, so the
    specific groups are listed before the broad ones.
    """

    id: str
    label: str
    hint: str
    prefixes: tuple[str, ...] = ()
    exact: frozenset[str] = frozenset()


CATEGORIES: tuple[Category, ...] = (
    Category(
        "apps", "Aplicações",
        "Abrir, procurar e alternar entre programas.",
        prefixes=("pc_app_",),
    ),
    Category(
        "windows", "Janelas",
        "Mover, redimensionar, encostar e fechar janelas.",
        prefixes=("pc_window_",),
    ),
    Category(
        "audio", "Áudio e multimédia",
        "Volume do sistema e controlos de reprodução.",
        prefixes=("pc_volume_",),
        exact=frozenset({"pc_media_control"}),
    ),
    Category(
        "files", "Ficheiros e pastas",
        "Procurar, abrir, criar, copiar e reciclar. Nunca apaga em definitivo.",
        prefixes=("pc_file_", "pc_folder_"),
        exact=frozenset({"organize_downloads", "rename_file_smart"}),
    ),
    Category(
        "web", "Web",
        "Abrir endereços e pesquisar na web.",
        prefixes=("pc_web_", "web_"),
    ),
    Category(
        "display", "Ecrã e monitores",
        "Monitores, brilho e captura de ecrã.",
        prefixes=("pc_display_", "pc_screenshot_"),
    ),
    Category(
        "input", "Escrita e área de transferência",
        "Escrever, carregar em teclas e usar a área de transferência, na janela ativa.",
        prefixes=("pc_input_", "pc_pointer_", "pc_clipboard_"),
    ),
    Category(
        "system", "Sistema",
        "Estado da máquina, definições do Windows, energia e sessão.",
        prefixes=("pc_power_", "pc_session_", "monitor_"),
        exact=frozenset({
            "pc_system_info", "pc_network_status", "pc_storage_info",
            "pc_settings_open", "system_stats", "clean_windows_cache",
        }),
    ),
    Category(
        "memory", "Memória",
        "O que o Nano guarda sobre ti entre conversas.",
        exact=frozenset({"remember_fact", "forget_fact"}),
    ),
    Category(
        "schedule", "Agenda e lembretes",
        "Lembretes e eventos de calendário.",
        prefixes=("calendar_", "set_reminder", "cancel_reminder", "list_reminder"),
    ),
    Category(
        "devices", "Dispositivos e notificações",
        "Equipamentos externos e notificações para fora do computador.",
        prefixes=("iot_", "phone_"),
    ),
)

#: Tools that exist but are plumbing rather than a capability a person asks for.
_HIDDEN = frozenset({"filesystem.read_file", "filesystem.write_file",
                     "filesystem.list_directory", "filesystem.create_directory",
                     "filesystem.delete_path", "project.run_tests",
                     "browser.search_web", "browser.fetch_url"})

#: The catch-all, so a newly added plugin tool appears somewhere rather than
#: vanishing from the catalogue.
_OTHER = Category("other", "Outras integrações",
                  "Capacidades adicionais fornecidas por componentes instalados.")


def _category_for(tool_name: str) -> Category:
    for category in CATEGORIES:
        if tool_name in category.exact:
            return category
        if any(tool_name.startswith(prefix) for prefix in category.prefixes):
            return category
    return _OTHER


def _status_for(entry: dict) -> str:
    """available | confirm — what happens when this capability is used.

    Deliberately only two values here. "unsupported" is not a property a
    registered tool can have: an unsupported capability has no registry entry
    at all, and those are added separately from core.capabilities.
    """
    return "confirm" if entry.get("requires_confirmation") else "available"


def build(registry: dict[str, dict]) -> dict:
    """The catalogue, from a live ToolExecutor registry.

    Takes the registry rather than the executor so this stays a pure function
    of observable state, and so the tests can build one deliberately.
    """
    buckets: dict[str, dict] = {}

    def bucket(category: Category) -> dict:
        return buckets.setdefault(category.id, {
            "id": category.id,
            "label": category.label,
            "hint": category.hint,
            "capabilities": [],
        })

    for name in sorted(registry):
        if name in _HIDDEN:
            continue
        entry = registry[name] or {}
        category = _category_for(name)
        bucket(category)["capabilities"].append({
            "tool": name,
            "description": str(entry.get("description") or name),
            "capability": (entry.get("capabilities") or [None])[0],
            "risk": str(entry.get("risk") or "low"),
            "status": _status_for(entry),
        })

    # Absent capabilities are listed too, and listed LAST inside their own
    # group. A catalogue that silently omits them is how "Executa PowerShell"
    # became a confirmation prompt: the honest answer is more useful than no
    # answer, and it belongs next to what Nano CAN do.
    unsupported = [{
        "tool": entry.id,
        "description": entry.explanation,
        "capability": None,
        "risk": "none",
        "status": "unsupported",
        "alternatives": list(entry.alternatives),
    } for entry in capabilities.UNSUPPORTED]

    ordered = [buckets[c.id] for c in CATEGORIES if c.id in buckets]
    if _OTHER.id in buckets:
        ordered.append(buckets[_OTHER.id])

    totals = {
        "available": sum(1 for g in ordered for c in g["capabilities"] if c["status"] == "available"),
        "confirm": sum(1 for g in ordered for c in g["capabilities"] if c["status"] == "confirm"),
        "unsupported": len(unsupported),
    }

    return {
        "categories": ordered,
        "unsupported": unsupported,
        "totals": {**totals, "capabilities": totals["available"] + totals["confirm"]},
    }


__all__ = ["CATEGORIES", "Category", "build"]
