<#
    Renders electron/assets/icon.ico from Nano's own mark.

    WHY A SCRIPT AND NOT A CHECKED-IN BINARY
    The source of truth for the Nano identity is frontend/components/NanoLogo.tsx:
    a geometric "N" cut from a hexagonal core inside a broken orbital ring. This
    script redraws those exact primitives -- same 48x48 coordinate space, same
    tokens from styles/globals.css -- so the desktop icon cannot drift away from
    the icon in the sidebar. Change the mark, re-run this, both agree again.

    No third-party logo, no downloaded asset, no traced illustration: every
    shape here is one of the paths in NanoLogo.tsx.

    Requires only System.Drawing, which ships with Windows PowerShell.

        powershell -ExecutionPolicy Bypass -File scripts\build_app_icon.ps1
#>

Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = 'Stop'
$repoRoot   = Split-Path -Parent $PSScriptRoot
$assetsDir  = Join-Path $repoRoot 'electron\assets'
$icoPath    = Join-Path $assetsDir 'icon.ico'
$pngPath    = Join-Path $assetsDir 'icon.png'
$trayPath   = Join-Path $assetsDir 'tray.png'

if (-not (Test-Path $assetsDir)) { New-Item -ItemType Directory -Path $assetsDir | Out-Null }

# Design tokens, copied from frontend/styles/globals.css.
$cBase    = [System.Drawing.ColorTranslator]::FromHtml('#0A0E13')  # --surface-1
$cEdge    = [System.Drawing.ColorTranslator]::FromHtml('#151D26')
$cAccent  = [System.Drawing.ColorTranslator]::FromHtml('#2DD4BF')  # --accent
$cAccent2 = [System.Drawing.ColorTranslator]::FromHtml('#7DD3FC')  # --accent-2

function New-NanoBitmap {
    param(
        [int]$Size,
        [switch]$Bare,        # drop the orbit + nodes, for tiny sizes
        [switch]$NoBadge      # transparent background instead of the dark tile
    )

    # Supersample, then downscale: GDI+ antialiasing alone leaves the 2 px
    # strokes ragged at 16 and 32 px, which is exactly where a tray icon lives.
    $ss = 4
    $big = $Size * $ss
    $bmp = New-Object System.Drawing.Bitmap($big, $big, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.Clear([System.Drawing.Color]::Transparent)

    # The mark is authored in a 48x48 box, like the SVG.
    $s = $big / 48.0
    function P { param($x, $y) New-Object System.Drawing.PointF(($x * $s), ($y * $s)) }

    if (-not $NoBadge) {
        # Rounded dark tile. An app icon needs a body: floating strokes vanish
        # against a light taskbar and read as a glyph, not an application.
        $inset = 1.0 * $s
        $r = 10.0 * $s
        $w = $big - (2 * $inset)
        $tile = New-Object System.Drawing.Drawing2D.GraphicsPath
        $tile.AddArc($inset, $inset, $r, $r, 180, 90)
        $tile.AddArc(($inset + $w - $r), $inset, $r, $r, 270, 90)
        $tile.AddArc(($inset + $w - $r), ($inset + $w - $r), $r, $r, 0, 90)
        $tile.AddArc($inset, ($inset + $w - $r), $r, $r, 90, 90)
        $tile.CloseFigure()

        $tileBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
            (New-Object System.Drawing.PointF(0, 0)),
            (New-Object System.Drawing.PointF($big, $big)), $cEdge, $cBase)
        $g.FillPath($tileBrush, $tile)
        $tileBrush.Dispose()

        $tilePen = New-Object System.Drawing.Pen(
            ([System.Drawing.Color]::FromArgb(38, $cAccent.R, $cAccent.G, $cAccent.B)), (0.9 * $s))
        $g.DrawPath($tilePen, $tile)
        $tilePen.Dispose()
        $tile.Dispose()
    }

    # The gradient every stroke of the mark shares (accent -> accent-2), matching
    # the linearGradient in NanoLogo.tsx.
    $grad = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        (P 8 6), (P 40 42), $cAccent, $cAccent2)

    if (-not $Bare) {
        # Orbit: two quarter arcs with deliberate gaps (never a full ring, or it
        # reads as a loading spinner). Centre 24,24, radius 20.5.
        $orbitPen = New-Object System.Drawing.Pen(
            ([System.Drawing.Color]::FromArgb(140, $cAccent.R, $cAccent.G, $cAccent.B)), (1.6 * $s))
        $orbitPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $orbitPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $box = New-Object System.Drawing.RectangleF((3.5 * $s), (3.5 * $s), (41.0 * $s), (41.0 * $s))
        $g.DrawArc($orbitPen, $box, -90, 90)   # top  -> right
        $g.DrawArc($orbitPen, $box, 90, 90)    # bottom -> left
        $orbitPen.Dispose()
    }

    # Hexagonal core.
    $hex = New-Object System.Drawing.Drawing2D.GraphicsPath
    $hex.AddPolygon(@((P 24 8.5), (P 38.5 16.75), (P 38.5 31.25), (P 24 39.5), (P 9.5 31.25), (P 9.5 16.75)))
    $hexFill = New-Object System.Drawing.SolidBrush(
        ([System.Drawing.Color]::FromArgb(36, $cAccent.R, $cAccent.G, $cAccent.B)))
    $g.FillPath($hexFill, $hex)
    $hexFill.Dispose()
    $hexPen = New-Object System.Drawing.Pen($grad, (1.8 * $s))
    $hexPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $g.DrawPath($hexPen, $hex)
    $hexPen.Dispose()
    $hex.Dispose()

    # The N: two uprights and the diagonal as one continuous stroke.
    $nPen = New-Object System.Drawing.Pen($grad, (2.9 * $s))
    $nPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $nPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $nPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $n = New-Object System.Drawing.Drawing2D.GraphicsPath
    $n.AddLines(@((P 18.5 31), (P 18.5 17), (P 29.5 31), (P 29.5 17)))
    $g.DrawPath($nPen, $n)
    $nPen.Dispose()
    $n.Dispose()

    if (-not $Bare) {
        # The two circuit nodes on the diagonal.
        $b1 = New-Object System.Drawing.SolidBrush($cAccent)
        $b2 = New-Object System.Drawing.SolidBrush($cAccent2)
        $g.FillEllipse($b1, ((18.5 - 1.9) * $s), ((17 - 1.9) * $s), (3.8 * $s), (3.8 * $s))
        $g.FillEllipse($b2, ((29.5 - 1.9) * $s), ((31 - 1.9) * $s), (3.8 * $s), (3.8 * $s))
        $b1.Dispose(); $b2.Dispose()
    }

    $grad.Dispose()
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
$sizes = @(16, 24, 32, 48, 64, 128, 256)
$frames = @()
foreach ($size in $sizes) {
    # Below 24 px the orbit and the nodes turn to mush, so the mark drops them
    # exactly as NanoLogo.tsx does with its `bare` prop.
    $bmp = if ($size -le 20) { New-NanoBitmap -Size $size -Bare } else { New-NanoBitmap -Size $size }
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
# The tray is drawn at 32 px with no tile: Windows composites it against the
# taskbar, and a second dark square inside the tray looks like a bug.
$tray = New-NanoBitmap -Size 32 -NoBadge
$tray.Save($trayPath, [System.Drawing.Imaging.ImageFormat]::Png)
$tray.Dispose()

Write-Output ("icon.ico  {0} bytes, {1} frames: {2}" -f (Get-Item $icoPath).Length, $frames.Count, ($sizes -join ', '))
Write-Output ("icon.png  {0} bytes (256x256)" -f (Get-Item $pngPath).Length)
Write-Output ("tray.png  {0} bytes (32x32, no tile)" -f (Get-Item $trayPath).Length)
