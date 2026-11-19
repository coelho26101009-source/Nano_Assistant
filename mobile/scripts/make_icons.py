"""Build the Android launcher icons, splash screens and notification icon.

Everything is made from the supplied artwork, unchanged
(frontend/public/branding/nano-symbol-original.png): the N on NANO's own
near-black (#101010, the PC sidebar). Run from mobile/: python3 scripts/make_icons.py
Also writes store/icon-512.png and store/feature-graphic.png for the Play listing.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent.parent
SYMBOL = HERE.parent / "frontend" / "public" / "branding" / "nano-symbol-original.png"
WORDMARK = HERE.parent / "frontend" / "public" / "branding" / "nano-wordmark-original.png"
RES = HERE / "android" / "app" / "src" / "main" / "res"
STORE = HERE / "store"
BG = (16, 16, 16, 255)
SPLASH_BG = (24, 24, 24, 255)

DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}


def symbol() -> Image.Image:
    image = Image.open(SYMBOL).convert("RGBA")
    return image.crop(image.getbbox())


def placed(canvas: int, fraction: float, background=None) -> Image.Image:
    """The symbol centred on a square canvas, its longer side = fraction of it."""
    mark = symbol()
    scale = canvas * fraction / max(mark.size)
    mark = mark.resize((max(1, round(mark.width * scale)), max(1, round(mark.height * scale))), Image.LANCZOS)
    out = Image.new("RGBA", (canvas, canvas), background or (0, 0, 0, 0))
    out.alpha_composite(mark, ((canvas - mark.width) // 2, (canvas - mark.height) // 2))
    return out


def rounded(image: Image.Image, radius: float) -> Image.Image:
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
    out = image.copy()
    out.putalpha(mask)
    return out


def circle(image: Image.Image) -> Image.Image:
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, image.width - 1, image.height - 1), fill=255)
    out = image.copy()
    out.putalpha(mask)
    return out


def silhouette(size: int) -> Image.Image:
    """Status-bar icon: Android shows only the alpha, in white."""
    mark = placed(size, 0.86)
    alpha = mark.getchannel("A").point(lambda a: 255 if a > 90 else 0)
    out = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    out.putalpha(alpha)
    return out


def main() -> None:
    for name, factor in DENSITIES.items():
        folder = RES / f"mipmap-{name}"
        folder.mkdir(parents=True, exist_ok=True)
        legacy = round(48 * factor)
        rounded(placed(legacy, 0.62, BG), legacy * 0.18).save(folder / "ic_launcher.png")
        circle(placed(legacy, 0.58, BG)).save(folder / "ic_launcher_round.png")
        # Adaptive foreground: 108dp canvas, art inside the 66dp safe zone.
        placed(round(108 * factor), 0.44).save(folder / "ic_launcher_foreground.png")
        drawable = RES / f"drawable-{name}"
        drawable.mkdir(parents=True, exist_ok=True)
        silhouette(round(24 * factor)).save(drawable / "ic_stat_nano.png")
    # Splash screens (Capacitor's legacy splash drawables, and the default one).
    for folder in sorted(RES.glob("drawable*")):
        target = folder / "splash.png"
        if target.exists():
            width, height = Image.open(target).size
            splash = Image.new("RGBA", (width, height), SPLASH_BG)
            mark = placed(round(min(width, height) * 0.5), 0.5)
            splash.alpha_composite(mark, ((width - mark.width) // 2, (height - mark.height) // 2))
            splash.convert("RGB").save(target)
    STORE.mkdir(exist_ok=True)
    rounded(placed(512, 0.62, BG), 0).convert("RGB").save(STORE / "icon-512.png")
    feature = Image.new("RGBA", (1024, 500), SPLASH_BG)
    mark = placed(300, 0.9)
    feature.alpha_composite(mark, (150, 100))
    word = Image.open(WORDMARK).convert("RGBA")
    word = word.resize((round(word.width * 1.4), round(word.height * 1.4)), Image.LANCZOS)
    feature.alpha_composite(word, (500, (500 - word.height) // 2))
    feature.convert("RGB").save(STORE / "feature-graphic.png")
    print("icons written")


if __name__ == "__main__":
    main()
