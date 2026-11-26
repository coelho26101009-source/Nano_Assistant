/**
 * Nubi, NANO's mascot: a small cloud with a face, as soft as the N symbol.
 *
 * Chosen by Simão on 2026-11-26 from three proposals. She shows how NANO is
 * doing, never something it is not doing: the mood comes from a real state
 * (the voice phase, a pending answer). One switch in Settings turns her off,
 * and every caller then shows the original N mark instead (see useMascot).
 *
 * Drawn inline, so the face can change without loading anything, and the
 * gradient id is unique per instance (several Nubis share a page).
 */
import React, { useId } from "react";

export type NubiMood = "calm" | "listening" | "thinking" | "happy";

const INK = "#12203A";
const CHEEK = "#8DB8FF";
const WAVE = "var(--information)";
const DOT = "var(--accent)";

export default function Nubi({
  size = 24,
  mood = "calm",
  className = "",
  title,
}: {
  size?: number;
  mood?: NubiMood;
  className?: string;
  title?: string;
}) {
  const fill = `nubi-${useId().replace(/[^A-Za-z0-9_-]/g, "")}`;
  // Below 40px there is no room for the waves and dots around her: a tighter
  // frame keeps the cloud itself the same visual size as the N it replaces.
  const small = size < 40;
  return (
    <svg
      className={`nubi nubi--${mood} ${className}`.trim()}
      viewBox={small ? "20 30 200 200" : "0 0 240 240"}
      width={size}
      height={size}
      role={title ? "img" : undefined}
      aria-label={title || undefined}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      <defs>
        <radialGradient id={fill} gradientUnits="userSpaceOnUse" cx="96" cy="86" r="170">
          <stop offset="0" stopColor="#FFFFFF" />
          <stop offset=".5" stopColor="#EDF4FF" />
          <stop offset="1" stopColor="#5B9BFF" />
        </radialGradient>
      </defs>
      <g className="nubi__body" fill={`url(#${fill})`}>
        <circle cx="84" cy="88" r="36" />
        <circle cx="148" cy="78" r="42" />
        <ellipse cx="120" cy="146" rx="94" ry="72" />
      </g>
      <g className="nubi__face" key={mood}>
        <ellipse cx="74" cy="164" rx="11" ry="6" fill={CHEEK} opacity=".6" />
        <ellipse cx="166" cy="164" rx="11" ry="6" fill={CHEEK} opacity=".6" />
        {face(mood)}
      </g>
      {!small && mood === "listening" && (
        <g className="nubi__waves" fill="none" stroke={WAVE} strokeWidth="5" strokeLinecap="round">
          <path d="M224 128 Q233 148 224 168" />
          <path d="M236 116 Q250 148 236 180" opacity=".6" />
          <path d="M16 128 Q7 148 16 168" />
          <path d="M4 116 Q-10 148 4 180" opacity=".6" />
        </g>
      )}
      {!small && mood === "thinking" && (
        <g className="nubi__dots" fill={DOT}>
          <circle cx="196" cy="44" r="6" />
          <circle cx="212" cy="28" r="8" opacity=".75" />
          <circle cx="228" cy="12" r="10" opacity=".5" />
        </g>
      )}
    </svg>
  );
}

function eye(x: number, y: number, rx: number, ry: number) {
  return (
    <g key={x}>
      <ellipse cx={x} cy={y} rx={rx} ry={ry} fill={INK} />
      <circle cx={x + 3} cy={y - 5} r="3.2" fill="#fff" />
    </g>
  );
}

function face(mood: NubiMood) {
  switch (mood) {
    case "listening":
      return (
        <>
          {eye(96, 144, 10, 15)}{eye(144, 144, 10, 15)}
          <ellipse cx="120" cy="170" rx="6" ry="5" fill={INK} />
        </>
      );
    case "thinking":
      return (
        <>
          {eye(100, 136, 8, 11)}{eye(148, 136, 8, 11)}
          <path d="M112 168 Q122 164 132 167" stroke={INK} strokeWidth="4" fill="none" strokeLinecap="round" />
        </>
      );
    case "happy":
      return (
        <>
          <g fill="none" stroke={INK} strokeWidth="5.5" strokeLinecap="round">
            <path d="M86 148 Q96 134 106 148" />
            <path d="M134 148 Q144 134 154 148" />
          </g>
          <path d="M104 162 Q120 184 136 162 Z" fill={INK} />
          <path d="M112 172 Q120 179 128 172 Q120 168 112 172Z" fill="#F08DA0" />
        </>
      );
    default:
      return (
        <>
          {eye(96, 144, 9, 13)}{eye(144, 144, 9, 13)}
          <path d="M110 162 Q120 170 130 162" stroke={INK} strokeWidth="4" fill="none" strokeLinecap="round" />
        </>
      );
  }
}
