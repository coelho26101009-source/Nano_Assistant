/**
 * The Nano mark.
 *
 * An original identity: a geometric "N" cut from a hexagonal core, wrapped by a
 * broken orbital ring. The hexagon reads as something engineered and small-scale
 * (nano), the ring as something in motion (an assistant that is working), and
 * the gap in the ring keeps it from looking like a generic loading spinner.
 *
 * Drawn from primitives rather than a traced illustration so it stays crisp at
 * 16 px, where fine detail would turn to mush. Everything scales from the
 * `size` prop and inherits colour from the tokens, so one component serves the
 * sidebar, the avatar and the favicon.
 */
import React from "react";

export type NanoLogoProps = {
  size?: number;
  /** Hide the orbit ring — used at very small sizes and inside dense rows. */
  bare?: boolean;
  /** Animate the orbit. Ignored when the user prefers reduced motion. */
  active?: boolean;
  title?: string;
  className?: string;
};

export default function NanoLogo({
  size = 28,
  bare = false,
  active = false,
  title,
  className = "",
}: NanoLogoProps) {
  const gradientId = React.useId();
  const labelled = Boolean(title);

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={`nano-logo${active ? " nano-logo--active" : ""} ${className}`.trim()}
      role={labelled ? "img" : "presentation"}
      aria-label={title}
      aria-hidden={labelled ? undefined : true}
      focusable="false"
    >
      <defs>
        <linearGradient id={`${gradientId}-core`} x1="8" y1="6" x2="40" y2="42" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="var(--accent)" />
          <stop offset="100%" stopColor="var(--accent-2)" />
        </linearGradient>
      </defs>

      {/* Orbit: two arcs with deliberate gaps, so it never reads as a spinner. */}
      {!bare && (
        <g className="nano-logo__orbit" stroke="var(--accent)" strokeWidth="1.6" strokeLinecap="round" opacity="0.55">
          <path d="M24 3.5A20.5 20.5 0 0 1 44.5 24" />
          <path d="M24 44.5A20.5 20.5 0 0 1 3.5 24" />
        </g>
      )}

      {/* Hexagonal core. */}
      <path
        d="M24 8.5 38.5 16.75v14.5L24 39.5 9.5 31.25v-14.5L24 8.5Z"
        fill={`url(#${gradientId}-core)`}
        fillOpacity="0.14"
        stroke={`url(#${gradientId}-core)`}
        strokeWidth="1.8"
        strokeLinejoin="round"
      />

      {/* The N: two uprights and the diagonal, as one continuous stroke path. */}
      <path
        d="M18.5 31V17l11 14V17"
        stroke={`url(#${gradientId}-core)`}
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />

      {/* Two nodes on the diagonal: the "circuit" read, dropped when bare. */}
      {!bare && (
        <>
          <circle cx="18.5" cy="17" r="1.9" fill="var(--accent)" />
          <circle cx="29.5" cy="31" r="1.9" fill="var(--accent-2)" />
        </>
      )}
    </svg>
  );
}

/** Sidebar lockup: mark plus wordmark and version. */
export function NanoWordmark({ version, collapsed }: { version?: string; collapsed?: boolean }) {
  return (
    <div className="brand-lockup">
      <NanoLogo size={collapsed ? 24 : 30} title="Nano" />
      {!collapsed && (
        <span className="brand-lockup__text">
          <span className="brand-lockup__name">Nano</span>
          {version && <span className="brand-lockup__version">{version}</span>}
        </span>
      )}
    </div>
  );
}
