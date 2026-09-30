<#
    Renders every derivative of the approved N symbol: the web symbol the UI
    and favicon use, the Electron icons and the voice overlay mark.

    WHY A SCRIPT AND NOT A CHECKED-IN BINARY
    The source of truth for the Nano identity is the master in
    frontend/public/branding/nano-symbol-original.png (a clean transparent PNG,
    kept byte-for-byte as supplied). This script derives everything else from
    that exact file, so the web symbol, the taskbar icon, the tray icon and the
    mark inside the overlay cannot drift apart. Replace the master, re-run
    this, and they all agree again.

    The symbol already has transparency; no background is removed and nothing
    is redrawn or traced. The master is cropped to its content (a lossless
    crop plus a small transparent margin) and every resize runs on
    premultiplied alpha, so transparent pixels never bleed a matte or halo
    into the silhouette. Only the small tile behind the Windows icon is
    generated here.

    Requires only System.Drawing, which ships with Windows PowerShell.

        powershell -ExecutionPolicy Bypass -File scripts\build_app_icon.ps1
#>

Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = 'Stop'
$repoRoot   = Split-Path -Parent $PSScriptRoot
$assetsDir  = Join-Path $repoRoot 'electron\assets'
$markPath   = Join-Path $repoRoot 'frontend\public\branding\nano-symbol-original.png'
$webPath    = Join-Path $repoRoot 'frontend\public\branding\nano-symbol.png'
$icoPath    = Join-Path $assetsDir 'icon.ico'
$pngPath    = Join-Path $assetsDir 'icon.png'
$trayPath   = Join-Path $assetsDir 'tray.png'
$overlayPath = Join-Path $repoRoot 'electron\overlay\nano-mark.png'

if (-not (Test-Path $markPath)) {
    throw "the approved N symbol is missing: $markPath"
}
if (-not (Test-Path $assetsDir)) { New-Item -ItemType Directory -Path $assetsDir | Out-Null }

# Design tokens, copied from frontend/styles/globals.css.
$cBase = [System.Drawing.ColorTranslator]::FromHtml('#101419')
$cEdge = [System.Drawing.ColorTranslator]::FromHtml('#202B3A')
$cGlow = [System.Drawing.Color]::FromArgb(48, 47, 111, 237)

# Measures where the symbol actually is inside the master. Alpha above 8/255
# is content; the fainter pixels are anti-aliasing dust that must not move the
# crop. Done in C# because scanning 1.5 million pixels in PowerShell takes minutes.
Add-Type -TypeDefinition @"
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
public static class NanoAlpha {
    public static int[] Bounds(Bitmap bmp, int threshold) {
        int w = bmp.Width, h = bmp.Height;
        var data = bmp.LockBits(new Rectangle(0, 0, w, h), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        byte[] px = new byte[Math.Abs(data.Stride) * h];
        Marshal.Copy(data.Scan0, px, 0, px.Length);
        bmp.UnlockBits(data);
        int x0 = w, y0 = h, x1 = -1, y1 = -1;
        for (int y = 0; y < h; y++) {
            int row = y * data.Stride;
            for (int x = 0; x < w; x++) {
                if (px[row + x * 4 + 3] > threshold) {
                    if (x < x0) x0 = x; if (x > x1) x1 = x;
                    if (y < y0) y0 = y; if (y > y1) y1 = y;
                }
            }
        }
        return new int[] { x0, y0, x1, y1 };
    }
}
"@ -ReferencedAssemblies System.Drawing

$master = [System.Drawing.Bitmap]::FromFile($markPath)
$bounds = [NanoAlpha]::Bounds($master, 8)
$cropMargin = 20   # transparent px kept around the content, at master scale
$cropX = [Math]::Max(0, $bounds[0] - $cropMargin)
$cropY = [Math]::Max(0, $bounds[1] - $cropMargin)
$cropW = [Math]::Min($master.Width,  $bounds[2] + 1 + $cropMargin) - $cropX
$cropH = [Math]::Min($master.Height, $bounds[3] + 1 + $cropMargin) - $cropY

# A 1:1 copy of the crop into a PREMULTIPLIED canvas. Every later resize
# interpolates premultiplied colour, which is what keeps the edge free of a
# white or dark fringe: a transparent pixel contributes nothing, not its RGB.
$source = New-Object System.Drawing.Bitmap($cropW, $cropH, [System.Drawing.Imaging.PixelFormat]::Format32bppPArgb)
$sg = [System.Drawing.Graphics]::FromImage($source)
$sg.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
$sg.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
$sg.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
$sg.DrawImage($master,
    (New-Object System.Drawing.Rectangle(0, 0, $cropW, $cropH)),
    $cropX, $cropY, $cropW, $cropH, [System.Drawing.GraphicsUnit]::Pixel)
$sg.Dispose()
$master.Dispose()

function Resize-Premultiplied {
    # Halves repeatedly (a clean 2x box average), then one bicubic step, so a
    # 20x reduction to a 16 px tray frame is averaged rather than aliased.
    param([System.Drawing.Bitmap]$Bitmap, [int]$Width, [int]$Height)
    if ($Bitmap.Width -eq $Width -and $Bitmap.Height -eq $Height) { return $Bitmap.Clone() }
    $current = $Bitmap
    while ($current.Width -ge ($Width * 2) -and $current.Height -ge ($Height * 2)) {
        $half = New-Object System.Drawing.Bitmap([int][Math]::Floor($current.Width / 2), [int][Math]::Floor($current.Height / 2), [System.Drawing.Imaging.PixelFormat]::Format32bppPArgb)
        $hg = [System.Drawing.Graphics]::FromImage($half)
        $hg.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
        $hg.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBilinear
        $hg.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
        $attr = New-Object System.Drawing.Imaging.ImageAttributes
        $attr.SetWrapMode([System.Drawing.Drawing2D.WrapMode]::TileFlipXY)
        $hg.DrawImage($current, (New-Object System.Drawing.Rectangle(0, 0, $half.Width, $half.Height)),
            0, 0, $current.Width, $current.Height, [System.Drawing.GraphicsUnit]::Pixel, $attr)
        $attr.Dispose(); $hg.Dispose()
        if ($current -ne $Bitmap) { $current.Dispose() }
        $current = $half
    }
    $out = New-Object System.Drawing.Bitmap($Width, $Height, [System.Drawing.Imaging.PixelFormat]::Format32bppPArgb)
    $og = [System.Drawing.Graphics]::FromImage($out)
    $og.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
    $og.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $og.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
    $attr = New-Object System.Drawing.Imaging.ImageAttributes
    $attr.SetWrapMode([System.Drawing.Drawing2D.WrapMode]::TileFlipXY)
    $og.DrawImage($current, (New-Object System.Drawing.Rectangle(0, 0, $Width, $Height)),
        0, 0, $current.Width, $current.Height, [System.Drawing.GraphicsUnit]::Pixel, $attr)
    $attr.Dispose(); $og.Dispose()
    if ($current -ne $Bitmap) { $current.Dispose() }
    return $out
}

function New-NanoBitmap {
    param(
        [int]$Size,
        [switch]$NoBadge      # transparent background instead of the dark tile
    )

    # Supersample, then downscale: compositing straight to 16 px leaves the
    # the symbol's contour ragged, and a tray icon lives at exactly that size.
    $ss = 4
    $big = $Size * $ss
    $bmp = New-Object System.Drawing.Bitmap($big, $big, [System.Drawing.Imaging.PixelFormat]::Format32bppPArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
    $g.Clear([System.Drawing.Color]::Transparent)

    # The rounded dark tile the mark sits on in the taskbar. The tray gets no
    # tile: Windows composites it against the taskbar itself, and a second dark
    # square inside the tray looks like a bug.
    # The insets allow for the 20 px margin the crop keeps, so the N fills the
    # same share of the icon as it always has.
    $inset = if ($NoBadge) { [Math]::Round($big * 0.03) } else { [Math]::Round($big * 0.07) }
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

        # A restrained blue light behind the original mark.
        $glow = New-Object System.Drawing.Drawing2D.GraphicsPath
        $glow.AddEllipse($big * 0.12, $big * 0.18, $big * 0.76, $big * 0.76)
        $bloom = New-Object System.Drawing.Drawing2D.PathGradientBrush($glow)
        $bloom.CenterColor = $cGlow
        $bloom.SurroundColors = @([System.Drawing.Color]::FromArgb(0, 47, 111, 237))
        $g.FillPath($bloom, $glow)
        $bloom.Dispose(); $glow.Dispose()

        $g.SetClip($tile)
        $tile.Dispose()
    }

    # Fit the wider N inside the square without distorting it. The symbol is
    # resized on its own (premultiplied) and then laid on at 1:1, so the tile
    # never takes part in the interpolation.
    $box = $big - (2 * $inset)
    $scale = [Math]::Min($box / $source.Width, $box / $source.Height)
    $w = [int][Math]::Round($source.Width * $scale)
    $h = [int][Math]::Round($source.Height * $scale)
    $fitted = Resize-Premultiplied -Bitmap $source -Width $w -Height $h
    $g.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceOver
    $g.DrawImageUnscaled($fitted, [int][Math]::Round(($big - $w) / 2), [int][Math]::Round(($big - $h) / 2))
    $fitted.Dispose()
    $g.Dispose()

    $out = Resize-Premultiplied -Bitmap $bmp -Width $Size -Height $Size
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

# The UI and favicon symbol: the same cropped master, just smaller. 360 px wide
# is 2x the largest place it is shown (the 56 px home mark) with headroom.
$webWidth  = 360
$webHeight = [int][Math]::Round($webWidth * $source.Height / $source.Width)
$web = Resize-Premultiplied -Bitmap $source -Width $webWidth -Height $webHeight
$web.Save($webPath, [System.Drawing.Imaging.ImageFormat]::Png)
$web.Dispose()

$overlay = New-NanoBitmap -Size 128 -NoBadge
$overlay.Save($overlayPath, [System.Drawing.Imaging.ImageFormat]::Png)
$overlay.Dispose()

$source.Dispose()

Write-Output ("symbol    crop {0},{1} {2}x{3} of the master; nano-symbol.png {4}x{5}, SYMBOL_RATIO = {4}/{5}" -f $cropX, $cropY, $cropW, $cropH, $webWidth, $webHeight)
Write-Output ("icon.ico  {0} bytes, {1} frames: {2}" -f (Get-Item $icoPath).Length, $frames.Count, ($sizes -join ', '))
Write-Output ("icon.png  {0} bytes (256x256)" -f (Get-Item $pngPath).Length)
Write-Output ("tray.png  {0} bytes (32x32, no tile)" -f (Get-Item $trayPath).Length)
Write-Output ("overlay mark  {0} bytes (128x128, no tile)" -f (Get-Item $overlayPath).Length)
