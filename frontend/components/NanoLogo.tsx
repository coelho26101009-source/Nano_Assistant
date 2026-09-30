/** The supplied NANO artwork, resized from the unchanged master without redrawing it. */
import React from "react";

import { BRAND_NAME } from "../lib/brand";

const SYMBOL = "/branding/nano-symbol.png";
const WORDMARK = "/branding/nano-wordmark-original.png";
const SYMBOL_RATIO = 360 / 289;
const WORDMARK_RATIO = 334 / 103;

export default function NanoLogo({
  size = 24,
  title,
  className = "",
}: {
  size?: number;
  title?: string;
  className?: string;
}) {
  return (
    <img
      src={SYMBOL}
      width={Math.round(size * SYMBOL_RATIO)}
      height={size}
      className={`nano-mark ${className}`.trim()}
      alt={title || ""}
      aria-hidden={title ? undefined : true}
      draggable={false}
    />
  );
}

export function NanoWordmark({
  height = 18,
  title = BRAND_NAME,
  className = "",
}: { height?: number; title?: string; className?: string }) {
  return (
    <img
      src={WORDMARK}
      width={Math.round(height * WORDMARK_RATIO)}
      height={height}
      className={`nano-wordmark ${className}`.trim()}
      alt={title}
      draggable={false}
    />
  );
}

export function NanoLockup({ size = 24 }: { size?: number }) {
  return (
    <span className="brand-lockup">
      <NanoLogo size={size} />
      <NanoWordmark height={Math.round(size * 0.8)} />
    </span>
  );
}
