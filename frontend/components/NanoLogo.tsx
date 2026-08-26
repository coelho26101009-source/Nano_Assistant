/**
 * The Nano brand marks.
 *
 * THESE ARE THE SUPPLIED ASSETS, NOT A REDRAWING. `public/branding/` holds the
 * artwork the identity was approved on: `nano-mark.png` (the white "N" inside
 * the red flame) and `nano-wordmark.png` (the "NANO" lettering). Nothing here
 * recolours, restyles or reinvents them.
 *
 * WHY THE `-alpha` FILES EXIST. The supplied PNGs are colour-type 2 — truecolour
 * with NO alpha channel — so the artwork is burned onto an opaque black square.
 * Dropped straight onto a glass panel or a rounded avatar that renders as a
 * black tile with hard corners, which is exactly what the redesign is trying to
 * get away from. `scripts/derive_brand_assets.py` recovers the alpha channel
 * (the artwork is effectively premultiplied against black, so alpha is
 * max(R,G,B) and the straight colour is rgb/alpha — an exact recovery, not a
 * guess), trims the empty margin and writes the `-alpha` variants. The
 * originals are left untouched as the masters.
 *
 * Sizing is always `object-fit: contain` inside a square (mark) or a
 * height-driven box (wordmark), so neither can be stretched out of proportion.
 */
import React from "react";

export const NANO_MARK_SRC = "/branding/nano-mark-alpha.png";
export const NANO_WORDMARK_SRC = "/branding/nano-wordmark-alpha.png";

/** The wordmark's own aspect ratio, so a height never distorts the width. */
const WORDMARK_RATIO = 720 / 239;

export type NanoLogoProps = {
  size?: number;
  /** Drop the red drop-shadow. Used at very small sizes and in dense rows. */
  bare?: boolean;
  /** Breathe the glow while Nano is working. Ignored under reduced motion. */
  active?: boolean;
  title?: string;
  className?: string;
};

/**
 * The flame mark on its own, with no container.
 *
 * Used wherever the app needs its symbol: the top bar, message avatars, the
 * conversation header, list rows and empty states.
 */
export default function NanoLogo({
  size = 28,
  bare = false,
  active = false,
  title,
  className = "",
}: NanoLogoProps) {
  const labelled = Boolean(title);
  const classes = [
    "nano-mark",
    bare ? "nano-mark--plain" : "",
    active ? "nano-mark--active" : "",
    className,
  ].filter(Boolean).join(" ");

  return (
    <img
      src={NANO_MARK_SRC}
      width={size}
      height={size}
      className={classes}
      alt={labelled ? title : ""}
      role={labelled ? "img" : "presentation"}
      aria-hidden={labelled ? undefined : true}
      draggable={false}
    />
  );
}

/**
 * The mark inside its disc.
 *
 * The disc is what makes the mark read as an identity at avatar sizes: a
 * faint radial of the flame's own red behind it, and a hairline that picks up
 * the same hue. `size` is the diameter; the mark is inset inside it.
 */
export function NanoAvatar({
  size = 34,
  active = false,
  title,
  className = "",
}: { size?: number; active?: boolean; title?: string; className?: string }) {
  return (
    <span
      className={`mark-disc ${className}`.trim()}
      style={{ width: size, height: size }}
      role={title ? "img" : "presentation"}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      <NanoLogo size={Math.round(size * 0.62)} active={active} />
    </span>
  );
}

/**
 * The "NANO" lettering.
 *
 * Reserved for real branding moments — the welcome state and the About panel —
 * rather than sprinkled through the chrome, where the mark alone is stronger.
 * Driven by height so the aspect ratio is fixed by construction.
 */
export function NanoWordmark({
  height = 34,
  title = "Nano",
  className = "",
}: { height?: number; title?: string; className?: string }) {
  return (
    <img
      src={NANO_WORDMARK_SRC}
      height={height}
      width={Math.round(height * WORDMARK_RATIO)}
      className={`nano-wordmark ${className}`.trim()}
      alt={title}
      draggable={false}
    />
  );
}

/**
 * Top-bar lockup: the mark beside the wordmark.
 *
 * The product name is the ARTWORK, not text set in the UI font. A "Nano" typed
 * in Inter beside a hand-drawn flame reads as two different brands; the
 * wordmark is the approved lettering and it is what belongs here. The version
 * stays as text — it is data, not identity — and is deliberately small.
 */
export function NanoLockup({
  version, size = 26,
}: { version?: string; size?: number }) {
  return (
    <span className="brand-lockup">
      <NanoLogo size={size} title="Nano" />
      <NanoWordmark height={Math.round(size * 0.68)} className="brand-lockup__word" />
      {version && <span className="brand-lockup__version">{version}</span>}
    </span>
  );
}
