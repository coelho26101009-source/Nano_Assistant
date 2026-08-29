---
name: nano-test-gate
description: Final validation gate for a completed Nano development pass. Invoke manually before declaring a feature ready for human review, checkpoint or commit.
disable-model-invocation: true
---

# Nano Final Test Gate

Run this only when implementation is believed complete.

Required gate when present:
1. python -m pytest -q
2. Electron npm test
3. frontend typecheck
4. frontend production build
5. render/layout checks for UI changes
6. focused suites for modified security/provider/PC/voice areas

For real-machine behavior, label evidence as REAL / SIMULATED / MOCKED / NOT TESTED.

Never claim human voice validation unless a human performed it.

Before suggesting commit, inspect git status and flag .env, logs, runtime data, audio, screenshots, keys, certificates or private artifacts.

Do not commit or push unless explicitly requested.

READY requires green required tests, no hidden regression, preserved security invariants, real validation where practical and honest limitations.
