"""Whether this process may load the repository ``.env`` -- one answer, one place.

Two things load ``ROOT/.env`` into ``os.environ``: ``core/main.py`` at import,
and ``scripts/benchmark_providers.py`` at import, so that the benchmark sees
the credentials the product sees. Both ask this module first.

``NANO_SKIP_DOTENV=1`` turns loading off. It is how an isolated process keeps
a developer's real credentials out of it: Electron sets it for a backend given
an explicit profile, the packaged smoke test sets it, and tests/conftest.py
sets it before pytest imports a single test module. A frozen build never reads
a ``.env`` at all.

WHY IT IS A MODULE AND NOT A CONDITION COPIED INTO EACH CALLER
--------------------------------------------------------------
The benchmark used to call ``load_dotenv`` unconditionally. A test imports it,
so every bare ``pytest`` run loaded the developer's real API keys into the test
process while collecting -- before any test ran, and straight past the switch
that core.main honoured. Timing and routing tests then ran against whatever
that machine's ``.env`` happened to configure, and CI, which has no ``.env``,
ran a different suite. A caller that asks here cannot drift from that switch.

Stdlib only, and deliberately free of ``core.app_paths``: asking must not
compute ROOT or DATA_DIR before the caller has decided to load anything.
"""
from __future__ import annotations

import os
import sys

#: Set to "1" to keep this process from loading the repository ``.env``.
SKIP_VARIABLE = "NANO_SKIP_DOTENV"


def dotenv_allowed() -> bool:
    """False in a frozen build, and whenever ``NANO_SKIP_DOTENV`` is ``"1"``."""
    return not getattr(sys, "frozen", False) and os.getenv(SKIP_VARIABLE) != "1"
