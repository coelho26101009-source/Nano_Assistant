---
name: nano-pc-control
description: Nano Windows PC Control conventions. Use when adding or changing application, window, audio/media, input, clipboard, file/folder, web/URL, display, system, screenshot/OCR, power/session or other Windows-control capabilities.
---

# Nano PC Control

Goal: broad Windows coverage through many narrow typed tools, never one generic executor.

Each tool needs:
- narrow purpose
- typed arguments
- capability identity
- target identity where applicable
- risk classification
- confirmation behavior
- stable structured result
- bounded output
- deterministic failure states

Prefer existing helpers, stdlib, ctypes/Win32/COM, then small mature dependencies only when justified.

Target resolution must return FOUND / NOT_FOUND / AMBIGUOUS. Never execute on AMBIGUOUS.

Files: normalize paths, preserve protected paths, avoid permanent deletion, block script/executable creation as a bypass.

Input: allowlisted keys/hotkeys, bounded text, known target when possible. No unrestricted scan-code or macro tool.

Windows: clamp geometry to real monitor work areas; no hidden process-kill fallback.

Web: validate URL schemes; desktop URL helpers are not a browser agent.

Power/session: explicit confirmation; never real-test shutdown/restart/logoff.

Any side-effecting tool must remain safe across Groq → Ollama fallback and execute at most once per logical turn.
