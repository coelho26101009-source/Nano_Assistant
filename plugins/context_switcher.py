"""Nano plugin: withdrawn. Kept as the record of what used to be here.

HISTORICALLY "Context Switcher": one command that activated a work "mode" from
`config/modes/*.yaml` -- closing apps, opening apps, setting the volume and
turning on Focus Assist. It exposed two tools to the model,
``context_activate_mode`` and ``context_list_modes``.

It was withdrawn by the public-release security audit. `get_tools()` returns
nothing, so `plugin_loader` no longer registers it and the model can no longer
reach it.

WHY IT WENT
-----------

The handler contradicted, in four separate places, guarantees Nano makes
elsewhere in this repository and states to the user in Definições -> PC Control:

* ``subprocess.Popen([app_path], shell=True)`` on a path read from YAML. The
  shipped `hacker.yaml` opened ``"wt"`` -- Windows Terminal -- which is
  precisely one of the interpreters `core/pc_control/applications.py` refuses
  to launch by name, alongside ``powershell``, ``pwsh``, ``bash`` and ``wsl``.
  The refusal there was real; this was a way around it.
* ``subprocess.run(["powershell", "-Command", ...])`` twice: once to nudge the
  volume, once to write ``HKCU\\...\\quiethourssettings`` for Focus Assist.
  `core/capabilities.py` declares shell execution UNAVAILABLE and
  `PolicyEngine` blocks the capability outright; `docs/architecture/PC_CONTROL.md`
  lists "arbitrary command/shell/PowerShell execution" and "registry edits"
  under *Explicitly unsupported in V2*. All three statements were false while
  this file shipped.
* ``subprocess.run(["taskkill", "/F", "/IM", app])`` force-killed processes by
  name. "process kill" is on the same unsupported list, and PC Control V2's own
  window-closing path deliberately asks an application to close rather than
  killing it.
* ``_load_mode`` built its path as ``MODES_DIR / f"{mode_name}.yaml"`` from the
  MODEL-SUPPLIED ``mode`` argument, with no containment check. ``mode`` of
  ``"../../../../Windows/win"`` resolves outside ``config/modes`` entirely, so
  the YAML driving all of the above did not have to be one of the three files
  the project ships.

The severity came from the combination. The capability had no entry in
`PolicyEngine._aliases`, so it resolved as an unknown, LOW-risk capability and
produced an ordinary APPROVAL_REQUIRED card reading like a harmless "activate
work mode". One human "yes" to that prompt granted PowerShell, a registry
write, force-kill and a terminal -- and the request could be prompted by
untrusted content, since the trust-boundary rules stop external text from
GRANTING permission but cannot stop it from SUGGESTING a tool call.

HOW TO BRING THE FEATURE BACK SAFELY
------------------------------------

Work modes are a good idea; this implementation was the problem. A safe version
composes the narrow PC Control V2 tools that already exist and already carry
their own confirmation and target binding, rather than shelling out:

* opening applications -> ``pc_app_launch`` (which refuses interpreters)
* opening URLs         -> ``pc_web_open_url``
* volume               -> ``pc_volume_set``
* closing applications -> ``pc_window_batch_close`` (asks; never force-kills)
* Focus Assist         -> ``pc_settings_open(section=...)``, letting the human
                          make the change, exactly as ``system_bluetooth`` was
                          replaced in the V2 audit

That version would need the mode name resolved against a closed allow-list of
files actually inside ``config/modes``, and a confirmation card that names every
application it is about to close. None of that is implemented here: this file
is a tombstone, not a staging area.
"""

from __future__ import annotations


def get_tools() -> list[dict]:
    """No tools. See the module docstring for what was here and why it is not."""
    return []


TOOL_HANDLERS: dict = {}
