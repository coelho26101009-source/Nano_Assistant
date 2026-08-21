#!/usr/bin/env bash
# ===========================================================================
#  NANO ASSISTANT - Linux launcher (mirrors NANO.bat).
#
#  To start Nano: ./NANO.sh
#
#  This launcher is self-contained on purpose: no chain of nested scripts.
# ===========================================================================
set -u
cd "$(dirname "$0")"

echo
echo "  =========================================="
echo "    NANO ASSISTANT"
echo "  =========================================="
echo

fail() {
    echo
    echo "  Startup failed."
    echo "  Reason:    $1"
    echo "  Component: $2"
    echo "  Fix:       $3"
    echo
    exit 1
}

# --- 1. Python runtime ------------------------------------------------------
# A local .venv wins so dependencies always come from one known interpreter.
NANO_PY="python3"
if [ -x ".venv/bin/python" ]; then
    NANO_PY=".venv/bin/python"
fi

if ! "$NANO_PY" --version >/dev/null 2>&1; then
    echo "  [NANO] Python ........ FAILED"
    fail "Python was not found on this system." \
         "Python runtime" \
         "Install Python 3.12 or newer and make sure \"python3\" works in a terminal."
fi

PYVER=$("$NANO_PY" -c "import sys;print(sys.version.split()[0])" 2>/dev/null)
PYEXE=$("$NANO_PY" -c "import sys;print(sys.executable)" 2>/dev/null)
echo "  [NANO] Python ........ OK (${PYVER})"
echo "         ${PYEXE}"

# --- 2. Core dependencies ----------------------------------------------------
if ! "$NANO_PY" -c "import eel, httpx, yaml, psutil" >/dev/null 2>&1; then
    echo "  [NANO] Dependencies .. FAILED"
    fail "Required Python packages are missing in this interpreter." \
         "Dependencies" \
         "\"$NANO_PY\" -m pip install -r requirements.txt"
fi
echo "  [NANO] Dependencies .. OK"

# --- 3. Frontend (build only when missing) -----------------------------------
if [ ! -f "frontend/out/index.html" ]; then
    echo "  [NANO] Frontend ...... BUILDING (first run, this takes a minute)"
    (cd frontend && npm run build)
    if [ ! -f "frontend/out/index.html" ]; then
        echo "  [NANO] Frontend ...... FAILED"
        fail "The frontend build did not produce frontend/out/index.html" \
             "Frontend" \
             "cd frontend && npm install && npm run build"
    fi
fi
echo "  [NANO] Frontend ...... OK"

# --- 4. Backend ---------------------------------------------------------------
# main.py starts Ollama if needed, starts the wake listener, and opens the
# UI exactly once. This launcher never opens a browser itself.
echo "  [NANO] Backend ....... STARTING"
echo
echo "  ------------------------------------------"
echo "   Keep this terminal open while using Nano."
echo "   Close it (or press Ctrl+C) to stop Nano."
echo "  ------------------------------------------"
echo

"$NANO_PY" core/main.py --mode default
RC=$?

echo
if [ "$RC" -eq 0 ]; then
    echo "  [NANO] Nano closed normally."
    exit 0
fi

echo "  [NANO] Backend ....... EXITED WITH CODE ${RC}"
echo
echo "  Nano stopped unexpectedly."
echo "  Component: Backend (core/main.py)"
echo "  The error should be visible in the output above."
echo "  A full log is also written to: nano.log"
echo
exit 1
