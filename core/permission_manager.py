"""Central permission/risk policy for the Nano agent."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from core.app_paths import DATA_DIR
from core.policy_engine import AuthorityDecision, AutonomyMode, PolicyEngine, RiskLevel


@dataclass
class PermissionDecision:
    allowed: bool
    requires_confirmation: bool
    risk: RiskLevel
    reason: str


# Capabilities that always require explicit confirmation per execution.
_APPROVAL_GATED_CAPABILITIES = frozenset({
    "filesystem.delete",
    "filesystem.write",
    "process.kill",
    "process.start",
    "git.destructive",
    "git.write",
    "financial.transaction",
    "credential.write",
    "shell.execute",
    "external.send",
    "browser.submit",
    "browser.interact",
    "system",
})

# Critical capabilities: never allow persistent/autonomous bypass.
_CRITICAL_CAPABILITIES = frozenset({
    "filesystem.delete",
    "process.kill",
    "git.destructive",
    "financial.transaction",
    "credential.write",
})


class PermissionManager:
    """Centralized safety authority for all Nano permissions and policy decisions."""

    def __init__(
        self,
        confirmation_callback: Callable[[str, dict], bool] | None = None,
        policy_store_path: str | Path | None = None,
        autonomy_mode: str | AutonomyMode = AutonomyMode.SAFE,
    ):
        self.confirmation_callback = confirmation_callback
        self.policy_engine = PolicyEngine(autonomy_mode=autonomy_mode)
        self._policies: dict[str, dict[str, Any]] = {}
        self._pending_requests: dict[str, dict[str, Any]] = {}
        self._audit_log: list[dict[str, Any]] = []
        self._once_grants: set[tuple[str, str]] = set()
        self._task_grants: dict[str, set[str]] = {}
        self._policy_store_path = Path(policy_store_path) if policy_store_path else DATA_DIR / "permission_policies.json"
        self._policy_store_path.parent.mkdir(parents=True, exist_ok=True)
        self._register_default_policies()
        self._load_policy_store()

    def _now_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def _canonical_capability(self, action_name: str) -> str:
        return self.policy_engine.canonical_capability(action_name)

    def is_approval_gated(self, capability: str) -> bool:
        return self._canonical_capability(capability) in _APPROVAL_GATED_CAPABILITIES

    def is_critical_capability(self, capability: str) -> bool:
        return self._canonical_capability(capability) in _CRITICAL_CAPABILITIES

    def resolve_tool_capability(self, tool_name: str, args: dict | None = None) -> str:
        """Map a plugin/tool name to the canonical policy capability."""
        args = args or {}
        if tool_name == "system_files":
            operation = str(args.get("operation", "")).lower()
            if operation in {"delete", "remove", "unlink", "rmdir"}:
                return "filesystem.delete"
            if operation in {"read", "list"}:
                return "filesystem.read"
            return "filesystem.write"
        return self._canonical_capability(tool_name)

    def _resolve_target(self, args: dict | None) -> str | None:
        args = args or {}
        for key in ("path", "target", "url", "command", "cwd"):
            value = args.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    def _grant_key(self, capability: str, args: dict | None) -> tuple[str, str]:
        target = self._resolve_target(args) or "*"
        return (self._canonical_capability(capability), target)

    def _has_execution_grant(self, capability: str, args: dict | None = None, *, task_id: str | None = None) -> bool:
        key = self._grant_key(capability, args)
        if key in self._once_grants:
            return True
        return self._has_task_execution_grant(capability, task_id=task_id)

    def _has_task_execution_grant(self, capability: str, *, task_id: str | None = None) -> bool:
        canonical = self._canonical_capability(capability)
        if task_id and canonical in self._task_grants.get(task_id, set()):
            return True
        return False

    def _consume_execution_grant(self, capability: str, args: dict | None = None, *, task_id: str | None = None) -> bool:
        key = self._grant_key(capability, args)
        if key in self._once_grants:
            self._once_grants.discard(key)
            self.log_decision(
                self._canonical_capability(capability),
                "allow_once",
                risk=self.classify_action(capability, args),
                target=self._resolve_target(args),
                task_id=task_id,
                reason="One-shot permission grant consumed.",
                event_name="PermissionConsumed",
            )
            return True
        if self._has_task_execution_grant(capability, task_id=task_id):
            return True
        return False

    def _sanitize_stored_decision(self, capability: str, decision: str) -> str:
        normalized = self._canonical_capability(capability)
        decision_lower = str(decision).lower()
        if decision_lower in {"deny", "blocked"}:
            return decision_lower
        if normalized in _CRITICAL_CAPABILITIES and decision_lower in {"allow", "allow_persistent", "allow_once", "allow_for_task"}:
            return "approval_required"
        if normalized in _APPROVAL_GATED_CAPABILITIES and decision_lower in {"allow", "allow_persistent"}:
            return "approval_required"
        return decision_lower

    def log_decision(self, action_name: str, decision: str, *, risk: str | RiskLevel | None = None, target: str | None = None, task_id: str | None = None, reason: str | None = None, event_name: str | None = None) -> dict:
        payload = {
            "event": event_name or "PermissionDecision",
            "timestamp": self._now_iso(),
            "action": action_name,
            "decision": str(decision).lower(),
            "risk": str(risk.value if isinstance(risk, RiskLevel) else (risk or "unknown")),
            "target": target,
            "task_id": task_id,
            "reason": reason,
        }
        self._audit_log.append(payload)
        return payload

    def get_audit_log(self, limit: int | None = None) -> list[dict[str, Any]]:
        entries = list(self._audit_log)
        if limit is not None:
            return entries[-max(1, int(limit)):]
        return entries

    def set_emergency_stop(self, enabled: bool) -> bool:
        return self.policy_engine.set_emergency_stop(enabled)

    def is_emergency_stopped(self) -> bool:
        return bool(self.policy_engine.emergency_stop)

    def _register_default_policies(self) -> None:
        default_policies = {
            "filesystem.read": {"risk": RiskLevel.LOW, "default": "allow", "requires_confirmation": False, "decision": "AUTONOMOUS", "scope": "current_workspace", "description": "Read local files in the workspace."},
            "filesystem.write": {"risk": RiskLevel.MEDIUM, "default": "allow_task", "requires_confirmation": False, "decision": "APPROVAL_REQUIRED", "scope": "current_project", "description": "Create or edit files in the workspace."},
            "filesystem.delete": {"risk": RiskLevel.CRITICAL, "default": "deny", "requires_confirmation": True, "decision": "APPROVAL_REQUIRED", "scope": "explicit_target", "description": "Delete files or directories."},
            "process.read": {"risk": RiskLevel.MEDIUM, "default": "allow_task", "requires_confirmation": False, "decision": "APPROVAL_REQUIRED", "scope": "system", "description": "Inspect running processes."},
            "process.start": {"risk": RiskLevel.HIGH, "default": "deny", "requires_confirmation": True, "decision": "APPROVAL_REQUIRED", "scope": "system", "description": "Launch a new process."},
            "process.kill": {"risk": RiskLevel.CRITICAL, "default": "deny", "requires_confirmation": True, "decision": "APPROVAL_REQUIRED", "scope": "system", "description": "Stop a running process."},
            "shell.execute": {"risk": RiskLevel.HIGH, "default": "deny", "requires_confirmation": True, "decision": "APPROVAL_REQUIRED", "scope": "current_project", "description": "Execute shell commands."},
            "browser.read": {"risk": RiskLevel.LOW, "default": "allow", "requires_confirmation": False, "decision": "AUTONOMOUS", "scope": "external_service", "description": "Open and read public pages."},
            "browser.interact": {"risk": RiskLevel.MEDIUM, "default": "allow_task", "requires_confirmation": False, "decision": "APPROVAL_REQUIRED", "scope": "external_service", "description": "Interact with page elements."},
            "browser.submit": {"risk": RiskLevel.HIGH, "default": "deny", "requires_confirmation": True, "decision": "APPROVAL_REQUIRED", "scope": "external_service", "description": "Submit forms or send messages."},
            "git.read": {"risk": RiskLevel.LOW, "default": "allow", "requires_confirmation": False, "decision": "AUTONOMOUS", "scope": "current_project", "description": "Read repository status and diff."},
            "git.write": {"risk": RiskLevel.MEDIUM, "default": "allow_task", "requires_confirmation": False, "decision": "APPROVAL_REQUIRED", "scope": "current_project", "description": "Create branches or commit changes."},
            "git.destructive": {"risk": RiskLevel.CRITICAL, "default": "deny", "requires_confirmation": True, "decision": "APPROVAL_REQUIRED", "scope": "current_project", "description": "Reset, delete branches, or push destructive changes."},
            "external.send": {"risk": RiskLevel.HIGH, "default": "deny", "requires_confirmation": True, "decision": "APPROVAL_REQUIRED", "scope": "external_service", "description": "Send messages or external content."},
            "financial.transaction": {"risk": RiskLevel.CRITICAL, "default": "deny", "requires_confirmation": True, "decision": "APPROVAL_REQUIRED", "scope": "external_service", "description": "Payments and transaction actions."},
            "credential.write": {"risk": RiskLevel.CRITICAL, "default": "deny", "requires_confirmation": True, "decision": "APPROVAL_REQUIRED", "scope": "system", "description": "Write credentials or secrets."},
        }
        for capability, config in default_policies.items():
            self.register_policy(capability, **config, persist=False)

    def _load_policy_store(self) -> None:
        if not self._policy_store_path.exists():
            return
        try:
            raw = json.loads(self._policy_store_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(raw, dict):
            return
        for capability, details in raw.items():
            if not isinstance(details, dict):
                continue
            sanitized = self._sanitize_stored_decision(capability, details.get("decision", "AUTONOMOUS"))
            self.register_policy(
                capability,
                decision=sanitized,
                scope=details.get("scope", "current_workspace"),
                risk=details.get("risk", RiskLevel.LOW.value),
                reason=details.get("reason", ""),
                persist=False,
            )

    def _save_policy_store(self) -> None:
        payload = {
            capability: {
                "decision": details.get("decision", details.get("default", "AUTONOMOUS")),
                "scope": details.get("scope", "current_workspace"),
                "risk": details.get("risk", RiskLevel.LOW.value),
                "reason": details.get("reason", ""),
            }
            for capability, details in self._policies.items()
        }
        self._policy_store_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def register_policy(
        self,
        capability: str,
        *,
        risk: RiskLevel | str = RiskLevel.LOW,
        default: str = "allow",
        requires_confirmation: bool = False,
        description: str = "",
        decision: str | None = None,
        scope: str = "workspace",
        reason: str | None = None,
        created_at: str | None = None,
        expires_at: str | None = None,
        persist: bool = True,
    ) -> dict:
        normalized = self._canonical_capability(capability)
        stored_decision = self._sanitize_stored_decision(normalized, str(decision or default))
        decision_value = stored_decision.upper()
        if normalized in _APPROVAL_GATED_CAPABILITIES:
            engine_decision = "APPROVAL_REQUIRED"
        elif decision_value in {"ALLOW", "ALLOW_ONCE", "ALLOW_FOR_TASK", "ALLOW_PERSISTENT", "AUTONOMOUS"}:
            engine_decision = "AUTONOMOUS"
        elif decision_value in {"ASK", "APPROVAL_REQUIRED"}:
            engine_decision = "APPROVAL_REQUIRED"
        elif decision_value in {"DENY", "BLOCKED"}:
            engine_decision = "BLOCKED"
        else:
            engine_decision = "APPROVAL_REQUIRED"
        self._policies[normalized] = {
            "risk": RiskLevel(risk.lower() if isinstance(risk, str) else risk.value),
            "default": default,
            "decision": stored_decision,
            "scope": scope,
            "requires_confirmation": bool(requires_confirmation or normalized in _APPROVAL_GATED_CAPABILITIES),
            "description": description,
            "reason": reason or description,
            "created_at": created_at or self._now_iso(),
            "expires_at": expires_at,
            "last_used_at": None,
        }
        self.policy_engine.register_rule(
            normalized,
            decision=engine_decision,
            risk=self._policies[normalized]["risk"],
            scope=scope,
            reason=reason or description,
        )
        if persist:
            self._save_policy_store()
        return self._policies[normalized]

    def get_policy(self, capability: str) -> dict[str, Any] | None:
        return self._policies.get(self._canonical_capability(capability))

    def list_policies(self) -> list[dict[str, Any]]:
        items = []
        for capability, policy in sorted(self._policies.items()):
            items.append({
                "capability": capability,
                "decision": policy.get("decision", policy.get("default", "AUTONOMOUS")),
                "scope": policy.get("scope", "current_workspace"),
                "risk": str(policy.get("risk", RiskLevel.LOW).value if isinstance(policy.get("risk"), RiskLevel) else policy.get("risk", RiskLevel.LOW.value)),
                "created_at": policy.get("created_at"),
                "last_used_at": policy.get("last_used_at"),
                "expires_at": policy.get("expires_at"),
            })
        return items

    def revoke_policy(self, capability: str) -> bool:
        key = self._canonical_capability(capability)
        if key in self._policies:
            del self._policies[key]
            self.policy_engine.get_rules().pop(key, None)
            self._save_policy_store()
            return True
        return False

    def get_decision_for_action(self, action_name: str, args: dict | None = None) -> str:
        if self.is_emergency_stopped():
            return "deny"
        policy_name = self._canonical_capability(action_name)
        target = self._resolve_target(args)
        evaluation = self.policy_engine.evaluate(policy_name, target=target, arguments=args or {})
        if evaluation.decision == AuthorityDecision.BLOCKED:
            return "deny"
        stored = self._policies.get(policy_name, {})
        stored_decision = str(stored.get("decision", stored.get("default", "ask"))).lower()
        if stored_decision in {"deny", "blocked"}:
            return "deny"
        if evaluation.decision == AuthorityDecision.APPROVAL_REQUIRED or policy_name in _APPROVAL_GATED_CAPABILITIES:
            # ALLOW_ONCE is intentionally not an evaluated authorization: only
            # ask_for_confirmation() may consume it for a real execution.
            if self._has_task_execution_grant(policy_name, task_id=(args or {}).get("_task_id")):
                return "allow"
            return "ask"
        if stored_decision in {"ask", "approval_required"}:
            return "ask"
        return "allow"

    def classify_action(self, action_name: str, args: dict | None = None) -> RiskLevel:
        name = (action_name or "").lower()
        args = args or {}
        if any(token in name for token in ("delete", "format", "kill", "registry", "credential", "secret", "token", "password", "payment", "purchase")):
            return RiskLevel.CRITICAL
        if any(token in name for token in ("write", "move", "copy", "install", "network", "wifi", "bluetooth", "volume", "brightness", "submit", "push", "reset")):
            return RiskLevel.HIGH
        if any(token in name for token in ("git", "browser", "web", "file", "calendar", "reminder", "monitor", "process", "shell")):
            return RiskLevel.MEDIUM
        if action_name and action_name.lower().startswith("read"):
            return RiskLevel.LOW
        if isinstance(args.get("path"), str) and any(marker in str(args.get("path", "")).lower() for marker in ("documents", "desktop", "downloads", "appdata", "program files")):
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def evaluate(self, action_name: str, args: dict | None = None, *, task_id: str | None = None) -> PermissionDecision:
        target = self._resolve_target(args)
        policy_name = self._canonical_capability(action_name)
        evaluation = self.policy_engine.evaluate(policy_name, target=target, arguments=args or {}, task_id=task_id)
        if evaluation.decision == AuthorityDecision.BLOCKED:
            return PermissionDecision(False, True, evaluation.risk, evaluation.reason)
        if evaluation.decision == AuthorityDecision.APPROVAL_REQUIRED or policy_name in _APPROVAL_GATED_CAPABILITIES:
            # A one-shot grant must remain pending until confirmation consumes it.
            # Task grants are reusable, but only for their matching task id.
            if self._has_task_execution_grant(policy_name, task_id=task_id):
                return PermissionDecision(True, False, evaluation.risk, "Explicit task grant present.")
            return PermissionDecision(False, True, evaluation.risk, evaluation.reason)
        return PermissionDecision(True, False, evaluation.risk, evaluation.reason)

    def request_permission(self, action_name: str, args: dict | None = None, *, task_id: str | None = None, reason: str | None = None, target: str | None = None, agent: str | None = None, tool: str | None = None) -> str:
        request_id = uuid.uuid4().hex
        canonical = self.resolve_tool_capability(tool_name=tool or action_name, args=args)
        request = self.policy_engine.permission_request(
            task_id=task_id,
            agent=agent,
            tool=tool or action_name,
            capability=canonical,
            risk=self.classify_action(canonical, args),
            target=target or self._resolve_target(args) or "-",
            scope=(args or {}).get("scope"),
            reason=reason or "Requested by current task.",
        )
        request["id"] = request_id
        request["action"] = canonical
        request["args"] = args or {}
        request["status"] = "pending"
        self._pending_requests[request_id] = request
        self.log_decision(canonical, "PermissionRequested", risk=request.get("risk"), target=request.get("target"), task_id=task_id, reason=request.get("reason"), event_name="PermissionRequested")
        return request_id

    def resolve_permission(self, request_id: str, decision: str, *, allow_permanent: bool = False) -> dict:
        request = self._pending_requests.get(request_id)
        if not request:
            return {"ok": False, "error": "request_not_found"}
        if request.get("status") != "pending":
            return {"ok": False, "error": "request_not_pending"}

        normalized = str(decision).lower()
        if normalized not in {"allow_once", "allow_for_task", "allow_persistent", "allow", "deny"}:
            return {"ok": False, "error": "invalid_decision"}

        capability = self._canonical_capability(request["action"])
        request_args = request.get("args") or {}
        request_task_id = request.get("task_id")
        risk = self.classify_action(capability, request_args)

        if normalized in {"allow_persistent", "allow"}:
            return {"ok": False, "error": "persistent_allow_disabled"}
        if capability in _CRITICAL_CAPABILITIES and normalized == "allow_for_task":
            return {"ok": False, "error": "critical_requires_explicit_confirmation"}

        request["status"] = "resolved"
        request["decision"] = normalized
        request["allow_permanent"] = False

        if normalized == "allow_once":
            self._once_grants.add(self._grant_key(capability, request_args))
        elif normalized == "allow_for_task":
            if not request_task_id:
                return {"ok": False, "error": "task_id_required"}
            self._task_grants.setdefault(str(request_task_id), set()).add(capability)

        self.log_decision(
            capability,
            normalized,
            risk=risk,
            target=request.get("target"),
            task_id=request_task_id,
            reason=request.get("reason"),
            event_name="PermissionGranted" if normalized != "deny" else "PermissionDenied",
        )
        self._pending_requests.pop(request_id, None)
        return {"ok": True, "request_id": request_id, "decision": normalized}

    def get_pending_permissions(self) -> list[dict[str, Any]]:
        return list(self._pending_requests.values())

    def ask_for_confirmation(self, action_name: str, args: dict | None = None, *, task_id: str | None = None) -> bool:
        if self.is_emergency_stopped():
            return False

        args = dict(args or {})
        if task_id:
            args["_task_id"] = task_id

        decision = self.evaluate(action_name, args, task_id=task_id)
        if decision.allowed and not decision.requires_confirmation:
            return True
        if not decision.requires_confirmation:
            return decision.allowed

        if self._consume_execution_grant(action_name, args, task_id=task_id):
            return True

        if self.confirmation_callback is None:
            return False
        return bool(self.confirmation_callback(action_name, args))

    def is_blocked(self, action_name: str, args: dict | None = None, *, task_id: str | None = None) -> bool:
        if self.is_emergency_stopped():
            return True
        decision = self.evaluate(action_name, args, task_id=task_id)
        if decision.requires_confirmation:
            return not self.ask_for_confirmation(action_name, args, task_id=task_id)
        return not decision.allowed
