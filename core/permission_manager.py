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
        self._policies: dict[str, dict[str, Any]] = self.policy_engine.get_rules()
        self._pending_requests: dict[str, dict[str, Any]] = {}
        self._audit_log: list[dict[str, Any]] = []
        self._policy_store_path = Path(policy_store_path) if policy_store_path else DATA_DIR / "permission_policies.json"
        self._policy_store_path.parent.mkdir(parents=True, exist_ok=True)
        self._register_default_policies()
        self._load_policy_store()

    def _now_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def _canonical_capability(self, action_name: str) -> str:
        return self.policy_engine.canonical_capability(action_name)

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
            self.register_policy(capability, **config)

    def _load_policy_store(self) -> None:
        if not self._policy_store_path.exists():
            return
        try:
            raw = json.loads(self._policy_store_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(raw, dict):
            for capability, details in raw.items():
                if isinstance(details, dict):
                    policy = {"decision": details.get("decision", "AUTONOMOUS"), "scope": details.get("scope", "current_workspace"), "risk": details.get("risk", RiskLevel.LOW.value), "reason": details.get("reason", "")}
                    self.policy_engine.register_rule(capability, **policy)
                    self._policies[capability] = policy

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

    def register_policy(self, capability: str, *, risk: RiskLevel | str = RiskLevel.LOW, default: str = "allow", requires_confirmation: bool = False, description: str = "", decision: str | None = None, scope: str = "workspace", reason: str | None = None, created_at: str | None = None, expires_at: str | None = None) -> dict:
        normalized = self._canonical_capability(capability)
        stored_decision = str(decision or default)
        decision_value = stored_decision.upper()
        if decision_value in {"ALLOW", "ALLOW_ONCE", "ALLOW_FOR_TASK", "ALLOW_PERSISTENT"}:
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
            "requires_confirmation": bool(requires_confirmation),
            "description": description,
            "reason": reason or description,
            "created_at": created_at or self._now_iso(),
            "expires_at": expires_at,
            "last_used_at": None,
        }
        self.policy_engine.register_rule(normalized, decision=engine_decision, risk=self._policies[normalized]["risk"], scope=scope, reason=reason or description)
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
        if policy_name in self._policies:
            decision = str(self._policies[policy_name].get("decision", self._policies[policy_name].get("default", "allow"))).lower()
            if decision in {"allow", "allow_once", "allow_for_task", "allow_persistent"}:
                return "allow"
            if decision in {"deny", "blocked"}:
                return "deny"
            if decision in {"ask", "approval_required"}:
                return "ask"
        evaluation = self.policy_engine.evaluate(policy_name, target=(args or {}).get("path") or (args or {}).get("target") or (args or {}).get("url") or (args or {}).get("cwd"), arguments=args or {})
        if evaluation.decision == AuthorityDecision.BLOCKED:
            return "deny"
        if evaluation.decision == AuthorityDecision.APPROVAL_REQUIRED:
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

    def evaluate(self, action_name: str, args: dict | None = None) -> PermissionDecision:
        target = (args or {}).get("path") or (args or {}).get("target") or (args or {}).get("url")
        evaluation = self.policy_engine.evaluate(self._canonical_capability(action_name), target=target, arguments=args or {})
        if evaluation.decision == AuthorityDecision.BLOCKED:
            return PermissionDecision(False, True, evaluation.risk, evaluation.reason)
        if evaluation.decision == AuthorityDecision.APPROVAL_REQUIRED:
            return PermissionDecision(False, True, evaluation.risk, evaluation.reason)
        return PermissionDecision(True, False, evaluation.risk, evaluation.reason)

    def request_permission(self, action_name: str, args: dict | None = None, *, task_id: str | None = None, reason: str | None = None, target: str | None = None, agent: str | None = None, tool: str | None = None) -> str:
        request_id = uuid.uuid4().hex
        request = self.policy_engine.permission_request(
            task_id=task_id,
            agent=agent,
            tool=tool or action_name,
            capability=action_name,
            risk=self.classify_action(action_name, args),
            target=target or (args or {}).get("path") or (args or {}).get("target") or (args or {}).get("url") or "-",
            scope=(args or {}).get("scope"),
            reason=reason or "Requested by current task.",
        )
        request["id"] = request_id
        request["action"] = action_name
        request["args"] = args or {}
        request["status"] = "pending"
        self._pending_requests[request_id] = request
        self.log_decision(action_name, "PermissionRequested", risk=request.get("risk"), target=request.get("target"), task_id=task_id, reason=request.get("reason"), event_name="PermissionRequested")
        return request_id

    def resolve_permission(self, request_id: str, decision: str, *, allow_permanent: bool = False) -> dict:
        request = self._pending_requests.get(request_id)
        if not request:
            return {"ok": False, "error": "request_not_found"}
        normalized = str(decision).lower()
        if normalized not in {"allow_once", "allow_for_task", "allow_persistent", "allow", "deny"}:
            return {"ok": False, "error": "invalid_decision"}

        decision_name = normalized.upper()
        if decision_name == "ALLOW_PERSISTENT" and self.classify_action(request["action"], request.get("args")) in {RiskLevel.CRITICAL, RiskLevel.HIGH}:
            return {"ok": False, "error": "critical_requires_explicit_confirmation"}

        request["status"] = "resolved"
        request["decision"] = normalized
        request["allow_permanent"] = bool(allow_permanent and normalized in {"allow", "allow_persistent"})

        if normalized in {"allow", "allow_persistent"}:
            self.register_policy(request["action"], decision="allow", scope="global", reason=request.get("reason") or "User approved permanently.")
        elif normalized == "allow_for_task":
            self.register_policy(request["action"], decision="allow", scope=f"task:{request.get('task_id') or 'unknown'}", reason=request.get("reason") or "User approved for this task.")

        self.log_decision(
            request["action"],
            normalized,
            risk=request.get("risk"),
            target=request.get("target"),
            task_id=request.get("task_id"),
            reason=request.get("reason"),
            event_name="PermissionGranted" if normalized != "deny" else "PermissionDenied",
        )
        self._pending_requests.pop(request_id, None)
        return {"ok": True, "request_id": request_id, "decision": normalized}

    def get_pending_permissions(self) -> list[dict[str, Any]]:
        return list(self._pending_requests.values())

    def ask_for_confirmation(self, action_name: str, args: dict | None = None) -> bool:
        if self.is_emergency_stopped():
            return False
        if self.confirmation_callback is None:
            return False
        decision = self.evaluate(action_name, args)
        if not decision.requires_confirmation:
            return True
        return bool(self.confirmation_callback(action_name, args or {}))

    def is_blocked(self, action_name: str, args: dict | None = None) -> bool:
        decision = self.evaluate(action_name, args)
        return decision.allowed is False or decision.requires_confirmation and not self.ask_for_confirmation(action_name, args)
