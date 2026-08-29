"""Production safety policy engine for Nano.

This module is the central authority for deciding whether a capability may run
without approval, requires explicit confirmation, or is permanently blocked.
It intentionally treats model output as untrusted input and validates targets,
scopes, and contexts before allowing execution.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

# Scopes that may ever be reached without an explicit user decision.
AUTONOMOUS_SCOPES = frozenset({"current_workspace"})

# Capabilities that stay autonomous even though their target is an external
# service or the system. Each one is here for a stated reason, and the list is
# short on purpose -- it is the single exception to "a system-scoped
# autonomous decision becomes an approval".
#
#   browser.read / git.read / project.inspect  read-only, no side effect
#   pc.web.open / pc.web.search                hand a URL to the USER's own
#       browser. Nano does not fetch, read or act on the page; the network
#       request belongs to the browser and to the person who asked for it.
#       Without this, "abre o YouTube" would raise an approval dialog every
#       time, because the target string contains a URL.
SCOPE_ESCALATION_EXEMPT = frozenset({
    "browser.read", "git.read", "project.inspect",
    "pc.web.open", "pc.web.search",
})

# PC Control capabilities that WRITE to the filesystem. A protected location is
# refused for these outright, before the user is asked -- the tool handlers
# refuse them too, and asking somebody to authorise an action that was always
# going to be refused teaches them their approval does not mean anything.
#
# Reading and opening are deliberately absent: `pc.file.open` on a document is
# already limited to non-executable types and refuses protected paths in the
# handler, and blocking it here would also block a perfectly ordinary document
# that happens to sit on a second drive.
PC_FILE_MUTATION_CAPABILITIES = frozenset({
    "pc.file.create", "pc.file.copy", "pc.file.move", "pc.file.rename",
    "pc.file.recycle", "pc.folder.create", "pc.folder.recycle",
})

# Capabilities that may never run against an unnamed target. A destructive or
# session-ending action with no target is not under-specified, it is unsafe.
TARGET_REQUIRED_CAPABILITIES = frozenset({
    "filesystem.delete", "process.kill", "git.destructive", "credential.write",
    "financial.transaction",
    "pc.file.recycle", "pc.folder.recycle", "pc.file.move", "pc.file.rename",
    "pc.window.close", "pc.window.batch_close", "pc.input.type",
})


def capability_tokens(text: Any) -> set[str]:
    """Split a capability or argument blob into exact, comparable tokens.

    Token matching exists so that a capability like ``filesystem.read`` is never
    matched by the ``system`` rule simply because "system" is a substring of
    "filesystem". Matching is always on whole tokens.
    """
    return {token for token in _TOKEN_SPLIT.split(str(text).lower()) if token}


# Whole-token matches. Multi-word danger signatures live in _CRITICAL_PHRASES
# and are matched against argument text only, never against capability names.
_CRITICAL_TOKENS = frozenset({
    "delete", "destroy", "wipe", "format", "credential", "credentials",
    "password", "passwd", "secret", "secrets", "payment", "purchase",
    "checkout", "financial", "transaction", "system32", "sam", "shadow",
    # PC Control V2. These matter most for a capability with NO rule:
    # an unknown high-or-critical capability is blocked by default, so a
    # future `pc.file.erase` that nobody remembered to register fails
    # closed instead of falling through to "requires confirmation".
    "recycle", "shutdown", "restart", "logoff", "reboot",
})
_CRITICAL_PHRASES = ("rm -rf", "drop database", "net user", "del /s", "format c:", "reg delete")
_HIGH_TOKENS = frozenset({
    "write", "install", "shell", "powershell", "submit", "push", "move",
    "rename", "patch", "git", "process", "browser", "mail", "send", "exec",
    "execute", "kill", "registry", "system", "start", "reset",
    "close", "clipboard", "type",
})
_MEDIUM_TOKENS = frozenset({"read", "inspect", "search", "test", "status", "list", "get"})
_MUTATING_TOKENS = frozenset({
    "write", "delete", "submit", "send", "install", "kill", "start", "push",
    "credential", "financial", "transaction", "move", "rename",
})


class AutonomyMode(str, Enum):
    SAFE = "SAFE"
    BALANCED = "BALANCED"
    FULL_SUPERVISION = "FULL_SUPERVISION"


class AuthorityDecision(str, Enum):
    AUTONOMOUS = "AUTONOMOUS"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    BLOCKED = "BLOCKED"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class PolicyEvaluation:
    capability: str
    decision: AuthorityDecision
    risk: RiskLevel
    scope: str
    target: str | None
    reason: str
    requires_confirmation: bool = False
    is_known: bool = True


class PolicyEngine:
    """Central safety authority for Nano capabilities and actions."""

    _aliases = {
        "filesystem.read_file": "filesystem.read",
        "filesystem.list_directory": "filesystem.read",
        "filesystem.create_directory": "filesystem.write",
        "filesystem.write_file": "filesystem.write",
        "filesystem.delete_path": "filesystem.delete",
        "shell.execute": "shell.execute",
        "project.run_tests": "project.test",
        "browser.search_web": "browser.read",
        "browser.fetch_url": "browser.read",
        "browser.interact": "browser.interact",
        "browser.submit": "browser.submit",
        "process.start": "process.start",
        "process.kill": "process.kill",
        "git.read": "git.read",
        "git.write": "git.write",
        "git.destructive": "git.destructive",
        "email.send": "external.send",
        "message.send": "external.send",
        "payment": "financial.transaction",
        "purchase": "financial.transaction",
        "credential.update": "credential.write",
        "system_delete_file": "filesystem.delete",
        "system_run_powershell": "shell.execute",
        "system_format_drive": "filesystem.delete",
        "system_registry_write": "credential.write",
        "system_kill_process": "process.kill",
        "system_files": "filesystem.write",
        "system_volume": "system",
        "system_brightness": "system",
        "system_bluetooth": "system",
        "system_wifi": "system",
        "web_interact": "browser.interact",
        "web_navigate_extract": "browser.read",
        "web_search": "browser.read",
        "web_extract_prices": "browser.read",
        "web_screenshot": "browser.read",
        "phone_notify": "external.send",
        "iot_command": "external.send",
        "calendar_add_event": "external.send",
        "calendar_delete_event": "external.send",
        "calendar_import_ics": "external.send",
        "set_reminder": "external.send",
        "cancel_reminder": "external.send",
        "organize_downloads": "filesystem.write",
        "clean_windows_cache": "filesystem.delete",
        "rename_file_smart": "filesystem.write",
        "forget_fact": "credential.write",
        "remember_fact": "filesystem.write",
        "monitor_start": "process.start",
        "self.authorize": "credential.write",
        "voice.command": "shell.execute",
        # --- PC Control V1 -------------------------------------------------
        # Groq function names cannot contain dots, so the TOOLS are
        # pc_app_launch and the CAPABILITIES are pc.app.launch. This table is
        # the only place the two vocabularies meet.
        "pc_app_search": "pc.app.search",
        "pc_app_launch": "pc.app.launch",
        "pc_app_switch": "pc.app.switch",
        "pc_app_list_running": "pc.app.read",
        "pc_window_list": "pc.window.read",
        "pc_window_focus": "pc.window.control",
        "pc_window_minimize": "pc.window.control",
        "pc_window_maximize": "pc.window.control",
        "pc_window_restore": "pc.window.control",
        "pc_window_close": "pc.window.close",
        "pc_window_move": "pc.window.geometry",
        "pc_window_resize": "pc.window.geometry",
        "pc_window_center": "pc.window.geometry",
        "pc_window_snap": "pc.window.geometry",
        "pc_window_move_monitor": "pc.window.geometry",
        "pc_window_set_topmost": "pc.window.geometry",
        "pc_window_batch_state": "pc.window.batch",
        "pc_window_batch_close": "pc.window.batch_close",
        "pc_volume_get": "pc.volume.read",
        "pc_volume_set": "pc.volume.control",
        "pc_volume_change": "pc.volume.control",
        "pc_volume_mute": "pc.volume.control",
        "pc_volume_unmute": "pc.volume.control",
        "pc_media_control": "pc.media.control",
        "pc_display_info": "pc.display.read",
        "pc_display_set_brightness": "pc.display.control",
        "pc_display_change_brightness": "pc.display.control",
        "pc_clipboard_read": "pc.clipboard.read",
        "pc_clipboard_write": "pc.clipboard.write",
        "pc_clipboard_clear": "pc.clipboard.clear",
        "pc_input_type_text": "pc.input.type",
        "pc_input_press_key": "pc.input.key",
        "pc_input_hotkey": "pc.input.hotkey",
        "pc_pointer_scroll": "pc.pointer.scroll",
        "pc_folder_open": "pc.folder.open",
        "pc_file_search": "pc.file.search",
        "pc_file_open": "pc.file.open",
        "pc_folder_create": "pc.folder.create",
        "pc_file_create_text": "pc.file.create",
        "pc_file_copy": "pc.file.copy",
        "pc_file_move": "pc.file.move",
        "pc_file_rename": "pc.file.rename",
        "pc_file_recycle": "pc.file.recycle",
        "pc_folder_recycle": "pc.folder.recycle",
        "pc_web_open_url": "pc.web.open",
        "pc_web_search": "pc.web.search",
        "pc_settings_open": "pc.settings.open",
        "pc_system_info": "pc.system.read",
        "pc_network_status": "pc.network.read",
        "pc_storage_info": "pc.storage.read",
        "pc_session_lock": "pc.session.lock",
        "pc_power_sleep": "pc.power.sleep",
        "pc_power_restart": "pc.power.restart",
        "pc_power_shutdown": "pc.power.shutdown",
        "pc_session_logoff": "pc.session.logoff",
        "pc_screenshot_capture": "pc.screen.capture",
    }

    def __init__(self, autonomy_mode: str | AutonomyMode = AutonomyMode.SAFE):
        self.autonomy_mode = AutonomyMode(autonomy_mode.upper() if isinstance(autonomy_mode, str) else autonomy_mode)
        self._rules: dict[str, dict[str, Any]] = {}
        self._blocked_capabilities: set[str] = set()
        self._audit_events: list[dict[str, Any]] = []
        self._emergency_stop = False
        self._register_default_rules()
        self._block_unsupported_capabilities()

    def _block_unsupported_capabilities(self) -> None:
        """Capabilities Nano does not implement can never be approved either.

        ``shell.execute`` used to sit in the default rules as
        APPROVAL_REQUIRED, which said something untrue about the world: that a
        Yes was all it needed. It is now BLOCKED, so no confirmation, stored
        policy, task grant or allow-list can reach it -- ``evaluate`` returns
        BLOCKED for it before any of those are consulted.

        The alias table above is deliberately left intact: ``voice.command``
        and ``system_run_powershell`` still canonicalise to ``shell.execute``,
        which is what makes them land on this block rather than on the softer
        "unknown capability" path.
        """
        from core.capabilities import UNSUPPORTED, UNSUPPORTED_CAPABILITY_IDS

        reasons = {
            capability: entry.explanation
            for entry in UNSUPPORTED for capability in entry.capability_ids
        }
        for capability in UNSUPPORTED_CAPABILITY_IDS:
            self.block_capability(
                capability,
                reason=reasons.get(capability, "Capability is not implemented in Nano."),
            )

    @property
    def emergency_stop(self) -> bool:
        return self._emergency_stop

    def set_emergency_stop(self, enabled: bool) -> bool:
        self._emergency_stop = bool(enabled)
        return self._emergency_stop

    def canonical_capability(self, capability: str | None) -> str:
        if not capability:
            return "unknown"
        key = str(capability).strip()
        return self._aliases.get(key, key.lower())

    def register_rule(
        self,
        capability: str,
        *,
        decision: str,
        risk: str | RiskLevel,
        scope: str = "current_workspace",
        reason: str = "",
    ) -> dict[str, Any]:
        normalized = self.canonical_capability(capability)
        self._rules[normalized] = {
            "decision": str(decision).upper(),
            "risk": RiskLevel(risk.lower() if isinstance(risk, str) else risk.value),
            "scope": scope,
            "reason": reason,
        }
        return self._rules[normalized]

    def block_capability(self, capability: str, *, reason: str = "Blocked by policy.") -> None:
        self._blocked_capabilities.add(self.canonical_capability(capability))
        self._rules[self.canonical_capability(capability)] = {
            "decision": AuthorityDecision.BLOCKED.value,
            "risk": RiskLevel.CRITICAL,
            "scope": "system",
            "reason": reason,
        }

    def _register_default_rules(self) -> None:
        default_rules = {
            "filesystem.read": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "current_workspace", "Read-only workspace access."),
            "filesystem.write": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.MEDIUM, "current_project", "Creating or editing files requires confirmation outside dangerous contexts."),
            "filesystem.delete": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.CRITICAL, "explicit_target", "Destructive file operations require explicit approval."),
            "shell.execute": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "current_project", "Shell execution is approval-gated due to systemic effects."),
            "process.start": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "system", "Starting processes is approval-gated."),
            "process.kill": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.CRITICAL, "system", "Stopping processes requires explicit approval."),
            "browser.read": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "external_service", "Public reading is safe for read-only browsing."),
            "browser.interact": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.MEDIUM, "external_service", "Interactive browser actions are approval-gated."),
            "browser.submit": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "external_service", "Form submission or authenticated action requires confirmation."),
            "project.test": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_project", "Local tests are safe when they stay in the project scope."),
            "project.inspect": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "current_project", "Repository inspection is read-only."),
            "git.read": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "current_project", "Read-only repository access is safe."),
            "git.write": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.MEDIUM, "current_project", "Repository writes require confirmation."),
            "git.destructive": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.CRITICAL, "current_project", "Destructive git operations require explicit approval."),
            "external.send": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "external_service", "Outgoing external messages require explicit approval."),
            "financial.transaction": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.CRITICAL, "external_service", "Financial actions are critical and require approval."),
            "credential.write": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.CRITICAL, "system", "Credential changes are highly sensitive."),
            "system": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "system", "System-level actions require explicit confirmation."),
            # --- PC Control V1 -------------------------------------------
            # Scope is "current_workspace" for the autonomous entries on
            # purpose: an AUTONOMOUS decision at scope "system" is upgraded to
            # APPROVAL_REQUIRED further down, which would put a confirmation
            # dialog in front of "what is the volume?".
            #
            # Risk must stay LOW/MEDIUM for anything meant to run without a
            # prompt, because requires_confirmation is also true for any rule
            # whose risk is HIGH or CRITICAL. The sensitive three below rely on
            # exactly that.
            "pc.app.search": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "current_workspace", "Listing installed applications is read-only."),
            "pc.window.read": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "current_workspace", "Listing open windows is read-only."),
            "pc.volume.read": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "current_workspace", "Reading the volume is read-only."),
            "pc.system.read": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "current_workspace", "The system snapshot is read-only and carries no identifiers."),
            "pc.file.search": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "current_workspace", "Bounded filename search returns metadata only, never contents."),
            "pc.app.launch": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "Launching a catalogued application is reversible; the model cannot supply a path."),
            "pc.window.control": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "Focus, minimise, maximise and restore are reversible and lose no data."),
            "pc.volume.control": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "Volume changes are bounded and reversible."),
            "pc.folder.open": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "Showing a folder in Explorer reads nothing and changes nothing."),
            "pc.file.open": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "Opening a document; executable and script types are refused by the tool itself."),
            # --- PC Control V2, read and low-impact ----------------------
            "pc.app.read": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "current_workspace", "Listing applications that have a window open is read-only."),
            "pc.display.read": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "current_workspace", "Monitor geometry and brightness support are read-only."),
            "pc.network.read": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "current_workspace", "Connectivity state carries no addresses or network names."),
            "pc.storage.read": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "current_workspace", "Free space per volume is read-only and carries no identifiers."),
            "pc.pointer.scroll": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.LOW, "current_workspace", "Scrolling inside a named window changes nothing and is reversible."),

            # --- PC Control V2, reversible changes ------------------------
            "pc.app.switch": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "Bringing an already-open application forward is reversible."),
            "pc.window.geometry": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "Moving, sizing and snapping a window loses no data and is reversible."),
            "pc.window.batch": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "Minimising or restoring an application's windows is reversible."),
            "pc.media.control": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "Play, pause and skip are reversible transport commands."),
            "pc.display.control": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "Brightness is bounded, reversible, and verified against the monitor."),
            "pc.input.key": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "A single navigation key in a named window; destructive keys resolve elsewhere."),
            "pc.input.hotkey": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "An allow-listed chord sent to a named window."),
            "pc.web.open": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "Handing an http(s) address to the user's own browser; Nano does not read the page."),
            "pc.web.search": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "Opening a search in the user's own browser."),
            "pc.settings.open": (AuthorityDecision.AUTONOMOUS.value, RiskLevel.MEDIUM, "current_workspace", "Opening an allow-listed page of Windows Settings changes nothing."),

            # SENSITIVE. Confirmation is required for every rule below, and the
            # risk level alone would force it even if the decision were relaxed.
            "pc.window.close": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "system", "Closing a window can lose unsaved work and needs explicit confirmation."),
            "pc.window.batch_close": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "system", "Closing every window of an application multiplies the risk of losing unsaved work."),
            "pc.screen.capture": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "system", "A screenshot may contain anything on screen; it always needs explicit consent."),
            "pc.clipboard.read": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "system", "The clipboard may hold a password or a private message; reading it is a privacy event."),
            "pc.clipboard.write": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "system", "Writing replaces whatever the user had copied, and Windows cannot undo that."),
            "pc.clipboard.clear": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "system", "Clearing discards whatever the user had copied."),
            "pc.input.type": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "system", "Typing into an application acts as the user; the exact text is shown before it is sent."),
            "pc.input.key_destructive": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "system", "Delete and Backspace can remove the user's content, so they are confirmed."),
            "pc.folder.create": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "explicit_target", "Creating a folder writes to the user's disk."),
            "pc.file.create": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "explicit_target", "Creating a file writes to the user's disk; only inert text extensions are possible."),
            "pc.file.copy": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "explicit_target", "Copying writes a new file to the user's disk."),
            "pc.file.move": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "explicit_target", "Moving a file changes where the user's data lives."),
            "pc.file.rename": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "explicit_target", "Renaming changes how the user finds their own file."),
            "pc.session.lock": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "system", "Locking ends the user's access to what is on screen right now."),
            "pc.power.sleep": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.HIGH, "system", "Suspending the machine interrupts everything running on it."),

            # CRITICAL. Confirmed, and never coverable by a task-wide grant --
            # see PermissionManager._CRITICAL_CAPABILITIES.
            "pc.file.recycle": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.CRITICAL, "explicit_target", "Sending a file to the Recycle Bin removes it from where the user put it."),
            "pc.folder.recycle": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.CRITICAL, "explicit_target", "Sending a folder to the Recycle Bin removes everything inside it."),
            "pc.power.restart": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.CRITICAL, "system", "Restarting ends every running program and every unsaved document."),
            "pc.power.shutdown": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.CRITICAL, "system", "Shutting down ends every running program and every unsaved document."),
            "pc.session.logoff": (AuthorityDecision.APPROVAL_REQUIRED.value, RiskLevel.CRITICAL, "system", "Signing out closes every application in the session."),
        }
        for capability, (decision, risk, scope, reason) in default_rules.items():
            self.register_rule(capability, decision=decision, risk=risk, scope=scope, reason=reason)

    def _resolve_scope(self, scope: str | None, target: str | None = None) -> str:
        if scope:
            return scope
        if target and str(target).strip():
            target_text = str(target).lower()
            if any(marker in target_text for marker in ("http://", "https://", "www.", ".com")):
                return "external_service"
            if "/" in target_text or "\\" in target_text or target_text.startswith(".") or ":\\" in target_text:
                return "specific_path"
        return "current_workspace"

    def _resolve_target(self, target: Any) -> str | None:
        if target is None:
            return None
        if isinstance(target, (str, Path)):
            value = str(target).strip()
            return value or None
        if isinstance(target, dict):
            for key in ("path", "target", "url", "cwd", "process_name"):
                if key in target and target[key] not in (None, ""):
                    return str(target[key])
        return str(target)

    def _risk_from_target(self, capability: str, target: str | None, arguments: dict[str, Any] | None = None) -> RiskLevel:
        argument_text = f"{target or ''} {arguments or ''}".lower()
        tokens = capability_tokens(capability) | capability_tokens(argument_text)
        if tokens & _CRITICAL_TOKENS or any(phrase in argument_text for phrase in _CRITICAL_PHRASES):
            return RiskLevel.CRITICAL
        if tokens & _HIGH_TOKENS:
            return RiskLevel.HIGH
        if tokens & _MEDIUM_TOKENS:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def evaluate(
        self,
        capability: str | None,
        *,
        target: Any = None,
        scope: str | None = None,
        arguments: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        risk: str | RiskLevel | None = None,
        agent: str | None = None,
        task_id: str | None = None,
    ) -> PolicyEvaluation:
        if self._emergency_stop:
            return PolicyEvaluation(
                capability=self.canonical_capability(capability),
                decision=AuthorityDecision.BLOCKED,
                risk=RiskLevel.CRITICAL,
                scope=self._resolve_scope(scope, target),
                target=self._resolve_target(target),
                reason="Emergency stop engaged. New execution is blocked.",
                requires_confirmation=True,
                is_known=False,
            )

        normalized = self.canonical_capability(capability)
        resolved_target = self._resolve_target(target)
        resolved_scope = self._resolve_scope(scope, resolved_target)

        if normalized in self._blocked_capabilities:
            return self._record(PolicyEvaluation(normalized, AuthorityDecision.BLOCKED, RiskLevel.CRITICAL, resolved_scope, resolved_target, self._rules.get(normalized, {}).get("reason", "Blocked by policy."), True, False), agent=agent, task_id=task_id)

        rule = self._rules.get(normalized)
        effective_risk = RiskLevel(risk.lower() if isinstance(risk, str) else risk.value) if risk is not None else self._risk_from_target(normalized, resolved_target, arguments)

        if normalized == "unknown":
            return self._record(PolicyEvaluation("unknown", AuthorityDecision.APPROVAL_REQUIRED, RiskLevel.HIGH, resolved_scope, resolved_target, "Unknown capability requires review before execution.", True, False), agent=agent, task_id=task_id)

        if rule is None:
            if effective_risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                return self._record(PolicyEvaluation(normalized, AuthorityDecision.BLOCKED, effective_risk, resolved_scope, resolved_target, "Unknown high-risk capability is blocked by default.", True, False), agent=agent, task_id=task_id)
            return self._record(PolicyEvaluation(normalized, AuthorityDecision.APPROVAL_REQUIRED, effective_risk, resolved_scope, resolved_target, "Unknown capability requires confirmation and explicit validation.", True, False), agent=agent, task_id=task_id)

        decision = AuthorityDecision(rule["decision"].upper() if isinstance(rule["decision"], str) else rule["decision"])
        policy_scope = str(rule.get("scope") or resolved_scope)
        policy_risk = RiskLevel(rule.get("risk").value if hasattr(rule.get("risk"), "value") else str(rule.get("risk") or effective_risk).lower())

        if normalized in PC_FILE_MUTATION_CAPABILITIES and context and context.get("protected_target"):
            return self._record(PolicyEvaluation(
                normalized, AuthorityDecision.BLOCKED, RiskLevel.CRITICAL, policy_scope,
                resolved_target,
                "That location is protected: Windows internals, Program Files, "
                "credential stores, browser profiles and Nano's own directories "
                "are never written to or removed.", True, True,
            ), agent=agent, task_id=task_id)

        if normalized in TARGET_REQUIRED_CAPABILITIES:
            if resolved_target is None or str(resolved_target).strip() == "":
                return self._record(PolicyEvaluation(normalized, AuthorityDecision.BLOCKED, RiskLevel.CRITICAL, policy_scope, resolved_target, "Target is missing or ambiguous for a destructive or critical action.", True, False), agent=agent, task_id=task_id)

        if self.autonomy_mode == AutonomyMode.SAFE:
            if decision == AuthorityDecision.AUTONOMOUS and policy_risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                decision = AuthorityDecision.APPROVAL_REQUIRED
            if rule.get("decision") == "AUTONOMOUS" and normalized.startswith("browser") and resolved_target and "login" in str(resolved_target).lower():
                decision = AuthorityDecision.APPROVAL_REQUIRED

        if self.autonomy_mode == AutonomyMode.FULL_SUPERVISION and execution_is_mutating(normalized):
            decision = AuthorityDecision.APPROVAL_REQUIRED

        if decision == AuthorityDecision.AUTONOMOUS and resolved_scope in {"external_service", "system"} and normalized not in SCOPE_ESCALATION_EXEMPT:
            decision = AuthorityDecision.APPROVAL_REQUIRED

        # Filesystem scope enforcement. Only the current workspace is eligible
        # for autonomous access; anything else is an explicit target that needs
        # a human decision, and OS-level mutation is refused outright.
        if normalized.startswith("filesystem"):
            if resolved_scope == "system" and normalized != "filesystem.read":
                return self._record(PolicyEvaluation(
                    normalized, AuthorityDecision.BLOCKED, RiskLevel.CRITICAL, resolved_scope, resolved_target,
                    "Filesystem mutation outside the workspace and data directory is blocked.", True, True,
                ), agent=agent, task_id=task_id)
            if context and context.get("protected_target"):
                decision = AuthorityDecision.APPROVAL_REQUIRED
            if resolved_scope not in AUTONOMOUS_SCOPES:
                decision = AuthorityDecision.APPROVAL_REQUIRED

        requires_confirmation = decision == AuthorityDecision.APPROVAL_REQUIRED or (policy_risk in {RiskLevel.HIGH, RiskLevel.CRITICAL})
        return self._record(PolicyEvaluation(
            capability=normalized,
            decision=decision,
            risk=max_risk(policy_risk, effective_risk),
            scope=policy_scope,
            target=resolved_target,
            reason=rule.get("reason", "Policy decision applied."),
            requires_confirmation=requires_confirmation,
            is_known=True,
        ), agent=agent, task_id=task_id)

    def _record(self, evaluation: PolicyEvaluation, *, agent: str | None = None, task_id: str | None = None) -> PolicyEvaluation:
        """Append a policy evaluation to the audit trail and return it unchanged."""
        import datetime

        self._audit_events.append({
            "event": "PolicyEvaluated",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "capability": evaluation.capability,
            "decision": evaluation.decision.value,
            "risk": evaluation.risk.value,
            "scope": evaluation.scope,
            "target": evaluation.target,
            "reason": evaluation.reason,
            "agent": agent,
            "task_id": task_id,
        })
        del self._audit_events[:-500]
        return evaluation

    def remove_rule(self, capability: str) -> bool:
        """Remove a capability rule from the live rule set.

        ``get_rules`` returns a copy, so callers must use this to actually
        revoke a rule rather than mutating the returned dictionary.

        A capability Nano does not implement is never removable. This is
        reachable from the Permissions page via ``revoke_policy``, and
        discarding the block there would have quietly restored a capability
        that has no implementation -- turning "revoke this permission" into
        "grant a shell". Refusing keeps revocation to what it means.
        """
        normalized = self.canonical_capability(capability)
        from core.capabilities import UNSUPPORTED_CAPABILITY_IDS

        if normalized in UNSUPPORTED_CAPABILITY_IDS:
            return False
        self._blocked_capabilities.discard(normalized)
        return self._rules.pop(normalized, None) is not None

    def permission_request(self, *, task_id: str | None, agent: str | None, tool: str | None, capability: str | None, risk: RiskLevel | str | None, target: Any, scope: str | None, reason: str | None) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "agent": agent,
            "tool": tool,
            "capability": self.canonical_capability(capability),
            "risk": str(risk.value if hasattr(risk, "value") else (risk or RiskLevel.MEDIUM)).upper(),
            "target": self._resolve_target(target),
            "scope": self._resolve_scope(scope, self._resolve_target(target)),
            "reason": reason or "Requested by current task.",
            "requested_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "expires_at": None,
        }

    def get_rules(self) -> dict[str, dict[str, Any]]:
        return dict(self._rules)

    def get_audit_events(self) -> list[dict[str, Any]]:
        return list(self._audit_events)


def execution_is_mutating(capability: str | None) -> bool:
    return bool(capability_tokens(capability or "") & _MUTATING_TOKENS)


def max_risk(left: RiskLevel, right: RiskLevel) -> RiskLevel:
    ordering = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3, RiskLevel.CRITICAL: 4}
    return left if ordering[left] >= ordering[right] else right
