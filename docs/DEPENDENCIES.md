# Python dependencies

Nano's Python packages are declared by hand and installed from generated locks.
The manifests say what Nano needs; the locks say exactly which files satisfy it;
every install — CI, the packaged Windows runtime, a developer setup — reads the
locks with `pip install --require-hashes`, so the same inputs produce the same
environment on any machine, on any day, and a download that does not match its
recorded sha256 stops the install.

```
requirements*.txt        human-maintained manifests: direct dependencies and their ranges
      │  python scripts/lock_python_deps.py   (uv, pinned — the compiler only)
      ▼
requirements/*.lock      generated: every package, one exact version, every artifact's sha256
      │  python -m pip install --require-hashes ...
      ▼
CI · packaged runtime · developer setup
```

## The files

| Manifest (edit this) | Lock (generated) | Installed by | Platform |
|---|---|---|---|
| `requirements.txt` | `requirements/runtime.lock` | the packaged runtime, a developer setup | universal |
| `requirements-test.txt` | `requirements/test.lock` | CI (Ubuntu and Windows) | universal |
| `requirements-optional.txt` | `requirements/optional.lock` | opt-in, by hand | Windows x86-64, CPython 3.12 |
| `requirements-build.txt` | `requirements/build.lock` | everything above, first | universal |
| `requirements-tools.txt` | `requirements/tools.lock` | whoever regenerates the locks, and CI's drift check | universal |

**Layered.** `runtime.lock` is resolved with `build.lock` as a constraint, and
`test.lock` and `optional.lock` with both. A package that appears in two locks
has the same version in both, so CI tests the gevent, httpx and groq the app
ships, and the optional capabilities install on top of the runtime without
moving any of it.

**Universal, except the optional layer.** A universal lock keeps its environment
markers (`colorama ; sys_platform == 'win32'`) and is valid on every platform.
`optional.lock` cannot be: openwakeword requires `tflite-runtime` on Linux, which
publishes no wheel for Python 3.12, so a universal resolution of that layer has
no solution at all. The optional capabilities are Windows desktop features, and
that is the platform it is locked for.

**Anchored in time.** Resolution only considers files uploaded before
`EXCLUDE_NEWER` in `scripts/lock_python_deps.py`. A release published tomorrow
cannot change a lock, and neither can a wheel uploaded later for a version that
is already pinned. The anchor sits at least seven days in the past, so nothing
younger than a week enters: at the moment the locks were introduced, the
optional voice stack would otherwise have taken `av` 19.0.0, a major release
published that same day.

Each lock's header records the exact command that produced it — compiler
version, anchor, platform, Python version, manifest and constraints — and every
entry says what required it (`# via -r requirements.txt`, `# via eel`,
`# via -c requirements/runtime.lock`).

## Installing

Use Python 3.12. A virtual environment is recommended, and both launchers prefer
`.venv` when it exists:

```bat
python -m venv .venv
.venv\Scripts\python -m pip install --require-hashes -r requirements\build.lock
.venv\Scripts\python -m pip install --require-hashes --no-build-isolation -r requirements\runtime.lock
```

For the test suite, add `requirements\test.lock` the same way (CI installs
`build.lock` and `test.lock` only — no audio or GUI wheels).

Optional capabilities (local speech-to-text, wake word, PDF, browser automation,
images) are never installed by default. All of them at once, on Windows:

```bat
.venv\Scripts\python -m pip install --require-hashes --no-build-isolation -r requirements\optional.lock
```

Or one capability, still pinned and hash-checked — pip takes the versions and
hashes from the lock used as a constraints file, and refuses a file whose hash
does not match:

```bat
.venv\Scripts\python -m pip install --require-hashes --no-build-isolation -c requirements\optional.lock faster-whisper
```

The packaged application's embedded interpreter is built by
`scripts/prepare_windows_runtime.ps1`, which installs `build.lock` and then
`runtime.lock` the same way.

### Why `build.lock` goes first, and `--no-build-isolation`

`eel` and `bottle-websocket` are published only as source distributions, so pip
builds them during the install. By default it builds in a throwaway environment
into which it downloads the newest setuptools — unpinned, and outside
`--require-hashes`: that option never reaches the build-environment download,
which was verified by watching pip do it. Installing the locked pip, setuptools
and wheel first and switching isolation off makes every file pip fetches
hash-checked, and makes the built `eel` the same on every machine. The embedded
runtime could not use isolation anyway: its `._pth` ignores `PYTHONPATH`, which
is how pip hands an isolated environment to a build backend.

The flip side: if a future dependency is source-only *and* needs a different
build backend, the install fails loudly until that backend is added to
`requirements-build.txt` — never silently fetched unverified.

## Changing a dependency

1. Edit the manifest — never a `requirements/*.lock`.
2. Install the pinned compiler:
   `python -m pip install --require-hashes -r requirements/tools.lock`
3. Regenerate: `python scripts/lock_python_deps.py`
4. Review the lock diff and commit each manifest together with its locks.

CI's **Python dependency locks** job regenerates every lock and fails on any
difference: a manifest edited without regenerating, a lock edited by hand, a
pin the anchor no longer allows. `tests/test_python_dependency_locks.py`
catches the common cases offline, and checks that CI, the runtime script and the
launchers' fix hints all install from the locks.

### Every regeneration starts from scratch

The committed locks are never fed back to the compiler, so a lock is a pure
function of its manifests, the anchor, `EXCLUDE_NEWER_PACKAGE` and the uv
version, and the drift check can recompute and compare everything — hashes
included. The usual alternative, regenerating with the old lock as the output
file, keeps the hashes it finds there for every pin it keeps: a hash altered by
hand survives regeneration and the check passes. That was measured before this
design was chosen. It is also why the script has no "upgrade one package" flag.

| To… | Do this, then regenerate |
|---|---|
| refresh everything | move `EXCLUDE_NEWER` forward, keeping it at least seven days in the past |
| take one urgent fix | add `"package": "<a date after the fix>"` to `EXCLUDE_NEWER_PACKAGE` |
| hold one package back | an earlier date in `EXCLUDE_NEWER_PACKAGE`, or an upper bound in its manifest |
| change the compiler | update `requirements-tools.txt` and `UV_VERSION` together, to a uv released before the anchor |

Dependabot updates the manifests and never regenerates locks, so its pull
requests fail the lock job until someone does.

## What is and is not guaranteed

- **Packaged runtime:** the whole chain is pinned. The embeddable Python archive
  and `get-pip.py` are checked against sha256 values in the script, and every
  package, the build backend included, against the locks. `get-pip.py` is served
  from a URL that changes with each pip release; the pinned hash makes that fail
  closed — the build stops and the pin must be reviewed — rather than install
  something else.
- **CI:** every package pip downloads is hash-checked. The runner's Python
  itself comes from `actions/setup-python` and is not covered by the locks.
- **Linux:** `runtime.lock` is universal, but PyAudio publishes Windows wheels
  only, so on Linux pip would build it from source (PortAudio headers
  required). Nano's runtime targets Windows; Linux CI installs `test.lock`,
  which deliberately leaves the audio packages out.
- **Optional layer:** Windows x86-64 with CPython 3.12 only, for the reason
  above. Some in-app setup hints still name bare `pip install <package>`
  commands, which bypass the lock.
