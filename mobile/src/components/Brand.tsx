/** The supplied NANO artwork (copied unchanged from frontend/public/branding). */
const SYMBOL_RATIO = 360 / 289;
const WORDMARK_RATIO = 334 / 103;

export function NanoMark({ size = 24 }: { size?: number }) {
  return <img src="./branding/nano-symbol.png" width={Math.round(size * SYMBOL_RATIO)} height={size}
    className="nano-mark" alt="" aria-hidden="true" draggable={false} />;
}

export function NanoWordmark({ height = 20 }: { height?: number }) {
  return <img src="./branding/nano-wordmark-original.png" width={Math.round(height * WORDMARK_RATIO)} height={height}
    className="nano-wordmark" alt="NANO" draggable={false} />;
}
