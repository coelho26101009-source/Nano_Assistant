<#
    Renders electron/assets/icon.ico, icon.png and tray.png from Nano's own mark.

    WHY A SCRIPT AND NOT A CHECKED-IN BINARY
    The source of truth for the Nano identity is the supplied artwork in
    frontend/public/branding/. This script composites that exact file, so the
    taskbar icon, the tray icon and the mark inside the window cannot drift
    apart. Replace the artwork, re-run this, all three agree again.

    It reads nano-mark-alpha.png -- the transparent variant produced by
    scripts/derive_brand_assets.py -- because the supplied master has no alpha
    channel and would paste as a black square on the tray.

    It used to REDRAW an older, different mark from primitives. That mark no
    longer exists, and a script that invents its own logo is exactly how a
    desktop icon ends up disagreeing with the application it launches.

    Requires only System.Drawing, which ships with Windows PowerShell.

        powershell -ExecutionPolicy Bypass -File scripts\build_app_icon.ps1
#>

Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = 'Stop'
$repoRoot   = Split-Path -Parent $PSScriptRoot
$assetsDir  = Join-Path $repoRoot 'electron\assets'
$markPath   = Join-Path $repoRoot 'frontend\public\branding\nano-mark-alpha.png'
$icoPath    = Join-Path $assetsDir 'icon.ico'
$pngPath    = Join-Path $assetsDir 'icon.png'
$trayPath   = Join-Path $assetsDir 'tray.png'

if (-not (Test-Path $markPath)) {
    throw "the transparent mark is missing: $markPath. Run scripts/derive_brand_assets.py first."
}
if (-not (Test-Path $assetsDir)) { New-Item -ItemType Directory -Path $assetsDir | Out-Null }

# Design tokens, copied from frontend/styles/globals.css.
$cBase = [System.Drawing.ColorTranslator]::FromHtml('#0A0706')   # --bg-base
$cEdge = [System.Drawing.ColorTranslator]::FromHtml('#1C1010')
$cGlow = [System.Drawing.Color]::FromArgb(70, 244, 1, 1)         # --brand-red, dimmed

$source = [System.Drawing.Bitmap]::FromFile($markPath)

function New-NanoBitmap {
    param(
        [int]$Size,
        [switch]$NoBadge      # transparent background instead of the dark tile
    )

    # Supersample, then downscale: compositing straight to 16 px leaves the
    # flame's cutouts ragged, and a tray icon lives at exactly that size.
    $ss = 4
    $big = $Size * $ss
    $bmp = New-Object System.Drawing.Bitmap($big, $big, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.Clear([System.Drawing.Color]::Transparent)

    # The rounded dark tile the mark sits on in the taskbar. The tray gets no
    # tile: Windows composites it against the taskbar itself, and a second dark
    # square inside the tray looks like a bug.
    $inset = if ($NoBadge) { [Math]::Round($big * 0.02) } else { [Math]::Round($big * 0.06) }
    if (-not $NoBadge) {
        $radius = $big * 0.22
        $tile = New-Object System.Drawing.Drawing2D.GraphicsPath
        $d = $radius * 2
        $tile.AddArc(0, 0, $d, $d, 180, 90)
        $tile.AddArc($big - $d, 0, $d, $d, 270, 90)
        $tile.AddArc($big - $d, $big - $d, $d, $d, 0, 90)
        $tile.AddArc(0, $big - $d, $d, $d, 90, 90)
        $tile.CloseFigure()

        $brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
            (New-Object System.Drawing.Point(0, 0)),
            (New-Object System.Drawing.Point($big, $big)),
            $cEdge, $cBase)
        $g.FillPath($brush, $tile)
        $brush.Dispose()

        # A soft flame-coloured bloom behind the mark, the same idea as the
        # ambient glow in the shell.
        $glow = New-Object System.Drawing.Drawing2D.GraphicsPath
        $glow.AddEllipse($big * 0.12, $big * 0.18, $big * 0.76, $big * 0.76)
        $bloom = New-Object System.Drawing.Drawing2D.PathGradientBrush($glow)
        $bloom.CenterColor = $cGlow
        $bloom.SurroundColors = @([System.Drawing.Color]::FromArgb(0, 244, 1, 1))
        $g.FillPath($bloom, $glow)
        $bloom.Dispose(); $glow.Dispose()

        $g.SetClip($tile)
        $tile.Dispose()
    }

    # The artwork is taller than it is wide; fit it inside the square without
    # distorting it, which is the one thing a logo may never do.
    $box = $big - (2 * $inset)
    $scale = [Math]::Min($box / $source.Width, $box / $source.Height)
    $w = $source.Width * $scale
    $h = $source.Height * $scale
    $g.DrawImage($source, [single](($big - $w) / 2), [single](($big - $h) / 2), [single]$w, [single]$h)

    $g.Dispose()

    $out = New-Object System.Drawing.Bitmap($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $og = [System.Drawing.Graphics]::FromImage($out)
    $og.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $og.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $og.Clear([System.Drawing.Color]::Transparent)
    $og.DrawImage($bmp, (New-Object System.Drawing.Rectangle(0, 0, $Size, $Size)))
    $og.Dispose()
    $bmp.Dispose()
    return $out
}

function Get-PngBytes {
    param([System.Drawing.Bitmap]$Bitmap)
    $ms = New-Object System.IO.MemoryStream
    $Bitmap.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $bytes = $ms.ToArray()
    $ms.Dispose()
    return , $bytes
}

# --- Build the .ico -------------------------------------------------------
# Vista-era ICO: PNG-compressed frames, which Windows, Electron and
# electron-builder all read. 256 is required by electron-builder.
$sizes = @(16, 20, 24, 32, 48, 64, 128, 256)
$frames = @()
foreach ($size in $sizes) {
    $bmp = New-NanoBitmap -Size $size
    $frames += , @{ Size = $size; Bytes = (Get-PngBytes -Bitmap $bmp) }
    if ($size -eq 256) { $bmp.Save($pngPath, [System.Drawing.Imaging.ImageFormat]::Png) }
    $bmp.Dispose()
}

$stream = [System.IO.File]::Create($icoPath)
$writer = New-Object System.IO.BinaryWriter($stream)
$writer.Write([UInt16]0)                  # reserved
$writer.Write([UInt16]1)                  # type: icon
$writer.Write([UInt16]$frames.Count)

$offset = 6 + (16 * $frames.Count)
foreach ($frame in $frames) {
    $dim = if ($frame.Size -ge 256) { 0 } else { $frame.Size }
    $writer.Write([Byte]$dim)             # width  (0 means 256)
    $writer.Write([Byte]$dim)             # height
    $writer.Write([Byte]0)                # palette size
    $writer.Write([Byte]0)                # reserved
    $writer.Write([UInt16]1)              # colour planes
    $writer.Write([UInt16]32)             # bits per pixel
    $writer.Write([UInt32]$frame.Bytes.Length)
    $writer.Write([UInt32]$offset)
    $offset += $frame.Bytes.Length
}
foreach ($frame in $frames) { $writer.Write($frame.Bytes) }
$writer.Flush(); $writer.Dispose(); $stream.Dispose()

# --- Tray bitmap ----------------------------------------------------------
$tray = New-NanoBitmap -Size 32 -NoBadge
$tray.Save($trayPath, [System.Drawing.Imaging.ImageFormat]::Png)
$tray.Dispose()

$source.Dispose()

Write-Output ("icon.ico  {0} bytes, {1} frames: {2}" -f (Get-Item $icoPath).Length, $frames.Count, ($sizes -join ', '))
Write-Output ("icon.png  {0} bytes (256x256)" -f (Get-Item $pngPath).Length)
Write-Output ("tray.png  {0} bytes (32x32, no tile)" -f (Get-Item $trayPath).Length)
