<#
    Builds the relocatable embedded Python runtime that ships inside Nano.

    ONE SCRIPT, TWO CALLERS. `.github/workflows/build-windows.yml` used to carry
    its own inlined copy of this logic, and the two had drifted: CI appended the
    build machine's absolute workspace path to the interpreter's `._pth`, so the
    runtime it produced was not the runtime a developer produced, and the copy
    inside the installer carried a directory that exists on no user's computer.
    Both callers now run this file, so the runtime is the same either way.

    WHY `._pth` MATTERS. An embedded CPython ignores PYTHONPATH entirely and
    takes its `sys.path` from the `._pth` file beside `python.exe`. It is
    written here deterministically -- the same lines on every machine, with no
    absolute path from the build host. Nano's application root is deliberately
    NOT listed: `core/main.py` inserts `NANO_APP_ROOT` into `sys.path` itself,
    which is what keeps the runtime relocatable from a checkout to
    `resources/app` inside an installation.
#>
[CmdletBinding()]
param(
    [ValidateSet('3.12.10')]
    [string] $PythonVersion = '3.12.10'
)

$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$destination = [System.IO.Path]::GetFullPath((Join-Path $root 'runtime\python'))
$temp = [System.IO.Path]::GetTempPath()
$work = Join-Path $temp ("nano-runtime-build-" + [System.Guid]::NewGuid().ToString('N'))
$runtime = Join-Path $work 'python'
$sitePackages = Join-Path $runtime 'Lib\site-packages'
$zip = Join-Path $work "python-$PythonVersion-embed-amd64.zip"
$url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"

Write-Host "Nano embedded runtime -> $destination (prepared separately before replacement)"

New-Item -ItemType Directory -Path $runtime -Force | Out-Null

try {
Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
# Digest from python.org's matching .sigstore messageSignature. Verify before
# extracting or executing downloaded code; changing Python requires a new pin.
$pythonSha256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pythonSha256) {
    throw 'Embedded Python archive checksum mismatch.'
}
Expand-Archive -Path $zip -DestinationPath $runtime -Force
Remove-Item $zip -Force -ErrorAction SilentlyContinue

$python = Join-Path $runtime 'python.exe'
if (-not (Test-Path $python)) { throw "python.exe not found after extracting $url" }

$pth = Get-ChildItem -Path $runtime -Filter '*._pth' | Select-Object -First 1
if (-not $pth) { throw 'Embedded Python ._pth file not found.' }
$stem = [System.IO.Path]::GetFileNameWithoutExtension($pth.Name)
Set-Content -LiteralPath $pth.FullName -Encoding ascii -Value @(
    "$stem.zip"
    '.'
    'Lib\site-packages'
    'import site'
)

New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null

# pip is bootstrapped INTO the embedded interpreter rather than borrowed from
# the host. psutil, PyAudio and pygame all ship compiled extensions, so the
# wheels have to be chosen by the interpreter that will actually import them --
# a host Python of a different minor version would install unloadable binaries.
$getPip = Join-Path $work 'get-pip.py'
# Pinned to one commit of pypa/get-pip, not the moving bootstrap.pypa.io URL:
# that file changes with every pip release (pip 26.3 on 2026-11-02 broke the
# 0.3.0-beta.1 build on the checksum below). This copy is byte-identical to
# what bootstrap.pypa.io served on 2026-11-19; to update, compare both again.
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/pypa/get-pip/c7135ee10cd1b1fcb8fbf63d5d0004885cba7aa4/public/get-pip.py' -OutFile $getPip -UseBasicParsing
$getPipSha256 = '356e0d0de21cdc348c3394084d90939b85e4246c4d14c76aee6ca4f46646ea98'
if ((Get-FileHash -LiteralPath $getPip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $getPipSha256) {
    throw 'pip bootstrap checksum mismatch; review the upstream change before updating the pin.'
}
& $python $getPip --no-warn-script-location --no-cache-dir
if ($LASTEXITCODE -ne 0) { throw 'Failed to bootstrap pip into the embedded runtime.' }
Remove-Item $getPip -Force -ErrorAction SilentlyContinue

# EXACT VERSIONS, EVERY BYTE HASH-CHECKED. Both installs below read the locks
# that scripts/lock_python_deps.py generates from requirements-build.txt and
# requirements.txt -- the same locks CI tests against -- so a runtime built today
# and one built next month hold identical packages, and a download that does
# not match its recorded sha256 stops the build (docs/DEPENDENCIES.md).
#
# pip, setuptools and wheel have to be present in the interpreter itself,
# because the runtime install cannot use pip's build isolation.
#
# THE TRAP: eel is published only as a source distribution, so pip has to build
# a wheel for it, and it normally does that in an isolated environment which it
# hands to the build backend THROUGH PYTHONPATH. An embedded CPython with a
# `._pth` ignores PYTHONPATH completely -- that is the whole point of the file --
# so the isolated environment is invisible and the build dies with
# "Cannot import 'setuptools.build_meta'". Supplying the backend directly and
# turning isolation off is the fix; the two are a pair and neither works alone.
# Locking the backend is what makes that build reproducible too: an isolated
# build would have downloaded whatever setuptools was newest, unhashed.
& $python -m pip install --disable-pip-version-check --no-warn-script-location `
    --no-cache-dir --index-url https://pypi.org/simple --require-hashes `
    -r (Join-Path $root 'requirements\build.lock')
if ($LASTEXITCODE -ne 0) { throw 'Failed to install the build backend into the embedded runtime.' }

# The public index is named explicitly: a mirror configured on the build
# machine has reported eel as unavailable before.
& $python -m pip install --disable-pip-version-check --no-warn-script-location `
    --no-cache-dir --no-build-isolation --index-url https://pypi.org/simple `
    --require-hashes -r (Join-Path $root 'requirements\runtime.lock')
if ($LASTEXITCODE -ne 0) { throw 'Failed to install Nano runtime dependencies.' }

# THE SPEECH PACK SHIPS IN THE RUNTIME. Until 0.4.0-beta.3 the installer had no
# speech recognition at all: faster-whisper lives in requirements/optional.lock,
# and the "Preparar a voz" button that pip-installs it at run time could not
# work in an installation -- optional.lock is not packaged, so it stopped at
# "lock_missing", and anything pip had put into resources\app would have been
# wiped by the next automatic update anyway. "Ei Nano" therefore did nothing
# on an installed NANO. The pack is now installed here, at build time, with
# exactly the same package list the button uses (core/voice_setup.py computes
# it from the lock), every download hash-checked. The model itself (~480 MB)
# is still downloaded only when the person presses "Preparar a voz".
$speechRequirements = Join-Path $work 'speech-requirements.txt'
$previousAppRootForSpeech = $env:NANO_APP_ROOT
$previousDataDirForSpeech = $env:NANO_DATA_DIR
$speechProbeData = Join-Path $work 'speech-probe-data'
New-Item -ItemType Directory -Force -Path $speechProbeData | Out-Null
$env:NANO_APP_ROOT = $root
$env:NANO_DATA_DIR = $speechProbeData
try {
    & $python -c "import os, sys; sys.path.insert(0, os.environ['NANO_APP_ROOT']); from core import voice_setup; text = voice_setup.requirements_text(voice_setup.OPTIONAL_LOCK.read_text(encoding='utf-8')); assert 'faster-whisper==' in text, 'speech pack missing from optional.lock'; open(sys.argv[1], 'w', encoding='utf-8').write(text)" $speechRequirements
    if ($LASTEXITCODE -ne 0) { throw 'Could not compute the speech pack requirements.' }
}
finally {
    $env:NANO_APP_ROOT = $previousAppRootForSpeech
    $env:NANO_DATA_DIR = $previousDataDirForSpeech
}
& $python -m pip install --disable-pip-version-check --no-warn-script-location `
    --no-cache-dir --no-build-isolation --only-binary=:all: --index-url https://pypi.org/simple `
    --require-hashes -r $speechRequirements
if ($LASTEXITCODE -ne 0) { throw 'Failed to install the speech pack (faster-whisper).' }

# ONE C++ RUNTIME FOR THE WHOLE PROCESS. The winrt wheels (island music,
# local OCR) carry their own msvcp140.dll, version 14.29. Windows loads a DLL
# name only once per process, so once winrt was imported, onnxruntime (the
# Silero voice detector "Ei Nano" listens with) got that old copy and the
# backend died with 0xC0000005 the moment it loaded -- seen on Simao's PC with
# the first 0.4.0-beta.3 build. onnxruntime needs 14.40 or newer. The build
# host's current Microsoft C++ runtime (app-local deployment of these files is
# allowed by Microsoft) goes next to python.exe and replaces winrt's copy; a
# newer msvcp140 is backward compatible with code built for an older one.
$minimumCpp = [version]'14.40.0.0'
$cppNames = @('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll', 'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll')
$systemCpp = Join-Path $env:SystemRoot 'System32\msvcp140.dll'
if (-not (Test-Path -LiteralPath $systemCpp)) { throw 'The build host has no Microsoft C++ runtime (msvcp140.dll).' }
$systemCppVersion = [version]((Get-Item -LiteralPath $systemCpp).VersionInfo.FileVersion -split ' ')[0]
if ($systemCppVersion -lt $minimumCpp) { throw "The build host's msvcp140.dll is $systemCppVersion; onnxruntime needs $minimumCpp or newer." }
foreach ($name in $cppNames) {
    $source = Join-Path $env:SystemRoot "System32\$name"
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $runtime $name) -Force }
}
Get-ChildItem $sitePackages -Recurse -File -Filter 'msvcp140.dll' -ErrorAction SilentlyContinue | ForEach-Object {
    $version = [version](($_.VersionInfo.FileVersion -split ' ')[0])
    if ($version -lt $minimumCpp) {
        Copy-Item -LiteralPath $systemCpp -Destination $_.FullName -Force
        Write-Host ("  replaced old msvcp140.dll {0} in {1}" -f $version, $_.DirectoryName.Substring($sitePackages.Length + 1))
    }
}

# Caches are build-host droppings: they are large, they are not reproducible,
# and a stale one can shadow a source file that changed.
Get-ChildItem $runtime -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# Test suites, example programs and bundled documentation from the dependencies.
# None of them is imported at run time, together they are tens of megabytes of a
# user's download, and they are where every stray .pem and .wav in the runtime
# comes from -- gevent and future ship TLS fixtures, pygame ships sample audio.
# Each entry is a directory that a package does not import from itself; the
# import checks below run AFTER this pruning, so a wrong guess fails the build
# here rather than on a user's machine.
$prunable = @(
    'certifi\tests'
    'pip\_vendor\certifi\tests'
    'future\tests'
    'future\moves\test'
    'greenlet\tests'
    'importlib_resources\tests'
    'psutil\tests'
    'future\backports\test'
    'gevent\tests'
    'gevent\testing'
    'pygame\examples'
    'pygame\docs'
    'pygame\tests'
    'zope\interface\tests'
    'zope\interface\common\tests'
)
foreach ($relative in $prunable) {
    $target = Join-Path $sitePackages $relative
    if (Test-Path $target) {
        Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  pruned $relative"
    }
}
# The speech pack brings numpy, onnxruntime, av and friends, each with its own
# test folders. electron/scripts/verify-built-package.js refuses any folder
# named test(s) in the package, so every one left is removed here; the import
# checks below prove nothing that is imported went with them.
Get-ChildItem $sitePackages -Recurse -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @('tests', 'test') } |
    Sort-Object { $_.FullName.Length } -Descending |
    ForEach-Object {
        if (Test-Path -LiteralPath $_.FullName) {
            Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
            Write-Host ("  pruned " + $_.FullName.Substring($sitePackages.Length + 1))
        }
    }

# ---------------------------------------------------------------- verification
# The runtime is proved by running it, not by trusting that pip exited zero.
#
# WHAT IS DELIBERATELY NOT IMPORTED: `core.main`. It looks like the most
# thorough possible check, and it is the wrong one for a build script. Its
# module-level start-up runs `data_migration`, which goes looking for an
# existing Nano profile and COPIES IT into whatever NANO_DATA_DIR points at --
# on a developer machine that means reading that person's real conversations
# and memories. Preparing a runtime must never touch a user's data at all.
#
# What is checked instead proves the same two things the packaging step needs:
# that every third-party dependency imports under the embedded interpreter, and
# that Nano's own package is reachable the way the shipped application reaches
# it. Whether the whole application boots is settled by launching the packaged
# app, which is the only test that ever really answered that question.
#
# NANO_DATA_DIR still points at a throwaway directory, so that an import which
# unexpectedly touches storage cannot reach the real profile either.
$probeData = Join-Path $temp ("nano-runtime-probe-" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $probeData | Out-Null
$previousDataDir = $env:NANO_DATA_DIR
$previousAppRoot = $env:NANO_APP_ROOT
$env:NANO_DATA_DIR = $probeData
$env:NANO_APP_ROOT = $root
try {
    & $python -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version; print('Nano embedded interpreter OK', sys.version.split()[0])"
    if ($LASTEXITCODE -ne 0) { throw 'Embedded interpreter check failed.' }

    & $python -c "import eel, httpx, groq, psutil, yaml, dotenv, pygame, edge_tts, pyaudio, sqlite3, ssl; print('Nano embedded dependencies OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Embedded dependency import check failed.' }

    # The ears: the transcriber, and the Silero voice detector that "Ei Nano"
    # listens with (core/listening.py loads it from faster-whisper's assets).
    & $python -c "import faster_whisper, ctranslate2, av, onnxruntime, tokenizers; from faster_whisper.vad import get_vad_model; get_vad_model(); print('Nano speech pack OK', faster_whisper.__version__)"
    if ($LASTEXITCODE -ne 0) { throw 'Embedded speech pack import check failed.' }

    # The order the backend really uses: winrt first (the island's music
    # starts before "Ei Nano"), THEN the voice detector. This is the exact
    # sequence that crashed with 0xC0000005 when winrt's old msvcp140.dll won.
    & $python -c "import winrt.windows.media.control; from faster_whisper.vad import get_vad_model; get_vad_model(); print('Nano winrt + voice detector in one process OK')"
    if ($LASTEXITCODE -ne 0) { throw 'winrt and the voice detector cannot load in one process (C++ runtime clash).' }

    # Exactly how the shipped application reaches its own code: the app root
    # arrives through NANO_APP_ROOT, never through the ._pth.
    & $python -c "import os, sys; sys.path.insert(0, os.environ['NANO_APP_ROOT']); import core.app_paths, core.version; assert core.version.product() != '0.0.0', 'version.json not readable from the app root'; print('Nano embedded core import OK', core.version.product())"
    if ($LASTEXITCODE -ne 0) { throw 'Embedded core import check failed.' }
}
finally {
    $env:NANO_DATA_DIR = $previousDataDir
    $env:NANO_APP_ROOT = $previousAppRoot
    Remove-Item $probeData -Recurse -Force -ErrorAction SilentlyContinue
}

$size = [math]::Round((Get-ChildItem $runtime -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
# The previous working runtime remains untouched if download/install/probes fail.
# Reject junctions before replacing this narrowly named generated directory.
foreach ($candidate in @((Join-Path $root 'runtime'), $destination)) {
    if (Test-Path -LiteralPath $candidate) {
        $item = Get-Item -LiteralPath $candidate -Force
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw 'Runtime destination must not be a link or junction.'
        }
    }
}
if ($destination -ne [System.IO.Path]::GetFullPath((Join-Path $root 'runtime\python'))) {
    throw 'Runtime destination escaped the expected workspace directory.'
}
if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
Move-Item -LiteralPath $runtime -Destination $destination
Write-Host "Nano embedded runtime ready: $size MB at $destination"
}
finally {
    if ([System.IO.Path]::GetFullPath($work).StartsWith([System.IO.Path]::GetFullPath($temp), [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}
