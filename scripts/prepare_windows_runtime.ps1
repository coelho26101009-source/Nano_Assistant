$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..')
$runtime = Join-Path $root 'runtime/python'
$zip = Join-Path $env:TEMP 'python-embed.zip'
$pyVersion = '3.12.10'
$url = "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-embed-amd64.zip"

if (Test-Path $runtime) { Remove-Item $runtime -Recurse -Force }
New-Item -ItemType Directory -Path $runtime | Out-Null
Invoke-WebRequest -Uri $url -OutFile $zip
Expand-Archive -Path $zip -DestinationPath $runtime -Force

# The embeddable distribution disables site-packages by default.
$pth = Get-ChildItem $runtime -Filter '*._pth' | Select-Object -First 1
if (-not $pth) { throw 'Python _pth file not found' }
$content = Get-Content $pth.FullName
$content = $content | Where-Object { $_ -ne 'import site' }
Set-Content $pth.FullName ($content + 'import site') -Encoding ascii

$installer = Join-Path $env:TEMP 'get-pip.py'
Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $installer
& (Join-Path $runtime 'python.exe') $installer --no-warn-script-location

# Keep the installer reproducible and do not ship caches, tests or secrets.
& (Join-Path $runtime 'python.exe') -m pip install --disable-pip-version-check --no-warn-script-location --target (Join-Path $runtime 'Lib/site-packages') -r (Join-Path $root 'requirements.txt')

Remove-Item $zip,$installer -Force -ErrorAction SilentlyContinue
Get-ChildItem $runtime -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $runtime -Recurse -Directory -Filter '*.dist-info' | Where-Object { $_.Name -like '*pip*' -or $_.Name -like '*setuptools*' } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
