# Summary

<!-- What changes, and why. If it fixes an issue, "Fixes #123". -->

## Security impact

<!--
REQUIRED. "None" is a valid answer, but it must be a considered one.

Nano's invariant is:
  MODEL → REQUEST → POLICY → PERMISSION → TOOL EXECUTOR → NARROW TOOL → REAL RESULT
No model output may reach the OS except through a narrow tool with typed
arguments. Say whether this change touches any part of that chain.
-->

- [ ] This change adds **no** new way for model output to reach the operating system.
- [ ] This change adds **no** `subprocess`/`shell=True`/`eval`/`exec` on model-influenced input.
- [ ] This change does **not** weaken PolicyEngine, PermissionManager or ToolExecutor.
- [ ] No secret can reach the renderer, a log, a tool result, the clipboard or an audit entry.

## Permission and capability impact

<!--
If this adds or changes a tool, fill this in. If not, write "no capability change".
-->

- **New or changed capability:** <!-- capability id, or "none" -->
- **Risk level:** <!-- low / medium / high / critical -->
- **Confirmation:** <!-- runs directly / asks first — and why that is the right call -->
- **Target binding:** <!-- what the grant is bound to, so approving one target cannot authorise another -->
- **Failure mode:** <!-- what happens on an ambiguous or unresolvable target — it must fail closed -->

If this makes a previously unavailable capability available, update
`core/capabilities.py` and `docs/architecture/PC_CONTROL.md` in the same PR, so
the declaration and the code cannot disagree.

## Tests

<!-- What you ran, and what you added. Paste the counts. -->

- [ ] `python -m pytest -q`
- [ ] `cd electron && npm test`
- [ ] `cd frontend && npx tsc --noEmit`
- [ ] `cd frontend && npm run build`
- [ ] Render/behaviour harnesses, if the UI changed (`render-check.js`, `settings-drive.js`, `csp-check.js`)
- [ ] I ran the real application, not only the test suite
- [ ] New guards were proven non-vacuous (defect reintroduced, guard observed failing, fix restored)

**Results:**

<!-- e.g. "1175 passed, 1 skipped · 73 electron · tsc clean · build ok" -->

## Screenshots

<!--
For UI changes. Include the minimum window size (940×620) if layout changed.
Crop to Nano's window — do not capture your desktop, your other applications,
your files or anything personal.
-->

## Checklist

- [ ] I read [CONTRIBUTING.md](../blob/main/CONTRIBUTING.md).
- [ ] No secrets, `.env`, API keys, logs, screenshots of personal data, or other private artifacts are included in this PR or its history.
- [ ] I did not weaken or delete a test to make the build pass.
- [ ] User-facing strings are in Portuguese (Portugal); comments are in English.
- [ ] The UI does not display any state it has not actually measured.
- [ ] Documentation is updated where behaviour changed.
