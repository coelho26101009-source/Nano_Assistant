"""Production safety policy engine for Nano.

This module is the central authority for deciding whether a capability may run
without approval, requires explicit confirmation, or is permanently blocked.
It intentionally treats model output as untrusted input and validates targets,
scopes, and contexts before allowing execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


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
    }

    def __init__(self, autonomy_mode: str | AutonomyMode = AutonomyMode.SAFE):
        self.autonomy_mode = AutonomyMode(autonomy_mode.upper() if isinstance(autonomy_mode, str) else autonomy_mode)
        self._rules: dict[str, dict[str, Any]] = {}
        self._blocked_capabilities: set[str] = set()
        self._audit_events: list[dict[str, Any]] = []
        self._emergency_stop = False
        self._register_default_rules()

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
        text = (capability + " " + (target or "") + " " + str(arguments or "")).lower()
        if any(token in text for token in ("delete", "credential", "token", "password", "secret", "payment", "purchase", "checkout", "financial", "system", "reset", "format", "rm -rf", "drop database", "net user")):
            return RiskLevel.CRITICAL
        if any(token in text for token in ("write", "install", "shell", "submit", "push", "move", "rename", "patch", "git", "process", "browser", "mail", "send")):
            return RiskLevel.HIGH
        if any(token in text for token in ("read", "inspect", "search", "test", "status", "list")):
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
            return PolicyEvaluation(normalized, AuthorityDecision.BLOCKED, RiskLevel.CRITICAL, resolved_scope, resolved_target, self._rules.get(normalized, {}).get("reason", "Blocked by policy."), True, False)

        rule = self._rules.get(normalized)
        effective_risk = RiskLevel(risk.lower() if isinstance(risk, str) else risk.value) if risk is not None else self._risk_from_target(normalized, resolved_target, arguments)

        if normalized == "unknown":
            return PolicyEvaluation("unknown", AuthorityDecision.APPROVAL_REQUIRED, RiskLevel.HIGH, resolved_scope, resolved_target, "Unknown capability requires review before execution.", True, False)

        if rule is None:
            if effective_risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                return PolicyEvaluation(normalized, AuthorityDecision.BLOCKED, effective_risk, resolved_scope, resolved_target, "Unknown high-risk capability is blocked by default.", True, False)
            return PolicyEvaluation(normalized, AuthorityDecision.APPROVAL_REQUIRED, effective_risk, resolved_scope, resolved_target, "Unknown capability requires confirmation and explicit validation.", True, False)

        decision = AuthorityDecision(rule["decision"].upper() if isinstance(rule["decision"], str) else rule["decision"])
        policy_scope = str(rule.get("scope") or resolved_scope)
        policy_risk = RiskLevel(rule.get("risk").value if hasattr(rule.get("risk"), "value") else str(rule.get("risk") or effective_risk).lower())

        if normalized in {"filesystem.delete", "process.kill", "git.destructive", "credential.write", "financial.transaction"}:
            if resolved_target is None or str(resolved_target).strip() == "":
                return PolicyEvaluation(normalized, AuthorityDecision.BLOCKED, RiskLevel.CRITICAL, policy_scope, resolved_target, "Target is missing or ambiguous for a destructive or critical action.", True, False)

        if self.autonomy_mode == AutonomyMode.SAFE:
            if decision == AuthorityDecision.AUTONOMOUS and policy_risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                decision = AuthorityDecision.APPROVAL_REQUIRED
            if rule.get("decision") == "AUTONOMOUS" and normalized.startswith("browser") and resolved_target and "login" in str(resolved_target).lower():
                decision = AuthorityDecision.APPROVAL_REQUIRED

        if self.autonomy_mode == AutonomyMode.FULL_SUPERVISION and execution_is_mutating(normalized):
            decision = AuthorityDecision.APPROVAL_REQUIRED

        if decision == AuthorityDecision.AUTONOMOUS and resolved_scope in {"external_service", "system"} and normalized not in {"browser.read", "git.read", "project.inspect"}:
            decision = AuthorityDecision.APPROVAL_REQUIRED

        if decision == AuthorityDecision.AUTONOMOUS and normalized.startswith("filesystem") and resolved_target and any(marker in str(resolved_target).lower() for marker in ("../", "..\\", "/windows", "/program files", "/system32", "appdata\\roaming", "appdata\\local")):
            decision = AuthorityDecision.APPROVAL_REQUIRED

        requires_confirmation = decision == AuthorityDecision.APPROVAL_REQUIRED or (policy_risk in {RiskLevel.HIGH, RiskLevel.CRITICAL})
        return PolicyEvaluation(
            capability=normalized,
            decision=decision,
            risk=max_risk(policy_risk, effective_risk),
            scope=policy_scope,
            target=resolved_target,
            reason=rule.get("reason", "Policy decision applied."),
            requires_confirmation=requires_confirmation,
            is_known=True,
        )

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
    key = (capability or "").lower()
    return any(token in key for token in ("write", "delete", "submit", "send", "install", "kill", "start", "push", "credential", "financial", "transaction", "move", "rename"))


def max_risk(left: RiskLevel, right: RiskLevel) -> RiskLevel:
    ordering = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3, RiskLevel.CRITICAL: 4}
    return left if ordering[left] >= ordering[right] else right
