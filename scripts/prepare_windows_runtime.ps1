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
Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/pip/get-pip.py' -OutFile $getPip -UseBasicParsing
$getPipSha256 = 'fb24e693bab954209a063d90953621412ccad4a500905a726286e038f508ddf6'
if ((Get-FileHash -LiteralPath $getPip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $getPipSha256) {
    throw 'pip bootstrap checksum mismatch; review the upstream change before updating the pin.'
}
& $python $getPip --no-warn-script-location --no-cache-dir
if ($LASTEXITCODE -ne 0) { throw 'Failed to bootstrap pip into the embedded runtime.' }
Remove-Item $getPip -Force -ErrorAction SilentlyContinue

# setuptools and wheel have to be present in the interpreter itself, because
# the install below cannot use pip's build isolation.
#
# THE TRAP: eel is published only as a source distribution, so pip has to build
# a wheel for it, and it normally does that in an isolated environment which it
# hands to the build backend THROUGH PYTHONPATH. An embedded CPython with a
# `._pth` ignores PYTHONPATH completely -- that is the whole point of the file --
# so the isolated environment is invisible and the build dies with
# "Cannot import 'setuptools.build_meta'". Supplying the backend directly and
# turning isolation off is the fix; the two are a pair and neither works alone.
& $python -m pip install --disable-pip-version-check --no-warn-script-location `
    --no-cache-dir --index-url https://pypi.org/simple setuptools wheel
if ($LASTEXITCODE -ne 0) { throw 'Failed to install the build backend into the embedded runtime.' }

# The public index is named explicitly: a mirror configured on the build
# machine has reported eel as unavailable before.
& $python -m pip install --disable-pip-version-check --no-warn-script-location `
    --no-cache-dir --no-build-isolation --index-url https://pypi.org/simple `
    -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Failed to install Nano runtime dependencies.' }

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
