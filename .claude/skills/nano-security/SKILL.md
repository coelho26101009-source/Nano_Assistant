---
name: nano-security
description: Security rules for Nano. Use whenever changing tools, permissions, PolicyEngine, PermissionManager, ToolExecutor, PC Control, subprocess/process launching, files/paths, clipboard, screenshots, power/session actions, secrets, provider failover, or any code that can affect the operating system.
---

# Nano Security

Permanent authority chain:

MODEL → REQUEST → POLICY → PERMISSION → ToolExecutor → NARROW TOOL → REAL RESULT

Rules:
- Never expose unrestricted shell, terminal, PowerShell, CMD, arbitrary process/script execution, or generic OS execution to the model.
- Never use shell=True, os.system, or os.popen for model-controlled operations.
- Tool visibility is not authorization.
- Every OS effect must pass through PolicyEngine, PermissionManager and ToolExecutor.
- Preserve target binding, execution scopes, protected paths, ALLOW_ONCE and ALLOW_FOR_TASK.
- Ambiguous consequential targets fail closed.
- Never claim success before a verified real ToolResult.
- Never silently force-kill a process if graceful close fails.
- Preserve duplicate-effect protection across Groq → Ollama fallback.
- Never expose secrets from DPAPI/config stores through tools, logs, clipboard, typed input or diagnostics.
- Keep model-visible results bounded and structured.

For every new capability, verify: capability, target, risk, confirmation, scope, malformed-input behavior, fallback duplication risk, real-result verification and data exposure.
