"""Opening a page of Windows Settings, from a fixed list of pages.

THE MODEL NEVER SUPPLIES AN `ms-settings:` URI.

That protocol is a large, undocumented-in-places surface: it reaches account
recovery, sign-in options, device enrolment and a good deal else, and some of
its pages accept parameters. Accepting a URI from the model would make
"open the sound settings" and "open the page that removes a device" the same
tool with a different string.

So the argument is a SECTION NAME from the table below, and the URI is a
constant in this source file that the name maps to. A section that is not in
the table cannot be reached, and the refusal lists what can.
"""
from __future__ import annotations

import logging

from core.pc_control import winapi
from core.pc_control.results import PCControlError

logger = logging.getLogger("nano.pc_control.settings")

#: section name -> (ms-settings URI, human label). Every URI here is a literal
#: in this file. Nothing is ever built from an argument.
SECTIONS: dict[str, tuple[str, str]] = {
    "display":          ("ms-settings:display", "Ecrã"),
    "sound":            ("ms-settings:sound", "Som"),
    "sound_mixer":      ("ms-settings:apps-volume", "Misturador de volume"),
    "network":          ("ms-settings:network", "Rede e Internet"),
    "wifi":             ("ms-settings:network-wifi", "Wi-Fi"),
    "bluetooth":        ("ms-settings:bluetooth", "Bluetooth e dispositivos"),
    "apps":             ("ms-settings:appsfeatures", "Aplicações instaladas"),
    "default_apps":     ("ms-settings:defaultapps", "Aplicações predefinidas"),
    "storage":          ("ms-settings:storagesense", "Armazenamento"),
    "privacy":          ("ms-settings:privacy", "Privacidade e segurança"),
    "windows_update":   ("ms-settings:windowsupdate", "Windows Update"),
    "system":           ("ms-settings:system", "Sistema"),
    "personalization":  ("ms-settings:personalization", "Personalização"),
    "power":            ("ms-settings:powersleep", "Energia e suspensão"),
    "notifications":    ("ms-settings:notifications", "Notificações"),
    "language":         ("ms-settings:regionlanguage", "Idioma e região"),
}


def resolve_section(name) -> tuple[str, str, str]:
    """(section, uri, label) for an allow-listed section, or a refusal."""
    key = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key not in SECTIONS:
        raise PCControlError(
            "invalid_input",
            f"'{name}' não é uma secção das Definições que o Nano abra.",
            allowed=sorted(SECTIONS))
    uri, label = SECTIONS[key]
    return key, uri, label


def open_section(name) -> dict:
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "As Definições do Windows só existem no Windows.")
    section, uri, label = resolve_section(name)
    try:
        winapi.shell_execute(uri)
    except OSError as exc:
        logger.warning("could not open settings section %s: %s", section, exc)
        raise PCControlError("failed",
                             f"O Windows não conseguiu abrir as definições de {label}.") from exc
    return {"section": section, "label": label, "uri": uri}


__all__ = ["SECTIONS", "open_section", "resolve_section"]
