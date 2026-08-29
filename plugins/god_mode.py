"""Nano plugin: withdrawn. Kept as the record of what used to be here.

HISTORICALLY "God Mode": arbitrary PowerShell in natural language, plus a
handful of system helpers built on the same `subprocess.run(["powershell",
"-Command", <f-string>])` primitive. Every tool it declared has now been
withdrawn from the model and every function that reached PowerShell has been
deleted from this file. `get_tools()` returns nothing, so `plugin_loader`
simply does not register the plugin.

This file still exists because the reasons matter more than the code did, and
several comments elsewhere point here.

WHY EACH ONE WENT
-----------------

Withdrawn when PC Control V1 landed:

* ``system_run_powershell`` handed the model a full command line. PC Control's
  premise is that the model picks a TOOL and TYPED ARGUMENTS which reach a
  Win32 call as values; a general PowerShell tool defeats every narrow tool
  next to it, because anything refused elsewhere can be spelled out as a
  script here.
* ``system_wifi`` interpolated a model-supplied ``network_name`` straight into
  a `netsh wlan connect name="{network_name}"` string, so a crafted network
  name was command injection into that shell.
* ``system_volume`` was superseded by ``pc_volume_*`` AND was broken: its
  non-nircmd fallback allocated a buffer, copied bytes into it, changed
  nothing, and reported success. A tool that reports a result it did not
  produce is exactly what the real-result contract forbids.

Withdrawn in the PC Control V2 audit:

* ``system_bluetooth`` was a live command-injection sink. Its argument was
  declared as a boolean, but nothing enforced that, and the value was rendered
  into the script with ``str(enable).lower()`` -- so a string argument was
  pasted verbatim into a PowerShell block the model could steer. It is
  replaced by ``pc_settings_open(section="bluetooth")``, which opens the
  Windows Settings page and lets the human make the change.
* ``system_brightness`` built a WMI call as a PowerShell string. It is
  replaced by ``pc_display_set_brightness`` / ``pc_display_change_brightness``,
  which talk to the monitor through the documented Monitor Configuration API,
  validate 0-100, re-read the panel afterwards, and report `unsupported`
  honestly on hardware that has no software brightness.
* ``system_files`` was the widest hole left. It could CREATE a file with any
  extension anywhere the executor's path resolution allowed -- `.bat`, `.ps1`,
  `.vbs` included -- which made "write a file" a way to author an executable
  and turned the careful refusal in ``pc_file_open`` into a formality. Its
  ``move`` was a bare ``Path.rename`` with no protected-path policy of its own
  and no undo. It is replaced by the ``pc_file_*`` / ``pc_folder_*`` family,
  where creation is limited to an allow-list of inert text extensions,
  protected locations are refused, nothing is overwritten, and "delete" means
  the Recycle Bin.

Restoring any of them would mean re-adding both the declaration in
``get_tools()`` and the entry in ``TOOL_HANDLERS`` -- and re-introducing a
PowerShell call site that no longer exists anywhere in this repository's tool
surface.

THAT LAST SENTENCE WAS FALSE UNTIL THE V2 CHECKPOINT AUDIT. Emptying this file
removed the PowerShell tools the model could SEE, but ``core/tool_execution.py``
still registered ``shell.execute``, whose handler ran
``subprocess.run(["cmd", "/c", <model string>])`` behind an approval dialog.
``Brain._run_tool`` dispatches whatever tool name the model emits, so being
unadvertised was never the same as being unreachable. That registration and its
handler are now deleted, and the capability is declared unavailable in
``core/capabilities.py``, blocked in ``PolicyEngine`` and refused before any
confirmation. The sentence above is true now.
"""

from __future__ import annotations


def get_tools() -> list[dict]:
    """No tools. See the module docstring for what was here and why it is not."""
    return []


TOOL_HANDLERS: dict = {}
