/**
 * The Nano desktop title bar.
 *
 * The window is frameless, so this replaces the Windows caption. Two things
 * make that safe rather than annoying:
 *
 *  1. DRAG REGIONS ARE DELIBERATE. `-webkit-app-region: drag` is applied to the
 *     bar and then explicitly REMOVED from every interactive element. Miss one
 *     and that button becomes un-clickable: the drag region swallows the press
 *     before it reaches the handler. The `.titlebar__control` class carries the
 *     no-drag rule, so a new control cannot forget it.
 *
 *  2. IT ONLY EXISTS IN THE DESKTOP SHELL. Rendered from capability detection,
 *     so the same bundle opened in a browser during development simply has no
 *     title bar — no broken buttons, no crash.
 *
 * Close hides to the tray rather than quitting, because the global shortcut has
 * to keep working with the window gone. The tooltip says so, and Quit is a
 * separate explicit action in the tray menu.
 */
import React from "react";

import NanoLogo from "./NanoLogo";
import {
  hideWindow, minimizeWindow, toggleMaximizeWindow, useWindowState,
} from "../lib/desktop";

/* Windows caption glyphs, drawn rather than typed: the Segoe MDL2 font is not
   guaranteed, and a missing glyph renders as a tofu box in the corner of the
   application. Crisp at 10 px because every line sits on a whole pixel. */
const Minimize = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
    <rect x="0" y="4.5" width="10" height="1" fill="currentColor" />
  </svg>
);

const Maximize = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
    <rect x="0.5" y="0.5" width="9" height="9" stroke="currentColor" />
  </svg>
);

const Restore = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
    <rect x="0.5" y="2.5" width="7" height="7" stroke="currentColor" />
    <path d="M2.5 2.5V0.5h7v7h-2" stroke="currentColor" />
  </svg>
);

const Close = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
    <path d="M0.5 0.5l9 9M9.5 0.5l-9 9" stroke="currentColor" />
  </svg>
);

export default function TitleBar({ version }: { version?: string }) {
  const windowState = useWindowState();
  const maximized = windowState?.maximized ?? false;

  return (
    <div
      className="titlebar"
      data-focused={windowState?.focused !== false}
      // Double-clicking the caption maximises, as every Windows window does.
      onDoubleClick={toggleMaximizeWindow}
    >
      <div className="titlebar__brand">
        <NanoLogo size={17} bare />
        <span className="titlebar__name">Nano</span>
        {version && <span className="titlebar__version">{version}</span>}
      </div>

      {/* The draggable middle. Deliberately empty: a control here would have to
          opt out of dragging, and an accidental drag on a control is worse than
          a slightly emptier bar. */}
      <div className="titlebar__drag" />

      <div className="titlebar__controls">
        <button
          type="button" className="titlebar__control"
          onClick={minimizeWindow}
          aria-label="Minimizar" title="Minimizar"
        >
          <Minimize />
        </button>
        <button
          type="button" className="titlebar__control"
          onClick={toggleMaximizeWindow}
          aria-label={maximized ? "Restaurar" : "Maximizar"}
          title={maximized ? "Restaurar" : "Maximizar"}
        >
          {maximized ? <Restore /> : <Maximize />}
        </button>
        <button
          type="button" className="titlebar__control titlebar__control--close"
          onClick={hideWindow}
          aria-label="Fechar para o tabuleiro"
          // Says exactly what happens. A close button that does not close is
          // only acceptable if it never surprises anyone.
          title="Fechar para o tabuleiro — o Nano continua a correr"
        >
          <Close />
        </button>
      </div>
    </div>
  );
}
